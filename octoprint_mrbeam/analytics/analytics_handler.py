import json
import time
import os.path
from octoprint.events import Events as OctoPrintEvents
from octoprint_mrbeam.mrb_logger import mrb_logger
from octoprint_mrbeam.mrbeam_events import MrBeamEvents
from value_collector import ValueCollector

# singleton
_instance = None
def analyticsHandler(plugin):
	global _instance
	if _instance is None:
		_instance = AnalyticsHandler(plugin._event_bus, plugin._settings)
	return _instance

# TODO CLEM replace all dict-keys with CONSTANT_VALUES


def existing_analyticsHandler():
	"""
	Returns AnalyticsHandler instance only if it's already initialized. None otherwise
	:return: None or AnalyticsHandler instance
	"""
	global _instance
	return _instance


class AnalyticsHandler(object):
	def __init__(self, event_bus, settings):
		self._event_bus = event_bus
		self._settings = settings

		self._current_job_id = None
		self._isJobPaused = False
		self._isCoolingPaused = False
		self._isJobDone = False

		self._current_dust_collector = None
		self._current_cam_session_id = None
		self._current_intensity_collector = None
		self._current_lasertemp_collector = None

		self._jobevent_log_version = 2
		self._deviceinfo_log_version = 2
		self._dust_log_version = 2
		self._cam_event_log_version = 2

		self._logger = mrb_logger("octoprint.plugins.mrbeam.analyticshandler")

		analyticsfolder = os.path.join(self._settings.getBaseFolder("base"), self._settings.get(["analyticsfolder"]))
		if not os.path.isdir(analyticsfolder):
			os.makedirs(analyticsfolder)

		self._jsonfile = os.path.join(analyticsfolder, "analytics_log.json")
		if not os.path.isfile(self._jsonfile):
			self._init_jsonfile()

		self._subscribe()

	def _subscribe(self):
		self._event_bus.subscribe(OctoPrintEvents.PRINT_STARTED, self._event_print_started)
		self._event_bus.subscribe(OctoPrintEvents.PRINT_PAUSED, self._event_print_paused)
		self._event_bus.subscribe(OctoPrintEvents.PRINT_RESUMED, self._event_print_resumed)
		self._event_bus.subscribe(OctoPrintEvents.PRINT_DONE, self._event_print_done)
		self._event_bus.subscribe(OctoPrintEvents.PRINT_FAILED, self._event_print_failed)
		self._event_bus.subscribe(OctoPrintEvents.PRINT_CANCELLED, self._event_print_cancelled)
		self._event_bus.subscribe(MrBeamEvents.PRINT_PROGRESS, self._event_print_progress)
		self._event_bus.subscribe(MrBeamEvents.LASER_COOLING_PAUSE, self._event_laser_cooling_pause)
		self._event_bus.subscribe(MrBeamEvents.LASER_COOLING_RESUME, self._event_laser_cooling_resume)
		self._event_bus.subscribe(MrBeamEvents.LASER_JOB_DONE, self._event_laser_job_done)

	def _init_jsonfile(self):
		open(self._jsonfile, 'w+').close()
		data = {
			'hostname': self._getHostName(),
			'laser_head_version': self._getLaserHeadVersion()
		}
		self.write_event('deviceinfo','init_json', self._deviceinfo_log_version,payload=data)

	@staticmethod
	def _getLaserHeadVersion():
		# TODO CLEM ADD laser_head_id for real
		laser_head_version = 1
		return laser_head_version

	@staticmethod
	def _getSerialNumber():
		return _mrbeam_plugin_implementation.getMrBeamSerial()

	@staticmethod
	def _getHostName():
		return _mrbeam_plugin_implementation.getHostname()

	def _event_print_started(self, event, payload):
		filename = os.path.basename(payload['file'])
		self._current_job_id = '{}_{}'.format(filename,time.time())
		self._init_collectors()
		self._isJobPaused = False
		self._isCoolingPaused = False
		self._isJobDone = False
		self._write_jobevent('print_started', {'filename': filename})

	def _init_collectors(self):
		self._current_dust_collector = ValueCollector()
		self._current_intensity_collector = ValueCollector()
		self._current_lasertemp_collector = ValueCollector()

	def _event_print_paused(self, event, payload):
		if not self._isJobPaused: #prevent multiple printPaused events per Job
			self._write_jobevent('print_paused')
			self._isJobPaused = True

	def _event_print_resumed(self, event, payload):
		if self._isJobPaused:  #prevent multiple printResume events per Job
			self._write_jobevent('print_resumed')
			self._isJobPaused = False

	def _event_print_done(self, event, payload):
		if not self._isJobDone:
			self._isJobDone = True #prevent two jobDone events per Job
			self._write_jobevent('print_done')
			self._write_collectors()

	def _write_collectors(self):
		self._write_jobevent('dust_summary',payload=self._current_dust_collector.getSummary())
		self._write_jobevent('intensity_summary',payload=self._current_intensity_collector.getSummary())
		self._write_jobevent('lasertemp_summary', payload=self._current_lasertemp_collector.getSummary())

	def _cleanup(self,successfull):
		# TODO check if resetting job_id etc to None makes sense
		self._current_job_id = None
		self._current_dust_collector = None
		self._current_intensity_collector = None
		self._current_lasertemp_collector = None

	def _event_laser_job_done(self, event, payload):
		self._write_jobevent('laserjob_done')
		self._cleanup(successfull=True)

	def _event_print_failed(self, event, payload):
		self._write_jobevent('print_failed')
		self._write_collectors()
		self._cleanup(successfull=False)


	def _event_print_cancelled(self, event, payload):
		self._write_jobevent('print_cancelled')
		self._write_collectors()
		self._cleanup(successfull=False)


	def _event_print_progress(self, event, payload):
		self._write_jobevent('print_progress', {'progress':payload})

	def _event_laser_cooling_pause(self, event, payload):
		if not self._isCoolingPaused:
			self._write_jobevent('laser_cooling_start')
			self._isCoolingPaused = True

	def _event_laser_cooling_resume(self, event, payload):
		if self._isCoolingPaused:
			self._write_jobevent('laser_cooling_done')
			self._isCoolingPaused = False

	def write_conversion_details(self,details):
		if 'engrave' in details and details['engrave'] == True and 'raster' in details:
			eventname = 'conv_engrave'
			data = {
				'svgDPI': details['svgDPI']
			}
			data.update(details['raster'])
			self._write_jobevent(eventname,payload=data)

		if 'vector' in details and details['vector'] != []:
			eventname = 'conv_cut'
			for color_settings in details['vector']:
				data = {
					'svgDPI': details['svgDPI']
				}
				data.update(color_settings)
				self._write_jobevent(eventname,payload=data)

	def _write_jobevent(self,event,payload=None):
		#TODO add data validation/preparation here
		data = dict(job_id = self._current_job_id)

		if event in ('lasertemp_summary','intensity_summary'):
			data['laserhead_v'] = self._getLaserHeadVersion()

		if payload is not None:
			data['data'] = payload

		_jobevent_type = 'jobevent'
		self.write_event(_jobevent_type, event, self._jobevent_log_version, payload=data)

	def update_cam_session_id(self, lid_state):
		if lid_state == 'lid_opened':
			self._current_cam_session_id = 'cam_{}_{}'.format(self._getSerialNumber(),time.time())
		else:
			self._current_cam_session_id = None

	def _write_cam_event(self, event, payload=None):
		#TODO add data validation/preparation here
		data = dict(cam_session = self._current_cam_session_id)

		if payload is not None:
			data['data'] = payload

		self.write_event('cam', event, self._cam_event_log_version, payload=data)

	def write_event(self, typename, eventname, version, payload=None):
		data = {
			'serialnumber': self._getSerialNumber(),
			'type': typename,
			'log_version': version,
			'eventname': eventname,
			'timestamp': time.time()
		}
		if payload is not None:
			data.update(payload)
		self._append_data_to_file(data)

	def add_dust_value(self, dust_value):
		"""
		:param dust_value:
		:return:
		"""
		if self._current_dust_collector is not None:
			self._current_dust_collector.addValue(dust_value)

	def add_laser_temp_value(self,laser_temp):
		"""
		:param laser_temp:
		:return:
		"""
		if self._current_lasertemp_collector is not None:
			self._current_lasertemp_collector.addValue(laser_temp)

	def add_laser_intensity_value(self, laser_intensity):
		"""
		Laser intensity.
		:param laser_intensity: 0-255. Zero means laser is off
		"""
		if self._current_intensity_collector is not None:
			self._current_intensity_collector.addValue(laser_intensity)

	def write_dust_log(self, values):
		data = {
			'dust_start':values['dust_start'],
			'dust_end': values['dust_end'],
			'dust_start_ts': values['dust_start_ts'],
			'dust_end_ts': values['dust_end_ts']
		}
		self._write_jobevent('final_dust',payload=data)

	def _append_data_to_file(self, data):
		with open(self._jsonfile, 'a') as f:
			json.dump(data, f)
			f.write('\n')
