import time
from mysqlfetch import MysqlFetch
import ConfigParser
import pystrix
import re

class SystemCall():
	actionid = None
	destination = None
	primal_destination = None
	caller = None
	primal_caller = None
	caller_is_channel = False
	channel = None
	channel_name = None
	context = None
	ttl = None
	config = ConfigParser.ConfigParser()
	asterisk_sql = None
	amicore = None
	timestamp = None

	context = "preprocessing"

	def __init__(self,caller,destination,ttl=3):
		self.config.read("/usr/local/bin/amiproxy/settings.ini")
		self.primal_caller = caller
		self.caller = caller
		self._caller_is_channel()
		self.primal_destination = destination
		self.destination = destination
		self.ttl = ttl
		self.context = "preprocessing"
		self.amicore = pystrix.ami.core
		self.timestamp = int(round(time.time()))

	def _caller_is_channel(self):
		external = re.search('^SIP/.+/[^/]+', self.caller)
		internal = re.search('^SIP/[0-9]{4}$', self.caller)
		self.caller_is_channel = True if internal or external else False


	def asterisk_sql_connect(self):
		if (self.asterisk_sql == None):
			ast_host = self.config.get("asterisk_sql" , "host")
			ast_user = self.config.get("asterisk_sql" , "user")
			ast_pass = self.config.get("asterisk_sql" , "pass")
			ast_db = self.config.get("asterisk_sql" , "db")
			self.asterisk_sql = MysqlFetch(host = ast_host , user = ast_user , passwd = ast_pass , db = ast_db)
			return 1

	def numb_check(self):
		if not self.caller_is_channel:
			self.caller = self._normalize_num(self.caller)
		self.destination = self._normalize_num(self.destination)
		return True if (self.caller != None and self.destination != None) else False

	def _normalize_num(self,num):
		if (num[0] == '+' and len(num) == 13):
			num.replace('+','00',1)
		if (len(num) == 9):
			num = '0' + num
		if (num[0:4] == '0038' and len(num) == 14):
			num = num.replace('0038','',1)
		if (num[0:3] == '380' and len(num) == 12):
			num = num.replace('38','',1)
		return num if (len(num) == 10 or len(num) == 4) else None

	def busy_channel(self):
		if self.channel_name != None and self.channel_name != "vegatele" and self.channel_name != "life-pri" and self.channel_name != "internal":
			self.asterisk_sql_connect()
			data = self.asterisk_sql.query("UPDATE gsm_channels SET status = 'busy' WHERE name = '{name}'".format(name = self.channel_name))
			return True
		else:
			return False

	def _get_gsm_channel(self,operator):
		self.asterisk_sql_connect()
		assign = "recall"
		data = self.asterisk_sql.query("SELECT * FROM gsm_channels WHERE status = 'free' and channel = '{operator}' and assign = '{assign}' ORDER BY calls".format(operator = operator, assign = assign))
		if (len(data) > 0):
			self.channel_name = data[0]["name"]
			return "SIP/" + str(data[0]["name"]) + "/" + str(data[0]["prefix"])
		else:
			return False

	def get_channel(self):
		if not self.caller_is_channel:
			if len(re.findall("^09[95]",self.caller)) == 1 or self.caller[:3] == "066" or self.caller[:3] == "050":
				self.channel = self._get_gsm_channel("mtc")
				return self.channel
			elif len(re.findall("^09[6-8]",self.caller)) == 1 or self.caller[:3] == "067" or self.caller[:3] == "068":
				self.channel = self._get_gsm_channel("kyivstar")
				return self.channel
			elif len(re.findall("^0[69]3",self.caller)) == 1:
				self.channel_name = "life-pri"
				self.channel = "SIP/life-pri/"
				#self.channel_name = "vegatele"
				#self.channel = "SIP/vegatele/"
				return self.channel
			elif self.caller[:3] == "044":
				self.channel_name = "vegatele"
				self.channel = "SIP/vegatele/"
				return self.channel
			elif len(self.caller) == 4 and self.caller[:1] == "2":
				self.channel_name = "internal"
				self.channel = "SIP/"
				return self.channel
			else:
				self.channel_name = None
				self.channel = None
				return self.channel
		else:
			external = re.search('^SIP/(.+)/[^/]+', self.caller)
			if external:
				self.channel_name = external.group(1)
			else:
				self.channel_name = "internal"
			self.channel = self.channel_name
			return self.channel

	def ttl_deduct(self):
		self.ttl = self.ttl - 1
		return self.ttl

	def later(self):
		self.timestamp = int(round(time.time()))

	def originate(self):
		if not self.caller_is_channel:
			return self.amicore.Originate_Context(str(self.channel) + str(self.caller),self.context,self.destination,"1",callerid=self.caller)
		else:
			return self.amicore.Originate_Context(str(self.caller),self.context,self.destination,"1")

