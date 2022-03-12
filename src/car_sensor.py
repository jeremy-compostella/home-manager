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

'''This module implements a car Sensor providing state of charge and mileage
information based on an OBDII bluetooth adapter.

'''

import os
import sys
import time
from datetime import datetime, timedelta
from select import select

import obd
import Pyro5

from sensor import Sensor
from tools import (NameServer, Settings, db_latest_record, debug, init,
                   log_exception, my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'mac': '00:1D:A5:0C:80:96',
                    'port': '/dev/rfcomm0',
                    'baudrate': 10400}

def _percent(messages):
    for message in messages:
        if len(message.data) == 4:
            return message.data[3] * 100.0 / 255.0
    return None

def _odometer(messages):
    for message in messages:
        if len(message.data) == 7:
            return ((message.data[3] * (2 ** 24) +
                     message.data[4] * (2 ** 16) +
                     message.data[5] * (2 ** 8) +
                     message.data[6]) / 10) * 0.621371
    return None

class CarSensor(Sensor):
    '''Sensor collecting information via an OBDII bluetooth adapter.'''

    BOLT_CMDS={'state of charge': obd.OBDCommand('SoC', 'State of Charge',
                                                 b'228334', 4, _percent, 1,
                                                 True, header=b'7E4'),
               'mileage': obd.OBDCommand('Mileage', 'Mileage', b'2200A6', 7,
                                         _odometer, 0b11111111, True,
                                         header=b'7E0')}

    def __init__(self, settings):
        self.mac = settings.mac
        self.port = settings.port
        self.baudrate = settings.baudrate
        self.myobd = None
        latest = db_latest_record('car')
        if latest is None:
            self.record = {key:-1 for key in self.BOLT_CMDS}
        else:
            self.record = {key.replace('_', ' '):value \
                           for key, value in latest.items() \
                           if key != 'timestamp'}

    @Pyro5.api.expose
    def read(self, **kwargs):
        return self.record

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {'state of charge': '%', 'mileage': 'mi'}

    def _connect(self):
        debug('Trying to connect to %s' % self.mac)
        self.myobd = obd.OBD(portstr=self.port, baudrate=self.baudrate,
                             protocol='6', fast=False)
        if self.myobd.status() == obd.OBDStatus.NOT_CONNECTED:
            raise RuntimeError('Failed to establish connection')
        debug('Connection Established')

    def _read_car_data(self):
        success = False
        for key, cmd in self.BOLT_CMDS.items():
            for _ in range(3):
                resp = self.myobd.query(cmd, force=True)
                time.sleep(.3)
                if resp and resp.value:
                    self.record[key] = resp.value
                    debug('{%s: %s}' % (key, self.record))
                    success = True
                    break
        if not success:
            raise RuntimeError('Failed new record from the car')

    def update(self):
        '''Attempt to collect a record from the car.'''
        try:
            if not self.myobd:
                self._connect()
            self._read_car_data()
        except RuntimeError as error:
            if self.myobd:
                self.myobd.close()
                self.myobd = None
            raise error

class CarSensorProxy(Sensor):
    # pylint: disable=too-few-public-methods
    '''Helper class for Car Sensor.

    This class is a wrapper of the car sensor and service with exception
    handlers. It provides convenience for services using the car Sensor
    and Service by suppressing the burden of locating them and handling the
    various remote object related errors.

    '''
    def __init__(self, max_attempt=2):
        self.max_attempt = max_attempt
        self.sensor = None

    def __attemp(self, method, **kwargs):
        for attempt in range(self.max_attempt):
            last_attempt = attempt == self.max_attempt - 1
            if not self.sensor:
                try:
                    self.sensor = NameServer().locate_sensor('car')
                except Pyro5.errors.NamingError:
                    if last_attempt:
                        log_exception('Failed to locate car_sensor',
                                      *sys.exc_info())
                except Pyro5.errors.PyroError:
                    if last_attempt:
                        log_exception('Cannot communicate with car_sensor',
                                      *sys.exc_info())
            if self.sensor:
                try:
                    return getattr(self.sensor, method)(**kwargs)
                except Pyro5.errors.PyroError as err:
                    if last_attempt \
                       and not isinstance(err, Pyro5.errors.TimeoutError):
                        log_exception('Communication failed with car_sensor',
                                      *sys.exc_info())
                        debug("".join(Pyro5.errors.get_pyro_traceback()))
                    self.sensor = None
        raise RuntimeError('Could not communicate with car_sensor')

    def read(self, **kwargs):
        return self.__attemp('read', **kwargs)

    def units(self, **kwargs):
        return self.__attemp('units', **kwargs)

def main():
    '''Register and run the Car Sensor.'''
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    sensor = CarSensor(settings)

    daemon = Pyro5.api.Daemon()
    uri = daemon.register(sensor)

    nameserver = NameServer()
    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        watchdog.register(os.getpid(), 'car_sensor')
        watchdog.kick(os.getpid())

        try:
            nameserver.register_sensor('car', uri)
        except RuntimeError:
            log_exception('Failed to register the sensor',
                          *sys.exc_info())

        next_cycle_delay = 60
        try:
            sensor.update()
        except RuntimeError:
            next_cycle_delay = 15

        next_cycle = datetime.now() + timedelta(seconds=next_cycle_delay)
        while True:
            timeout = next_cycle - datetime.now()
            sockets, _, _ = select(daemon.sockets, [], [],
                                   timeout.seconds
                                   + timeout.microseconds / 1000000)
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break

if __name__ == "__main__":
    main()
