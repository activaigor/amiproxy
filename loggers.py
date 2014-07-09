import logging
import ConfigParser
import os

class Logger(object):

	logger = None
	levels = None
	config = None

	def __init__(self,classname):
		curdir = str(os.path.dirname(os.path.realpath(__file__)))
		self.config = ConfigParser.ConfigParser()
		self.config.read(curdir + "/settings.ini")
		self.logger = logging.getLogger(classname)
		self.levels = self.config.get("log", classname + "-level").split(",")
		log_path = self.config.get("log", classname)
		self.logSet(log_path)

	def logSet(self,pathInfo):
		hdlr = logging.FileHandler(pathInfo)
		formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
		hdlr.setFormatter(formatter)
		self.logger.addHandler(hdlr)
		self.logger.setLevel(logging.DEBUG)

	def debug(self,log):
		if "debug" in self.levels:
			return self.logger.debug(log)

	def info(self,log):
		if "info" in self.levels:
			return self.logger.info(log)

	def warning(self,log):
		if "warning" in self.levels:
			return self.logger.warning(log)

