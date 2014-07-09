#!/usr/bin/python

from amiproxy import AMIserver
import os,traceback

amiserver = AMIserver()
try:
	amiserver.run()
except Exception:
	traceback.print_exc(file=open("/usr/local/bin/amiproxy/error.log","a"))
