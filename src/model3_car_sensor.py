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

'''This module implements a Tesla Model 3 Sensor providing state of charge and
mileage information.

'''

import os
import sys
from datetime import datetime, timedelta
from select import select

import geopy.distance
import Pyro5
from geopy.geocoders import Nominatim
from teslapy import Tesla

from sensor import Sensor
from tools import (NameServer, Settings, debug, get_storage, init,
                   log_exception, my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'home_distance_threshold_feet': 50}

class Model3CarSensor(Sensor):
    '''Sensor collecting information via Tesla API.'''

    def __init__(self, vehicle, home_coordinate, settings):
        self.vehicle = vehicle
        self.home_coordinate = home_coordinate
        self.settings = settings
        self.cache = {}

    @Pyro5.api.expose
    def read(self, **kwargs):
        return self.cache

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {'odometer': 'mi',
                'is home': 'bool',
                'is plugged in': 'bool',
                'state of charge': '%',
                'latitude': 'latitude',
                'longitude': 'longitude',
                'charge_limit_soc': '%'}

    def update(self):
        '''Attempt to collect a record from the car.'''
        # if self.vehicle.available():
        data = self.vehicle.get_vehicle_data()
        self.cache['odometer'] = data['vehicle_state']['odometer']
        self.cache['state of charge'] = data['charge_state']['battery_level']
        self.cache['latitude'] = data['drive_state']['latitude']
        self.cache['longitude'] = data['drive_state']['longitude']
        distance = geopy.distance.geodesic(self.home_coordinate,
                                           (data['drive_state']['latitude'],
                                            data['drive_state']['longitude']))
        if distance.feet < self.settings.home_distance_threshold_feet:
            self.cache['is home'] = True
        else:
            self.cache['is home'] = False
        if data['charge_state']['charging_state'] in ['NoPower', 'Charging',
                                                      'Complete', 'Stopped']:
            self.cache['is plugged in'] = True
        else:
            self.cache['is plugged in'] = False

class Model3CarSensorProxy(Sensor):
    # pylint: disable=too-few-public-methods
    '''Helper class for Model3 Sensor.

    This class is a wrapper of the Model3 sensor and service with exception
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
                    self.sensor = NameServer().locate_sensor('model3')
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

def load_cache():
    '''Load Tesla cache data.'''
    with get_storage() as storage:
        return storage['Tesla']

def save_cache(cache):
    '''Store Tesla cache data.'''
    with get_storage() as storage:
        storage['Tesla'] = cache

def main():
    '''Register and run the Model3 Sensor.'''
    # pylint: disable=too-many-locals
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    locator = Nominatim(user_agent=config['general']['application'])
    point = locator.geocode(config['general']['address'])

    tesla = Tesla(config['Tesla']['login'],
                  cache_loader=load_cache, cache_dumper=save_cache)
    vehicle = next(v for v in tesla.vehicle_list() \
                   if v['vin'] == config['Tesla']['vin'])

    sensor = Model3CarSensor(vehicle, (point.latitude, point.longitude),
                             settings)

    daemon = Pyro5.api.Daemon()
    uri = daemon.register(sensor)

    nameserver = NameServer()
    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        watchdog.register(os.getpid(), 'model3_car_sensor')
        watchdog.kick(os.getpid())

        try:
            nameserver.register_sensor('model3_car', uri)
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
