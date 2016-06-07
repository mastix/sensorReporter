#!/usr/bin/python

"""
 Script:  sensorReporter.py
 Author:  Rich Koshak / Lenny Shirley <http://www.lennysh.com>
 Date:    February 10, 2016
 Purpose: Uses the REST API or MQTT to report updates to the configured sensors
"""

import logging
import logging.handlers
import ConfigParser
import signal
import sys
import time
import traceback
from threading import *
from signalProc import *

try:
    from restConn import RestConnection
    restSupport = True
except:
    restSupport = False
    print 'REST required files not found. REST not supported in this script.'

try:
    from mqttConn import MQTTConnection
    mqttSupport = True
except:
    mqttSupport = False
    print 'MQTT required files not found. MQTT not supported in this script.'

try:
    from bluetoothScanner import *
    bluetoothSupport = True
except ImportError:
    bluetoothSupport = False
    print 'Bluetooth is not supported on this machine'

try:
    from wifiScanner import *
    wifiSupport = True
except ImportError as test:
    wifiSupport = False
    print 'Wifi is not supported on this machine'
    print test

# Globals
logger = logging.getLogger('sensorReporter')
if restSupport:
    restConn = RestConnection()
if mqttSupport:
    mqttConn = MQTTConnection()
config = ConfigParser.ConfigParser(allow_no_value=True)
sensors = []
actuators = []

# The decorators below causes the creation of a SignalHandler attached to this function for each of the
# signals we care about using the handles function above. The resultant SignalHandler is registered with
# the signal.signal so cleanup_and_exit is called when they are received.
#@handles(signal.SIGTERM)
#@handles(signal.SIGHUP)
#@handles(signal.SIGINT)
def cleanup_and_exit():
    """ Signal handler to ensure we disconnect cleanly in the event of a SIGTERM or SIGINT. """

    logger.warn("Terminating the program")
    try:
        mqttConn.client.disconnect()
        logger.info("Successfully disconnected from the MQTT server")
    except:
        pass
    sys.exit(0)

# This decorator registers the function with the SignalHandler blocks_on so the SignalHandler knows
# when the function is running
#@cleanup_and_exit.blocks_on
def check(s):
    """Gets the current state of the passed in sensor and publishes it"""
    s.check_state()

def on_message(client, userdata, msg):
    """Called when a message is received from the MQTT broker, send the current sensor state.
       We don't care what the message is."""

    try:
        logger.info("Received a request for current state, publishing")
        if msg is not None:
            print(msg.topic)
            logger.info("Topic: " + msg.topic + " Message: " + str(msg.payload))
        for s in sensors:
            if s.poll > 0:
                s.check_state()
                s.publish_state()
    except Exception as arg:
        logger.info("Unexpected error: %s", arg)

def main():
    """Polls the sensor pins and publishes any changes"""

    if len(sys.argv) < 2:
        print "No config file specified on the command line!"
        sys.exit(1)

    load_config(sys.argv[1])
    for s in sensors:
        s.lastPoll = time.time()

    logger.info("Kicking off polling threads...")
    while True:

        # Kick off a poll of the sensor in a separate process
        for s in sensors:
            if s.poll > 0 and (time.time() - s.lastPoll) > s.poll:
                s.lastPoll = time.time()
                Thread(target=check, args=(s,)).start()

        time.sleep(0.5) # give the processor a chance if REST is being slow

#------------------------------------------------------------------------------
# Initialization
def config_logger(file, size, num):
    """Configure a rotating log"""
    print "Configuring logger: file = " + file + " size = " + str(size) + " num = " + str(num)
    logger.setLevel(logging.DEBUG)
    fh = logging.handlers.RotatingFileHandler(file, mode='a', maxBytes=size, backupCount=num)
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.info("---------------Started")

def config_mqtt(mqtt_config):
    """Configure the MQTT connection"""

    logger.info("Configuring the MQTT Broker " + mqtt_config.get("MQTT", "Host"))
    mqttConn.config(logger, mqtt_config.get("MQTT", "User"),
                    mqtt_config.get("MQTT", "Password"), mqtt_config.get("MQTT", "Host"),
                    mqtt_config.getint("MQTT", "Port"), mqtt_config.getfloat("MQTT", "Keepalive"),
                    mqtt_config.get("MQTT", "LWT-Topic"), mqtt_config.get("MQTT", "LWT-Msg"),
                    mqtt_config.get("MQTT", "Topic"), on_message, mqtt_config.get("MQTT", "TLS"))

def config_rest(url):
    """Configure the REST connection"""
    restConn.config(logger, url)
    logger.info("REST URL set to: " + url)

def load_config(config_file):
    """Read in the config file, set up the logger, and populate the sensors"""
    print "Loading " + config_file
    config.read(config_file)

    config_logger(config.get("Logging", "File"),
                  config.getint("Logging", "MaxSize"),
                  config.getint("Logging", "NumFiles"))

    if restSupport and config.has_section("REST"):
        config_rest(config.get("REST", "URL"))
    if mqttSupport:
        config_mqtt(config)

    logger.info("Populating the sensor's list...")
    for section in config.sections():
        if section.startswith("Sensor"):
            sensor_type = config.get(section, "Type")
            report_type = config.get(section, "ReportType")
            if report_type == "REST" and restSupport:
                type_connection = restConn
            elif report_type == "MQTT" and mqttSupport:
                type_connection = mqttConn

            if sensor_type == "Bluetooth" and bluetoothSupport:
                sensors.append(BtSensor(config.get(section, "Address"),
                                        config.get(section, "Destination"),
                                        type_connection.publish, logger,
                                        config.getfloat(section, "Poll")))
            elif sensor_type == "Wifi" and wifiSupport:
                sensors.append(WifiSensor(config.get(section, "Address"),
                                        config.get(section, "Destination"),
                                        type_connection.publish, logger,
                                        config.getfloat(section, "Poll")))
            else:
                msg = "Either '%s' is an unknown sensor type, not supported in this script, or '%s' is not supported in this script.  Please see preceding error messages to be sure." % (sensor_type, report_type)
                print msg
                logger.error(msg)

        elif section.startswith("Actuator"):
            actuator_type = config.get(section, "Type")
            subscribe_type = config.get(section, "SubscribeType")
            if subscribe_type == "REST" and restSupport:
                msg = "REST based actuators are not yet supported"
                print msg
                logger.error(msg)
            elif subscribe_type == "MQTT" and mqttSupport:
                type_connection = mqttConn
            else:
                msg = "Skipping actuator '%s' due to lack of support in the script for '%s'. Please see preceding error messages." % (config.get(section, "Destination"), subscribe_type)
                print msg
                logger.warn(msg)
                continue
    return sensors

if __name__ == "__main__":
    main()
