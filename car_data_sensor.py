#!/usr/bin/env python3
#
# Author: Jeremy Compostella <jeremy.compostella@gmail.com>
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer
#      in the documentation and/or other materials provided with the
#      distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.

import obd
import os
import threading

from bluetooth import *
from datetime import datetime, timedelta
from multiprocessing.connection import Listener
from tools import *

class Watchdog(threading.Thread):
    def __init__(self):
        super(Watchdog, self).__init__()
        self.lock = threading.Lock()
        self.last = datetime.now()
        pass

    def kick(self):
        with self.lock:
            self.last = datetime.now()

    def run(self):
        while True:
            with self.lock:
                if datetime.now() > self.last + timedelta(minutes=2):
                    alert('%s is stuck - exiting ...' %
                          os.path.splitext(__file__)[0])
                    os._exit(1)

def connect(mac):
    debug('Trying to connect to %s device' % mac)
    myobd = obd.OBD(portstr='/dev/rfcomm0', baudrate=10400, protocol='6', fast=False)
    if myobd.status() != obd.OBDStatus.NOT_CONNECTED:
        debug('Connection Established')
        return myobd
    myobd.close()
    return None

def percent(messages):
    for m in messages:
        if len(m.data) == 4:
            return m.data[3] * 100.0 / 255.0
    return None

def odometer(messages):
    for m in messages:
        if len(m.data) == 7:
            return ((m.data[3] * (2 ** 24) +
                     m.data[4] * (2 ** 16) +
                     m.data[5] * (2 ** 8) +
                     m.data[6]) / 10) * 0.621371
    return None

CMDS={ 'EV SoC':obd.OBDCommand('SoC', 'State of Charge', b'228334', 4,
                               percent, 1, True, header=b'7E4'),
       'EV mileage':obd.OBDCommand('Mileage', 'Mileage', b'2200A6', 7,
                                   odometer, 0b11111111, True, header=b'7E0') }

def read_car_data(myobd):
    debug('Retrieving data from the car')
    res = {}
    for key, cmd in CMDS.items():
        for i in range(6):
            resp = myobd.query(cmd, force = True)
            time.sleep(1)
            if resp and resp.value:
                res[key] = resp.value
                break
    return res

class CarDataSensorServer(threading.Thread):
    __current = None

    def __init__(self, config):
        super(CarDataSensorServer, self).__init__()
        self.lock = threading.Lock()
        self.address = (config['host'], int(config['port']))
        with get_storage() as db:
            if "CarData" in db:
                self.__current = db["CarData"]
            else:
                self.__current = { 'time':datetime.now() }
                self.__current.update({ k:-1 for k in CMDS.keys() })

    @property
    def state(self):
        with self.lock:
            return self.__state

    @state.setter
    def state(self, state):
        if not state:
            return
        with self.lock:
            for key, value in state.items():
                self.__current[key] = state[key]
            self.__current['time'] = datetime.now()
            with get_storage() as db:
                db["CarData"] = self.__current

    @property
    def at(self):
        with self.lock:
            return self.__at

    def run(self):
        listener = Listener(self.address)
        while True:
            conn = listener.accept()
            conn.send(self.__current)
        listener.close()

server = None

def main():
    prefix = os.path.splitext(__file__)[0]
    config = init(prefix + '.log')

    global server
    server = CarDataSensorServer(config['CarData'])
    server.start()

    wdt = Watchdog()
    wdt.start()

    obd.logger.setLevel(obd.logging.DEBUG)
    debug("... is now ready to run")
    while True:
        wdt.kick()
        myobd = connect(config['CarData']['obd_MAC'])
        if not myobd:
            time.sleep(15)
            continue

        while True:
            wdt.kick()
            data = read_car_data(myobd)
            if not data:
                break
            debug('Car data: %s' % data)
            server.state = data
            wait_for_next_minute()

        debug('Could not read data from the car')
        myobd.close()
        time.sleep(15)

if __name__ == "__main__":
    main()
