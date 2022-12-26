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

'''This module implements a car charger task.

It manages two chargers connected to the same NEMA 14-50 outlet.

'''

import os
import socket
import sys
from abc import abstractmethod
from datetime import date, datetime, timedelta
from enum import IntEnum
from select import select
from time import sleep

import geopy.distance
import Pyro5
import requests
import teslapy
from cachetools import TTLCache
from geopy.geocoders import Nominatim
from teslapy import Tesla
from wallbox import Wallbox

from model3_car_sensor import load_cache, save_cache
from power_sensor import RecordScale
from scheduler import Priority, SchedulerProxy, Task
from sensor import Sensor, SensorReader
from tools import (NameServer, Settings, debug, init, log_exception,
                   my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'power_sensor_key': 'EV',
                    'cycle_length': 15,
                    'home_distance_threshold_feet': 500,
                    'commute_car': 'Chevy Bolt EV'}

MODULE_NAME = 'car_charger'

class SensorReaderCache(Sensor):
    '''Act as a data cache for a sensor.'''
    def __init__(self, name):
        self.sensor = SensorReader(name)
        self.update()

    def update(self):
        '''Update the cache.'''
        self.cache = self.sensor.read()

    def read(self, **kwargs: dict) -> dict:
        return self.cache

    def units(self, **kwargs: dict) -> dict:
        raise RuntimeError('Should not be called')

class CarCharger:
    '''Represent a car charger.'''
    def __init__(self, name):
        self.name = name

    @abstractmethod
    def start(self):
        '''Start charging.'''

    @abstractmethod
    def stop(self):
        '''Stop charging.'''

    @abstractmethod
    def is_charging(self):
        '''Charging status'''

    @abstractmethod
    def is_plugged_in(self):
        '''True if the charger is plugged in to the car.'''

    @abstractmethod
    def can_charge(self):
        '''True if the car can accept a charge.'''

    @property
    @abstractmethod
    def min_charging_current(self):
        '''Maximal current supported by the charger in Ampere.'''

    @property
    @abstractmethod
    def max_charging_current(self):
        '''Maximal current supported by the charger in Ampere.'''

    @property
    @abstractmethod
    def charging_current(self):
        '''Current charging current in Ampere.'''

    @property
    @abstractmethod
    def state_of_charge(self):
        '''Current state of charge.'''

    @property
    @abstractmethod
    def max_state_of_charge(self):
        '''Maximum State of charge.'''

    @property
    @abstractmethod
    def low_priority_threshold(self):
        '''Define the LOW priority state of charge threshold.

        Returns None to use the default.'''

    @property
    def priority(self):
        '''Priority of this car charger.'''
        # Set the low threshold so that the highest the requested maximum
        # charge of state the highest the threshold.
        low = self.low_priority_threshold
        if not low:
            low = self.max_state_of_charge - (100 - self.max_state_of_charge) / 2
        thresholds = {Priority.URGENT: 33,
                      Priority.HIGH: 55,
                      Priority.MEDIUM: low,
                      Priority.LOW: 101}
        if not self.is_plugged_in() or not self.can_charge():
            return Priority.LOW
        for priority in reversed(Priority):
            if self.state_of_charge < thresholds[priority]:
                return priority
        return Priority.LOW

class WallboxCarCharger(CarCharger):
    '''CarCharger implementation for Wallbox Pulse 2 EV charger.'''
    class Status(IntEnum):
        '''Wallbox charger states.'''
        FULLY_CHARGED = 181
        UNPLUGGED = 161
        WAITING_FOR_NEXT_SCHEDULE = 179
        PAUSED = 182
        CHARGING = 194

    def __init__(self, name, wallbox, charger_id, sensor, max_state_of_charge):
        CarCharger.__init__(self, name)
        self.wallbox = wallbox
        self.charger_id = charger_id
        self.sensor = sensor
        self._max_state_of_charge = max_state_of_charge
        self.cache = TTLCache(1, timedelta(seconds=15), datetime.now)

    def __call(self, name, *args):
        for _ in range(3):
            try:
                method = getattr(self.wallbox, name)
                return method(self.charger_id, *args)
            except requests.exceptions.HTTPError:
                log_exception(f'{name}{args} failed', *sys.exc_info())
                self.wallbox.authenticate()
            except (requests.exceptions.RequestException,
                    socket.gaierror, OSError):
                log_exception(f'{name}{args} failed', *sys.exc_info())
                sleep(0.5)
        raise RuntimeError(f'{name}{args} failed too many times')

    @property
    def status(self):
        '''JSON representation of the charger status.'''
        try:
            return self.cache['status']
        except KeyError:
            status = self.__call('getChargerStatus')
            self.cache['status'] = status
            return self.cache['status']

    def start(self):
        self.__call('resumeChargingSession')
        self.cache.clear()

    def stop(self):
        self.__call('pauseChargingSession')
        self.charging_current = self.min_charging_current
        self.cache.clear()

    @property
    def status_id(self):
        '''Identifier describing the charger status.'''
        return self.status['status_id']

    def is_charging(self):
        return self.status_id == self.Status.CHARGING

    def is_plugged_in(self):
        return self.status_id not in [self.Status.UNPLUGGED,
                                      self.Status.FULLY_CHARGED]

    def can_charge(self):
        return self.state_of_charge < self.max_state_of_charge

    @property
    def min_charging_current(self):
        return 6

    @property
    def max_charging_current(self):
        return self.status['config_data']['max_available_current']

    @property
    def charging_current(self):
        return self.status['config_data']['max_charging_current']

    @charging_current.setter
    def charging_current(self, current):
        self.__call('setMaxChargingCurrent', current)

    @property
    def state_of_charge(self):
        return self.sensor.read()['state of charge']

    @property
    def max_state_of_charge(self):
        '''Maximum State of charge.'''
        return self._max_state_of_charge

    @property
    def low_priority_threshold(self):
        if date.today().weekday() == 0 or date.today().weekday() == 6:
            return self._max_state_of_charge
        return None

class TeslaCarCharger(CarCharger):
    '''CarCharger implementation for Tesla.'''
    def __init__(self, name, vehicle, home, settings):
        CarCharger.__init__(self, name)
        self.vehicle = vehicle
        self.home = home
        self.settings = settings
        self.cache = TTLCache(1, timedelta(seconds=15), datetime.now)
        # On initialization, wake-up the car to get the car location
        if not 'drive_state' in self.status:
            self.vehicle.sync_wake_up()
        # By default, consider the car not home to prevent any unexpected
        # misbehavior.
        self.was_home = False
        self.was_home = self.is_home()

    @property
    def status(self):
        '''JSON representation of the charger status.'''
        try:
            return self.cache['status']
        except KeyError:
            try:
                vehicle_data = self.vehicle.get_vehicle_data()
            except requests.exceptions.RequestException as err:
                raise RuntimeError('Failed to get vehicle data') from err
            status = vehicle_data['charge_state']
            if 'drive_state' in vehicle_data:
                status.update(vehicle_data['drive_state'])
            else:
                debug('Missing "drive_state"')
            self.cache['status'] = status
            return self.cache['status']

    def _command(self, command, **kwargs):
        for _ in range(2):
            try:
                self.vehicle.command(command, **kwargs)
            except requests.exceptions.HTTPError as err:
                if err.response.status_code == 408:
                    debug('Vehicle offline, try to wake up')
                    self.vehicle.sync_wake_up()
            except (requests.exceptions.ReadTimeout, teslapy.VehicleError):
                log_exception(f'{command} failed', *sys.exc_info())

    def start(self):
        self._command('START_CHARGE')

    def stop(self):
        self.charging_current = self.min_charging_current
        self._command('STOP_CHARGE')

    def is_home(self):
        '''True if the car is located at home.'''
        if 'latitude' in self.status \
           and 'longitude' in self.status:
            distance = geopy.distance.geodesic(self.home,
                                               (self.status['latitude'],
                                                self.status['longitude']))
            self.was_home = distance.feet < \
                self.settings.home_distance_threshold_feet
        return self.was_home

    def is_charging(self):
        return self.is_home() and self.status['charging_state'] == 'Charging'

    def is_plugged_in(self):
        charging_states = ['NoPower', 'Charging', 'Complete', 'Stopped']
        return self.is_home() \
            and self.status['charging_state'] in charging_states

    def can_charge(self):
        return self.is_home() \
            and self.status['charging_state'] != 'Complete' \
            and self.status['battery_level'] < self.max_state_of_charge

    @property
    def min_charging_current(self):
        return 2

    @property
    def max_charging_current(self):
        return self.status['charge_current_request_max']

    @property
    def charging_current(self):
        return self.status['charge_amps']

    @charging_current.setter
    def charging_current(self, current):
        if self.charging_current == current:
            return
        self._command('CHARGING_AMPS', charging_amps=current)
        # According to https://github.com/tdorssers/TeslaPy, it can be set to
        # lower than 5 by calling the interface twice
        if current < 5:
            self._command('CHARGING_AMPS', charging_amps=current)

    @property
    def state_of_charge(self):
        return self.status['battery_level']

    @property
    def max_state_of_charge(self):
        return self.status['charge_limit_soc']

    @property
    def low_priority_threshold(self):
        return None

class CarChargerTask(Task):
    '''Task handling car charging.'''
    def __init__(self, charger, settings: Settings):
        Task.__init__(self, keys=[settings.power_sensor_key], auto_adjust=True)
        self.charger = charger
        self.settings = settings

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def start(self):
        debug('Starting')
        self.charger.start()

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def stop(self):
        debug('Stopping')
        self.charger.stop()

    @Pyro5.api.expose
    def is_running(self) -> bool:
        return self.charger.is_charging()

    @Pyro5.api.expose
    def is_stoppable(self):
        return True

    @Pyro5.api.expose
    def is_runnable(self):
        '''True if calling the 'start' function would initiate charging.'''
        return self.charger.is_plugged_in() and self.charger.can_charge()

    @Pyro5.api.expose
    def meet_running_criteria(self, ratio, power=0) -> bool:
        debug(f'meet_running_criteria({ratio:.3f}, {power:.3f})')
        if not self.is_runnable():
            return False
        if self.is_running():
            return ratio >= 0.9
        return ratio >= 1

    @property
    @Pyro5.api.expose
    def desc(self):
        description = f'CarCharger ({self.priority.name}'
        description += f', {self.charger.name}'
        if self.charger.state_of_charge is not None:
            description += f', {self.charger.state_of_charge:.1f}%'
        return description + ')'

    @property
    @Pyro5.api.expose
    def power(self):
        return self.charger.min_charging_current * .237

    @property
    @Pyro5.api.expose
    def priority(self):
        return self.charger.priority

    def current_rate_for(self, power):
        '''Return the appropriate current in Ampere for POWER in KWh.'''
        rate = max(int(power / .237), self.charger.min_charging_current)
        return min(rate, self.charger.max_charging_current)

    def adjust_charge_rate(self, record):
        '''Adjust the charging rate according to the instant POWER record.'''
        available = -(record['net'] - self.usage(record))
        current = self.current_rate_for(available)
        if self.charger.charging_current != current:
            debug(f'Adjusting to {current}A ({available:.2f} KWh)')
            self.charger.charging_current = current

def main():
    '''Register and run the car charger task.'''
    # pylint: disable=too-many-locals
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    locator = Nominatim(user_agent=config['general']['application'])
    point = locator.geocode(config['general']['address'])

    chargers = []

    # Bolt EV uses the Wallbox Pulsar II charger
    wallbox = Wallbox(config['Wallbox']['login'],
                      config['Wallbox']['password'], requestGetTimeout=5)
    wallbox.authenticate()
    device_id = int(config['Wallbox']['device_id'])
    if device_id not in wallbox.getChargersList():
        raise RuntimeError(f'{device_id} charger ID does not exist')
    car_sensor = SensorReaderCache('car')
    chargers.append(WallboxCarCharger('Chevy Bolt EV', wallbox, device_id,
                                      car_sensor, 79.6))

    # Tesla Model3 uses the Gen2 Tesla Charger
    tesla = Tesla(config['Tesla']['login'],
                  cache_loader=load_cache, cache_dumper=save_cache)
    vehicle = next(v for v in tesla.vehicle_list() \
                   if v['vin'] == config['Tesla']['vin'])
    chargers.append(TeslaCarCharger('Tesla Model 3', vehicle,
                                    (point.latitude, point.longitude),
                                    settings))


    Pyro5.config.COMMTIMEOUT = 5
    daemon = Pyro5.api.Daemon()
    nameserver = NameServer()

    tasks = {}
    for charger in chargers:
        task = CarChargerTask(charger, settings)
        uri = daemon.register(task)
        tasks[task] = uri

    for i, (task, uri) in enumerate(tasks.items()):
        nameserver.register_task(MODULE_NAME + '_' + str(i), uri)

    power_sensor = SensorReader('power')
    power_simulator = SensorReader('power_simulator')
    scheduler = SchedulerProxy()
    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        settings.load()

        watchdog.register(os.getpid(), MODULE_NAME)
        watchdog.kick(os.getpid())

        try:
            car_sensor.update()
        except RuntimeError:
            log_exception('Failed to update car data', *sys.exc_info())

        try:
            for i, (task, uri) in enumerate(tasks.items()):
                nameserver.register_task(MODULE_NAME + '_' + str(i), uri)
        except RuntimeError:
            log_exception('Failed to register a task', *sys.exc_info())

        # Self-testing: on basic operation failure unregister from the
        # scheduler.
        for i, (task, uri) in enumerate(tasks.items()):
            try:
                task.charger.is_charging() # pylint: disable=pointless-statement
                scheduler.register_task(uri)
            except RuntimeError:
                debug('Self-test failed on %d, unregister from the scheduler' %
                      i)
                scheduler.unregister_task(uri)

        next_cycle = datetime.now() + timedelta(
            # pylint: disable=maybe-no-member
            seconds=settings.cycle_length)
        while True:
            timeout = next_cycle - datetime.now()
            sockets, _, _ = select(daemon.sockets, [], [],
                                   timeout.seconds
                                   + timeout.microseconds / 1000000)
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break

        try:
            task = next(task for task in tasks if task.is_running())
        except (RuntimeError, StopIteration):
            continue

        record = power_sensor.read(scale=RecordScale.SECOND)
        if not record:
            debug('No new power record, use the simulator')
            record = power_simulator.read(scale=RecordScale.SECOND)
            if not record:
                debug('Failed to get a record from the simulator')
        if record:
            try:
                task.adjust_charge_rate(record)
            except RuntimeError:
                log_exception('adjust_charge_rate() failed', *sys.exc_info())

if __name__ == "__main__":
    main()
