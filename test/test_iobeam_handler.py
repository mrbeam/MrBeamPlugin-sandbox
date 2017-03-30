import unittest
import mock
import ddt
import logging
import socket
import os
import sys
import threading
import time
from octoprint_mrbeam.iobeam_handler import ioBeamHandler, IoBeamEvents


# hint: to get the output with timestamps:
# > nosetests --with-doctest --logging-format="%(asctime)s %(name)s: %(levelname)s: %(message)s" --debug=octoprint.plugins.mrbeam,test.mrbeam.serverthread

@ddt.ddt
class IoBeamHandlerTestCase(unittest.TestCase):

	def setUp(self):
		self._logger = logging.getLogger("test." + self.__module__ + "." + self.__class__.__name__)
		self._logger.debug("setUp() START")

		self.testThreadServer = ServerThread()
		self.testThreadServer.start()
		time.sleep(.01)

		self.mock = mock.MagicMock(name="EventManagerOctMock")
		self.ioBeamHandler = ioBeamHandler(self.mock)
		time.sleep(.01)

		self.mock.reset_mock()
		self._logger.debug("setUp() DONE --------------------")

	def tearDown(self):
		self._logger.debug("tearDown() START ----------------")
		self.ioBeamHandler.shutdown()
		self.testThreadServer.join()
		time.sleep(.01)
		self._logger.debug("tearDown() DONE")


	@ddt.data(
		# ( [list of mesages to send], [ list of tuples (event, payload)] )
		(["onebtn:pr"], [(IoBeamEvents.ONEBUTTON_PRESSED, None)]),
		(["onebtn:dn:0.8"], [(IoBeamEvents.ONEBUTTON_DOWN, 0.8)]),
		(["onebtn:rl:1.2"], [(IoBeamEvents.ONEBUTTON_RELEASED, 1.2)]),
		(["onebtn:pr", "onebtn:dn:0.2","onebtn:dn:0.5", "onebtn:rl:1.0"],
		 	[(IoBeamEvents.ONEBUTTON_PRESSED, None), (IoBeamEvents.ONEBUTTON_DOWN, 0.2),
			 (IoBeamEvents.ONEBUTTON_DOWN, 0.5),(IoBeamEvents.ONEBUTTON_RELEASED, 1.0)]),
	)
	@ddt.unpack
	def test_onebutton(self, messages, expectations):
		self._logger.debug("test_onebutton() messages: %s, expectations: %s", messages, expectations)
		self._send_messages_and_evaluate(messages, expectations)


	@ddt.data(
		# ( [list of mesages to send], [ list of tuples (event, payload)] )
		(["intlk:0:op"], [(IoBeamEvents.INTERLOCK_OPEN, None)], False),
		(["intlk:2:cl"], [], True),
		(["intlk:0:op", "intlk:2:cl", "intlk:1:cl"], [(IoBeamEvents.INTERLOCK_OPEN, None)], False),
		(["intlk:0:op", "intlk:2:cl", "intlk:0:cl"], [(IoBeamEvents.INTERLOCK_OPEN, None), (IoBeamEvents.INTERLOCK_CLOSED, None)], True),
	)
	@ddt.unpack
	def test_interlocks(self, messages, expectations, expectation_closed_in_the_end):
		self._logger.debug("test_interlocks() messages: %s, expectations: %s, expectation_closed_in_the_end: %s",
						   messages, expectations, expectation_closed_in_the_end)
		self._send_messages_and_evaluate(messages, expectations)
		self._logger.debug("test_interlocks() ANDYTEST is_interlock_closed: %s", self.ioBeamHandler.is_interlock_closed())
		self.assertEqual(self.ioBeamHandler.is_interlock_closed(), expectation_closed_in_the_end,
						 "is_interlock_closed() did not return %s in the end as expected." % expectation_closed_in_the_end)


	def test_reconnect_on_error(self):
		for i in range(0, 10):
			self.testThreadServer.sendCommand("some BS %s" % i)
			time.sleep(0.01)
		time.sleep(1.1) # eventBusMrb sleeps for 1 sec after closing connection to avoid busy loops

		expected = [mock.call.fire(IoBeamEvents.DISCONNECT, None),
					mock.call.fire(IoBeamEvents.CONNECT, None)]
		assert (self.mock.mock_calls == expected), \
			("Events fired by IoBeamHandler.\n"
			 "Expected calls: %s\n"
			 "Actual calls:   %s" % (expected, self.mock.mock_calls))


	def _send_messages_and_evaluate(self, messages, expectations):
		for msg in messages:
			self.testThreadServer.sendCommand(msg)
			time.sleep(.01)

		expected = []
		for exp in expectations:
			expected.append(mock.call.fire(exp[0], exp[1]))
		assert (self.mock.mock_calls == expected), \
			("Events fired by IoBeamHandler.\n"
			 "Expected calls: %s\n"
			 "Actual calls:   %s" % (expected, self.mock.mock_calls))


class ServerThread(threading.Thread):
	SOCKET_FILE = "/tmp/mrbeam_iobeam.sock"
	# SOCKET_FILE = "/var/run/mrbeam_iobeam.sock"
	SOCKET_NEWLINE = "\n"

	def __init__(self):
		super(ServerThread, self).__init__()
		self.daemon = True
		self.alive = threading.Event()
		self.alive.set()
		self.conn = None

		self._logger = logging.getLogger("test." + self.__module__ + "." + self.__class__.__name__)
		self._logger.info( self.__class__.__name__ + " initialized")

	def run(self):
		self._logger.info("Worker thread started.")
		try:
			os.remove(self.SOCKET_FILE)
		except OSError:
			pass

		self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		self.socket.bind(self.SOCKET_FILE)

		while self.alive.isSet():
			self._logger.info("Listening for incoming connections on " + self.SOCKET_FILE)
			self.socket.setblocking(1)
			self.socket.settimeout(3)
			self.socket.listen(0)
			self.conn, addr = self.socket.accept()
			self._logger.info("Client connected.")

			while self.alive.isSet():
				self.socket.settimeout(3)
				try:
					data = self.conn.recv(1024)
					if not data: break
				except Exception as e:
					if str(e) == "[Errno 35] Resource temporarily unavailable":
						pass
						# self._logger.warn(str(e))
					else:
						self._logger.warn("Exception while waiting: %s - %s", str(e))
						break

			self._logger.info ("  Disconnecting client...")
			self.conn.close()
			self.conn = None

		self._logger.info("Worker thread stopped.")

	def sendCommand(self, command):
		self._send(command)

	def _send(self, payload):
		if self.conn is not None:
			self._logger.info("  --> " + payload)
			self.conn.send(payload + self.SOCKET_NEWLINE)
		else:
			raise Exception("No Connection, not able to write on socket")

	def join(self, timeout=None):
		self.alive.clear()
		threading.Thread.join(self, timeout)
