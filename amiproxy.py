from SimpleXMLRPCServer import SimpleXMLRPCServer
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
from SocketServer import ThreadingMixIn
import socket
import sys
import pystrix
import Queue
import json
from threading import Thread
import logging
from systemcall import SystemCall
import time
import re

BIND_PORT=8123
BIND_HOST="0.0.0.0"
_HOST = '194.50.85.100'
_USERNAME = 'admin'
_PASSWORD = 'FcnthbcrFlvby'

#Originate result constants
ORIGINATE_RESULT_REJECT = 1 #Remote hangup
ORIGINATE_RESULT_RING_LOCAL = 2
ORIGINATE_RESULT_RING_REMOTE = 3
ORIGINATE_RESULT_ANSWERED = 4
ORIGINATE_RESULT_BUSY = 5
ORIGINATE_RESULT_CONGESTION = 8
ORIGINATE_RESULT_INCOMPLETE = 30 #Unable to resolve

class MyXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer): pass

class AMICore(object):
    """
    The class that will be used to hold the logic for this AMI session. You could also just work
    with the `Manager` object directly, but this is probably a better approach for most
    general-purpose applications.
    """
    _manager = None #The AMI conduit for communicating with the local Asterisk server
    _kill_flag = False #True when the core has shut down of its own accord
    queue = {}
    calls_queue = {}
    originate_response = {}
    registered_events = None
    calls_processor = None
    check_connection = None
    action_lookup = None
    logger = logging.getLogger('GSMstat')

    def __init__(self):
        self._log_set("/usr/local/bin/amiproxy/debug.log" , "info")
        self.queue["share"] = MSGQueue("share")
        self.not_my_actions = Queue.Queue()
        self.calls_queue["later"] = Queue.Queue()
        self.calls_queue["retry"] = Queue.Queue()
        self.calls_queue["retry_later"] = Queue.Queue()
        self.registered_events = []
        self._manager = pystrix.ami.Manager()
        self._register_callbacks()
        self.calls_processor = Thread(target = self._calls_processor)
        self.calls_processor.start()
        self.check_connection = Thread(target = self._check_connection)
        self.check_connection.start()
        self.action_lookup = False

    def _check_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = s.connect_ex((_HOST, 5038))
        if result == 0:
            s.close()
            return True
        else:
            s.close()
            return False

    def _connect(self):
        while not self._check_socket():
            time.sleep(2)
        try:
            self._manager.connect(_HOST)
            challenge_response = self._manager.send_action(pystrix.ami.core.Challenge())
            if challenge_response and challenge_response.success:
                action = pystrix.ami.core.Login(
                    _USERNAME, _PASSWORD, challenge=challenge_response.result['Challenge']
                )
                self._manager.send_action(action)
            else:
                self._kill_flag = True
                raise ConnectionError(
                    "Asterisk did not provide an MD5 challenge token" +
                    (challenge_response is None and ': timed out' or '')
                )
        except pystrix.ami.ManagerSocketError as e:
            self._kill_flag = True
            raise ConnectionError("Unable to connect to Asterisk server: %(error)s" % {
                'error': str(e),
            })
        except pystrix.ami.core.ManagerAuthError as reason:
            self._kill_flag = True
            raise ConnectionError("Unable to authenticate to Asterisk server: %(reason)s" % {
                'reason': reason,
            })
        except pystrix.ami.ManagerError as reason:
            self._kill_flag = True
            raise ConnectionError("An unexpected Asterisk error occurred: %(reason)s" % {
                'reason': reason,
            })
        else:
            self._manager.monitor_connection()

    def _register_callbacks(self):
        self._manager.register_callback('Shutdown', self._handle_shutdown)
        self.catch_event("OriginateResponse","calls_processor")
        self.catch_event("Status","action_responses")

    def _check_connection(self):
        while True:
            time.sleep(1)
            if not self._manager.is_connected():
                self._connect()

    def _calls_processor(self):
        while True:
            time.sleep(1)
            if self._manager.is_connected():
                # originate response processor
                # if success - forget about this. else - put to queue 'later'
                while (self.queue["calls_processor"].qsize() != 0):
                    try:
                        message = json.loads(self.get_messages_nowait("calls_processor"))
                        if str(message["ActionID"]) in self.originate_response:
                            self.logger.info(message["Channel"] + " processed")
                            if (int(message["Reason"]) == ORIGINATE_RESULT_ANSWERED):
                                self.logger.info(message["Channel"] + " has answered the call")
                                call = self.originate_response[message["ActionID"]]
                                self.originate_response.pop(message["ActionID"])
                                del call
                            elif (int(message["Reason"]) == ORIGINATE_RESULT_CONGESTION):
                                self.logger.info(message["Channel"] + " has congestion! Retrying...")
                                call = self.originate_response[message["ActionID"]]
                                self.calls_queue["retry"].put_nowait(call)
                                self.originate_response.pop(message["ActionID"])
                            else:
                                self.logger.info(message["Channel"] + " has failed. Call him later...")
                                call = self.originate_response[message["ActionID"]]
                                call.later()
                                self.calls_queue["later"].put_nowait(call)
                                self.originate_response.pop(message["ActionID"])
                    except Queue.Empty:
                        pass
                # transfer calls from queue.retry_later to queue.retry
                while (self.calls_queue["retry_later"].qsize() != 0):
                    try:
                        r_call = self.calls_queue["retry_later"].get_nowait()
                        self.calls_queue["retry"].put_nowait(r_call)
                    except Queue.Empty:
                        pass
                # queue 'retry' processor
                while (self.calls_queue["retry"].qsize() != 0):
                    try:
                        call = self.calls_queue["retry"].get_nowait()
                        self._repeatCall(call)
                    except Queue.Empty:
                        pass
                # queue 'later' processor
                while (self.calls_queue["later"].qsize() != 0):
                    try:
                        call = self.calls_queue["later"].get_nowait()
                        later_time = call.timestamp + 15
                        if later_time <= int(round(time.time())):
                            self._repeatCall_ttl(call)
                        else:
                            self.calls_queue["later"].put_nowait(call)
                            break
                    except Queue.Empty:
                        pass

        """
    Method [self.makeCall] is calling in the following order:
        1. Caller
        2. Destination
    If the call is successful - immediately forget about it.
    If there are no channels available - call queued to retry-queue which is checked every second (ie call again soon as available channel)
    If the call did not materialize for other reasons - chime performed later, after a fixed time period (15 sec)
        """

    def makeCall(self,caller,destination):
        call = SystemCall(caller,destination)
        if (call.numb_check() == True):
            channel_stat = call.get_channel()
            if (channel_stat != None):
                if (channel_stat != False):
                    try:
                        data = self._manager.send_action(call.originate())
                    except pystrix.ami.ManagerSocketError:
                        self.logger.warning("Connection refused while self.makeCall was executing")
                        self.calls_queue["retry"].put_nowait(call)
                    else:
                        if data.success != True:
                            self.calls_queue["retry"].put_nowait(call)
                        else:
                            self.originate_response[str(data.action_id)] = call
                            self.logger.info("Call queued [" + call.caller + " > " + call.destination + "] via " + call.channel)
                else:
                    self.logger.warning("No available channels to " + call.caller)
                    self.calls_queue["retry"].put_nowait(call)
                return json.dumps(True)
            else:
                self.logger.warning("Cannot find channel to " + call.caller)
                del call
                return json.dumps(False)
        else:
            self.logger.warning("Numbers not valid: " + call.primal_caller + ";" + call.primal_destination)
            del call
            return json.dumps(False)

    def agentPause(self,name,paused,queue = None):
        self.logger.info("[agentPause] Agent {name} change pause to {paused}".format(name = name, paused = paused))
        response = self._manager.send_action(pystrix.ami.core.QueuePause(name,paused,queue))
        return response.success

    def queuePenalty(self,name,penalty):
        self.logger.info("[queuePenalty] Agent {name} set penalty to {pen}".format(name = name, pen = penalty))
        response = self._manager.send_action(pystrix.ami.core.QueuePenalty(name,penalty))
        return response.success

    def agentLogoff(self,name):
        if re.search('^Agent/\d+$', name):
            name = name.split("/")[1]
            self.logger.info("[agentLogoff] Agent {agent} logged off".format(agent = name))
            response = self._manager.send_action(pystrix.ami.core.AgentLogoff(name))
        return response.success

    def setVar(self,chan,variable,value):
        self.logger.info("[setVar] {var} set to {val} on {ch}".format(var = variable, val = value, ch = chan))
        response = self._manager.send_action(pystrix.ami.core.Setvar(variable,value,chan))
        return response.success

    def chanStatus(self,name,variables = None):
        action = self._manager.send_action(pystrix.ami.core.Status(name,variables))
        action_id = action.action_id
        if action and action.success:
            self.logger.info("[chanStatus] {ai} request for a {ch}-chan info success".format(ai = action_id, ch = name))
            self.logger.info(action)
            while self.action_lookup == True:
                self.logger.info("{ai}: Waiting for another action lookup end".format(ai = action_id))
                time.sleep(1)
            msg = self._get_action_data(action_id)
            return msg
        else:
            self.logger.warning("[chanStatus] {ai} request for a {ch}-chan info failed".format(ai = action_id, ch = name))
            return json.dumps(False)

    def getVar(self,variable,channel = None):
        action = self._manager.send_action(pystrix.ami.core.Getvar(variable,channel))
        action_id = action.action_id
        if action and action.success:
            self.logger.info("[getVar] {ai} request for a {var} var - success".format(ai = action_id, var = variable))
            msg = json.dumps(action.result)
            return msg
        else:
            self.logger.warning("[getVar] {ai} request for a {var} var - failed".format(ai = action_id, var = variable))
            return json.dumps(False)

    def queueCityData(self,city):
        city_wait_var = "QUEUE_CITY_WAIT_" + str(city)
        city_hold_var = "QUEUE_CITY_HOLD_" + str(city)
        queue_wait = json.loads(self.getVar(city_wait_var))
        queue_hold = json.loads(self.getVar(city_hold_var))
        if queue_wait != False and queue_hold != False:
            result = {
                "Success" : True,
                "QUEUE_CITY_WAIT" : queue_wait["Value"],
                "QUEUE_CITY_HOLD" : queue_hold["Value"]
            }
        else:
            result = {
                "Success" : False
            }
        #self.setVar(None,city_wait_var,"0")
        #self.setVar(None,city_hold_var,"0")
        return json.dumps(result)

    def _get_action_data(self,actid):
        self.action_lookup = True
        while True:
            message = json.loads(self.get_messages("action_responses"))
            self.logger.info(message)
            if str(message["ActionID"]) == str(actid):
                self.logger.info("Find data for {ai}".format(ai = actid))
                while (self.not_my_actions.qsize() != 0):
                    try:
                        nma_msg = self.not_my_actions.get_nowait()
                        self.logger.info('Packing all NOT_MY_MSG to the shared queue')
                        self.queue["action_responses"].put_nowait(nma_msg)
                    except Queue.Empty:
                        self.logger.warning('Enough to packing')
                        pass
                self.action_lookup = False
                return json.dumps(message)
            else:
                self.logger.info("Data is not for {ai}".format(ai = actid))
                self.not_my_actions.put_nowait(json.dumps(message))

    def _repeatCall(self,call,ttl = False):
        if ttl == True:
            if call.ttl_deduct() == 0:
                return False
        # We will not repeat channel-calls
        if call.caller_is_channel:
            return False
        channel_stat = call.get_channel()
        if (channel_stat != False):
            try:
                data = self._manager.send_action(call.originate())
            except ManagerSocketError:
                self.logger.warning("Connection refused while self.makeCall was executing")
                self.calls_queue["retry"].put_nowait(call)
            else:
                if data.success != True:
                    self.calls_queue["retry_later"].put_nowait(call)
                else:
                    self.originate_response[str(data.action_id)] = call
                    self.logger.info("REPEAT: Call queued [" + call.caller + " > " + call.destination + "] via " + call.channel)
        else:
            self.logger.warning("REPEAT: No available channels to " + call.caller)
            self.calls_queue["retry_later"].put_nowait(call)

    def _repeatCall_ttl(self,call):
        self._repeatCall(call,ttl = True)

    def catch_event(self,event,queue="share"):
        if not queue in self.queue:
            self.queue[queue] = MSGQueue(queue)
        self.queue[queue].add_event(event)
        if not str(event) in self.registered_events:
            self.registered_events.append(event)
            self._manager.register_callback(str(event), self._handle_new_message)

    def get_messages(self,queue):
        return self.queue[str(queue)].stack.get()

    def get_messages_nowait(self,queue):
        return self.queue[str(queue)].stack.get_nowait()

    def _handle_new_message(self,event,manager):
        for queue in self.queue:
            if self.queue[queue].has_event(event.name):
                #print event
                self.queue[queue].put(json.dumps(event))
                #print "Event [" + str(event.name) + "] " + str(queue) + ".put(msg) qsize - " + str(self.queue[queue].qsize())

    def _handle_shutdown(self, event, manager):
        self._kill_flag = True

    def is_alive(self):
        return not self._kill_flag

    def _log_set(self,pathInfo,logLevel):
        self.pathInfo=pathInfo
        self.logLevel=logLevel
        self.hdlr = logging.FileHandler(self.pathInfo)
        self.formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        self.hdlr.setFormatter(self.formatter)
        self.logger.addHandler(self.hdlr)
        if self.logLevel == "info":
            self.logger.setLevel(logging.INFO)
        elif self.logLevel == "warn":
            self.logger.setLevel(logging.WARNING)

    def kill(self):
        self._manager.close()


class MSGQueue():
    name = None
    events = None
    stack = None

    def __init__(self,name):
        self.stack = Queue.Queue(10)
        self.name = name
        self.events = []

    def add_event(self,event):
        self.events.append(event)

    def show_events(self):
        return self.events

    def has_event(self,event):
        return 1 if event in self.events else 0

    def qsize(self):
        return self.stack.qsize()

    def put(self,msg):
        #if self.stack.full():
        # Queue is overloaded. Clear message stack
        #	self.stack.queue.clear()
        #	self.stack.put(msg)
        try:
            self.stack.put_nowait(msg)
        except Queue.Full:
            self.stack.queue.clear()
            self.stack.put_nowait(msg)

class Error(Exception):
    """
    The base class from which all exceptions native to this module inherit.
    """

class ConnectionError(Error):
    """
    Indicates that a problem occurred while connecting to the Asterisk server
    or that the connection was severed unexpectedly.
    """

class AMIserver():
    def run(self):
        class RequestHandler(SimpleXMLRPCRequestHandler):
            rpc_paths = ('/RPC2',)

        #server = SimpleXMLRPCServer((BIND_HOST , BIND_PORT) , logRequests=False , requestHandler=RequestHandler
        server = MyXMLRPCServer((BIND_HOST , BIND_PORT) , logRequests=False , requestHandler=RequestHandler, allow_none=True)
        server.register_introspection_functions()
        server.register_instance(AMICore())
        server.serve_forever()

if __name__ == '__main__':
    print 'This a module but not a programm'
