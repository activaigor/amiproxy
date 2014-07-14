import ConfigParser

class Connections(object):

	config = ConfigParser.ConfigParser()
	ami_host = None
	ami_port = None
	ami_user = None
	ami_pass = None

	def __init__(self, name):
		name = "ami_{0}".format(name)
		self.config.read("/home/activa/github/amiproxy/settings.ini")
		self.ami_host = self.config.get(name , "host")
		self.ami_port = self.config.get(name , "port")
		self.ami_user = self.config.get(name , "user")
		self.ami_pass = self.config.get(name , "pass")
