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

'''This module implements a car charger task based on the Wallbox EV charger.

'''

import os
import sys
from datetime import datetime, timedelta
from select import select
from time import sleep

import Pyro5
import requests
from cachetools import TTLCache
from wallbox import Wallbox

from car_sensor import CarSensorProxy
from power_sensor import RecordScale
from scheduler import Priority, SchedulerProxy, Task
from sensor import SensorReader
from tools import (NameServer, Settings, debug, init, log_exception,
                   my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'power_sensor_key': 'EV',
                    'min_available_current': 6,
                    'cycle_length': 15,
                    'max_state_of_charge': 79.6}

MODULE_NAME = 'car_charger'

class CarCharger(Task):
    '''Wallbox car charger Task.

    This task handles a Wallbox car charger and automatically adjusts the
    charge rate based on produced power availability.

    '''
    FULLY_CHARGED = 'Connected: waiting for car demand'
    PLUGGED_IN    = ['Charging', FULLY_CHARGED,
                     'Connected: waiting for next schedule',
                     'Paused by user']

    def __init__(self, wallbox: Wallbox, charger_id: int, settings: Settings):
        Task.__init__(self, Priority.LOW, keys=[settings.power_sensor_key],
                      auto_adjust=True)
        self.wallbox = wallbox
        self.charger_id = charger_id
        self.settings = settings
        self.cache = TTLCache(1, timedelta(seconds=3), datetime.now)
        self.state_of_charge = None

    def __call(self, name, *args):
        for _ in range(3):
            try:
                method = getattr(self.wallbox, name)
                return method(self.charger_id, *args)
            except requests.exceptions.HTTPError:
                log_exception('%s%s failed' % (name, args), *sys.exc_info())
                self.wallbox.authenticate()
            except requests.exceptions.ReadTimeout:
                log_exception('%s%s failed' % (name, args), *sys.exc_info())
                sleep(0.5)
        raise RuntimeError('%s%s failed too many times' % (name, args))

    @property
    def status(self):
        '''JSON representation of the charger status.'''
        try:
            return self.cache['status']
        except KeyError:
            self.cache['status'] = self.__call('getChargerStatus')
            return self.cache['status']

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def start(self):
        debug('Starting')
        self.__call('resumeChargingSession')
        self.cache.clear()

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def stop(self):
        debug('Stopping')
        self.__call('pauseChargingSession')
        self.__call('setMaxChargingCurrent', self.min_available_current)
        self.cache.clear()

    @property
    def status_description(self):
        '''String describing the charger status.'''
        return self.status['status_description']

    @property
    def min_available_current(self):
        '''Minimum current supported by the charger in Ampere.'''
        return self.settings.min_available_current

    @property
    def max_available_current(self):
        '''Maximal current supported by the charger in Ampere.'''
        return self.status['config_data']['max_available_current']

    @Pyro5.api.expose
    def is_running(self) -> bool:
        return self.status_description == 'Charging'

    @Pyro5.api.expose
    def is_stoppable(self):
        return True

    @Pyro5.api.expose
    def is_runnable(self):
        '''True if calling the 'start' function would initiate charging.'''
        return self.state_of_charge < self.settings.max_state_of_charge \
            and self.status_description in self.PLUGGED_IN \
            and self.status_description != self.FULLY_CHARGED

    @Pyro5.api.expose
    def meet_running_criteria(self, ratio, power=0) -> bool:
        debug('meet_running_criteria(%.3f, %.3f)' % (ratio, power))
        if not self.is_runnable():
            return False
        if self.is_running():
            return ratio >= 0.8
        return ratio >= 1

    @property
    @Pyro5.api.expose
    def desc(self):
        description = '%s(%s' % (self.__class__.__name__, self.priority.name)
        if self.state_of_charge is not None:
            description += ', %.1f%%' % self.state_of_charge
        return description + ')'

    @property
    @Pyro5.api.expose
    def power(self):
        return self.min_available_current * .24

    def adjust_priority(self, state_of_charge):
        '''Update the priority according to the current state of charge'''
        self.state_of_charge = state_of_charge
        thresholds = {Priority.URGENT: 40, Priority.HIGH: 55,
                      Priority.MEDIUM: 70, Priority.LOW: 101}
        for priority in reversed(Priority):
            if state_of_charge < thresholds[priority]:
                self.priority = priority
                break

    def current_rate_for(self, power):
        '''Return the appropriate current in Ampere for POWER in KWh.'''
        rate = max(int(power / .24), self.min_available_current)
        return min(rate, self.max_available_current)

    def adjust_charge_rate(self, record):
        '''Adjust the charging rate according to the instant POWER record.'''
        available = -(record['net'] - self.usage(record))
        current = self.current_rate_for(available)
        if self.status['config_data']['max_charging_current'] != current:
            debug('Adjusting to %dA (%.2f KWh)' % (current, available))
            self.__call('setMaxChargingCurrent', current)

def main():
    '''Register and run the car charger task.'''
    # pylint: disable=too-many-locals
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')['Wallbox']
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    wallbox = Wallbox(config['login'], config['password'],
                      requestGetTimeout=5)
    wallbox.authenticate()
    device_id = int(config['device_id'])
    if device_id not in wallbox.getChargersList():
        raise RuntimeError('%d charger ID does not exist' % device_id)
    task = CarCharger(wallbox, device_id, settings)

    Pyro5.config.COMMTIMEOUT = 5
    daemon = Pyro5.api.Daemon()
    nameserver = NameServer()
    uri = daemon.register(task)
    nameserver.register_task(MODULE_NAME, uri)

    sensor = CarSensorProxy()
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
            nameserver.register_task(MODULE_NAME, uri)
        except RuntimeError:
            log_exception('Failed to register the sensor',
                          *sys.exc_info())

        # Self-testing: on basic operation failure unregister from the
        # scheduler.
        try:
            task.status_description # pylint: disable=pointless-statement
            scheduler.register_task(uri)
        except RuntimeError:
            debug('Self-test failed, unregister from the scheduler')
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
            task.adjust_priority(sensor.read()['state of charge'])
        except RuntimeError:
            debug('Could not read current state of charge')

        if not task.is_running():
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
