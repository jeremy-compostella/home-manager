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

'''This module implements a water heater Task based on the Aquanta device.'''

import os
import select
import sys
import time
from datetime import datetime, timedelta, timezone
from select import select

import Pyro5.api
import requests
from cachetools import TTLCache
from dateutil import parser

from aquanta import Aquanta
from power_simulator import PowerSimulatorProxy
from scheduler import Priority, SchedulerProxy, Task
from sensor import Sensor
from tools import NameServer, Settings, debug, fahrenheit, init, log_exception
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'power': 4.65,
                    'minutes_per_degree': 2,
                    'desired_temperature': 125,
                    'min_run_time': 60 * 10,
                    'no_power_delay': 60 * 30,
                    'power_sensor_key': 'water heater',
                    'attempt_delay': 0.5}

class WaterHeaterState:
    '''Water heater state: temperature and tank level.

    The Aquanta sensors are unreliable and sometimes give the false impression
    that the tank is full and the temperature good while actually the water
    heater ran for a limited time a obviously isn't full nor has been able to
    reach this temperature for the water of the entire tank.

    This WaterHeaterState class acts as a proxy by updating the state
    representation

    '''
    def __init__(self, settings):
        self.settings = settings
        self.temperature = None
        self.tank_level = None

    def update(self, temperature, tank_level, force=False):
        if force \
           or self.temperature is None \
           or temperature < self.temperature \
           or tank_level < self.tank_level:
            self.temperature = temperature
            self.tank_level = tank_level

# pylint: disable=too-many-instance-attributes
# 10 attributes is reasonable for this class.
class WaterHeater(Task, Sensor):
    '''Aquanta controlled water heater Task and Sensor.

    This task makes use of the Aquanta away and boost features to control the
    water heater.

    The implementation assumes that the Aquanta device is configured in timer
    mode. As a result, the code is a little bit complexity to handle the
    schedules but the benefit is that if the task/scheduler stops running, or
    if the Aquanta server is inaccessible or, if the API changed unexpectedly,
    the Aquanta device should fallback automatically on its schedule.

    The Aquanta temperature sensor and available water are per design partially
    driven by some software algorithms. Indeed the temperature sensor sit
    outside the tank and the water level cannot be detected accurately.
    Therefore, if the water heater is not using any power for a little while,
    the WaterHeater task stops itself, sets the priority to LOW and waits for
    the temperature or available values to change before making any decision.

    '''
    def __init__(self, aquanta, settings):
        Task.__init__(self, Priority.LOW, power=settings.power,
                      keys=[settings.power_sensor_key])
        self.aquanta = aquanta
        self.settings = settings
        self.state = WaterHeaterState(settings)
        self.target_time = datetime.min
        self.started_at = None
        self._not_runnable_till = datetime.min
        self.cache = TTLCache(3, timedelta(seconds=30), datetime.now)
        self.adjust_priority()

    def _getattr(self, name):
        try:
            return self.cache[name]
        except KeyError:
            pass
        for _ in range(3):
            try:
                self.cache[name] = getattr(self.aquanta, name)
                return self.cache[name]
            except (requests.exceptions.RequestException, RuntimeError):
                log_exception('Failed to get %s attribute' % name,
                              *sys.exc_info())
                time.sleep(self.settings.attempt_delay)
        raise RuntimeError('%s attribute access failed' % name)

    @property
    def min_run_time(self):
        '''Minimal run time for the water heater.

        It is to prevent damage of the water heater by turning it on and off
        too frequently.

        '''
        return timedelta(seconds=self.settings.min_run_time)

    @property
    def desired_temperature(self):
        '''The desired water temperature.'''
        return self.settings.desired_temperature

    def _update_state(self):
        state = self._getattr('water')
        force = datetime.now() < self._not_runnable_till
        # Water tank level has decreased, make the task runnable
        if self.state.tank_level is not None \
           and self.state.tank_level > state['available']:
            debug('Making runnable %s %s' \
                  % (self.state.tank_level, state['available']))
            self._not_runnable_till = datetime.min
        self.state.update(state['temperature'], state['available'],
                          force=force)

    @property
    def temperature(self):
        '''Current water temperature.'''
        self._update_state()
        return fahrenheit(self.state.temperature)

    @property
    def available(self):
        '''Current water tank level expressed as percent.'''
        self._update_state()
        return self.state.tank_level * 100

    def estimate_run_time(self):
        '''Estimate the required time to reach the target temperature.'''
        temperature = 60 * (100 - self.available) / 100 \
            + self.temperature * self.available / 100
        deviation = self.desired_temperature - temperature
        return timedelta(minutes=int(deviation
                                     * self.settings.minutes_per_degree))

    @property
    def mode(self):
        '''Return the Aquanta device active mode.

        Usually one of 'away', 'boost' or 'timer'.

        '''
        return self._getattr('infocenter')['currentMode']['type']

    @mode.setter
    def mode(self, value):
        try:
            mode, duration = value
        except ValueError as err:
            if value != 'timer':
                raise ValueError('Invalid %s for mode setter') from err
            mode = value
        if mode not in ['boost', 'away', 'timer']:
            raise ValueError('Unsupported %s mode')
        if mode == 'timer':
            if self.mode == 'timer':
                return
            fun = getattr(self.aquanta, 'delete_' + self.mode)
        else:
            now = datetime.now(timezone.utc)
            start = now - timedelta(minutes=1)
            end = now + duration
            set_method = getattr(self.aquanta, 'set_' + mode)
            fun = lambda: set_method(start.strftime(self.aquanta.DATE_FORMAT),
                                     end.strftime(self.aquanta.DATE_FORMAT))
        for _ in range(3):
            try:
                fun()
                del self.cache['infocenter']
                return
            except (requests.exceptions.RequestException, RuntimeError):
                log_exception('Failed to change the mode', *sys.exc_info())
                time.sleep(self.settings.attempt_delay)
        raise RuntimeError('Failed to change the mode')

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def start(self):
        '''Turn on the water heater.'''
        if self.is_running():
            return
        if self.mode == 'away':
            self.mode = 'timer'
        duration = max(self.estimate_run_time(), self.min_run_time)
        debug('Starting for %s' % duration)
        self.mode = ('boost', duration)
        self.started_at = datetime.now()

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def stop(self):
        '''Turn off the water heater.

        If the water heater is running but has not been running for
        MIN_RUN_TIME, this function does not do anything.

        '''
        # if not self.is_stoppable():
        #     return
        if self.mode == 'boost':
            self.mode = 'timer'
        now = datetime.now()
        for sched in self.today_schedule():
            if sched[0] <= now < sched[1]:
                self.mode = ('away', sched[1] - now)
                break
        self.started_at = None

    @Pyro5.api.expose
    def is_runnable(self):
        '''True if the Task can be schedule.'''
        return datetime.now() > self._not_runnable_till \
            and not self.has_reached_target

    @Pyro5.api.expose
    def is_running(self):
        return self.mode in ['setpoint', 'boost']

    def has_been_running_for(self):
        '''Return the time the water heater has been running.'''
        if self.is_running():
            # Handle the situation where it has been started without using the
            # start() method (by using the Aquanta application for example).
            if not self.started_at:
                self.started_at = datetime.now()
            return datetime.now() - self.started_at
        return timedelta()

    @Pyro5.api.expose
    def is_stoppable(self):
        '''Return True if it has been running for MIN_RUN_TIME.'''
        if not self.is_runnable():
            return True
        return self.has_been_running_for() > self.min_run_time

    @Pyro5.api.expose
    def meet_running_criteria(self, ratio, power=0):
        '''True if the water heater can be turned on or should keep running.

        The water heater may not use any power while it is filling the tank and
        may stop using power or not starting using any power when the tank is
        full tank. This function attempt to detect the best it can when the
        water heater should be started or stopped.

        - If the water heater tank is full we expect that if started it would
          use power right away. If it does not we make the task not runnable
          for 'no_power_delay'.

        - If the water heater has been running for a little while and suddenly
          stop using power, we consider it the tank is full, the water fully
          heated and make the task not runnable for four times 'no_power_delay'.

        '''
        debug('meet_running_criteria(%.3f, %.3f)' % (ratio, power))
        duration = self.has_been_running_for()
        if duration > timedelta():
            if self.available == 100 or duration >= timedelta(minutes=4):
                min_time = timedelta(seconds=30)
                min_power = 1 / 2 * self.power
            else:
                min_time = timedelta(seconds=90)
                min_power = 0
            if duration > min_time and power <= min_power:
                delay = timedelta(seconds=self.settings.no_power_delay)
                if duration > timedelta(minutes=3):
                    delay *= 4
                debug('Not using any enough power, make unrunnable for %s' % delay)
                self._not_runnable_till = datetime.now() + delay
                return False
        # Accept to operate with any ratio if we are too close to the target
        # time and the priority level is URGENT.
        debug('target_time=%s' % self.target_time)
        debug('estimate_run_time()=%s' % self.estimate_run_time())
        if self.priority == Priority.URGENT \
           and self.target_time - datetime.now() < self.estimate_run_time():
            return True
        return ratio >= 1

    @Pyro5.api.expose
    @property
    def desc(self):
        '''String representation of the water heater task and status.'''
        description = 'WaterHeater(%s' % self.priority.name
        try:
            description += ', %d%%, %.2fF' % (self.available, self.temperature)
        except RuntimeError:
            pass
        return description + ')'

    @Pyro5.api.expose
    def read(self, **kwargs):
        return {'temperature': self.temperature, 'available': self.available}

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {'temperature': '°F', 'available': '%'}

    def adjust_priority(self):
        '''Adjust the priority according to the status and target time.

        If the temperature and the water availability has not changed since the
        last priority adjustment, the function aborts.

        The priority is adjusted based on temperature and water availability
        thresholds. If the desired temperature has been reached and the tank is
        full, it sets the has_reached_target attribute to True.

        If the priority is not the highest and we have less time than estimated
        to reach the target, the priority is artificially increased by one
        level.

        '''
        self.cache.clear()
        thresholds = {Priority.URGENT: {'available': 50, 'temperature': 110},
                      Priority.HIGH: {'available': 70, 'temperature': 120},
                      Priority.MEDIUM: {'available': 90,
                                        'temperature': self.desired_temperature},
                      Priority.LOW: {'available': 100,
                                     'temperature': self.desired_temperature}}
        for priority in reversed(Priority):
            if self.available >= thresholds[priority]['available'] \
               and self.temperature >= thresholds[priority]['temperature']:
                continue
            self.has_reached_target = False
            self.priority = priority
            now = datetime.now()
            # Increase the priority if we are close to the target time
            if self.priority < Priority.URGENT \
               and self.target_time > now \
               and self.target_time - now < self.estimate_run_time():
                debug('Close to the target time, increase the priority')
                self.priority = Priority(self.priority + 1)
            return
        self.has_reached_target = True

    def prevent_auto_start(self):
        '''Prevent automatic turn on.

        This function puts the Aquanta in away mode if the schedule is about to
        turn the water heater on. The away mode is set for the duration of the
        programmed ON schedule.

        '''
        if not self.is_running() and self.mode == 'timer':
            now = datetime.now()
            soon = now + timedelta(minutes=3)
            for sched in self.today_schedule():
                if sched[0] <= soon < sched[1]:
                    self.mode = ('away', sched[1] - now)
                    break

    def today_schedule(self):
        '''Return today's schedule as list of [start, stop] datetime.'''
        schedule = [sched for sched in self._getattr('timer')['schedules'] \
                    if (datetime.today().weekday() + 1) % 7 \
                    in sched['daysOfWeek']]
        schedule.sort(key=lambda sched: sched['start']['hour'] * 60 + \
                      sched['start']['minute'])
        if not schedule:
            return []
        res = []
        start = schedule[0]['end']
        for current in schedule[1::]:
            end = current['start']
            res.append([datetime.now().replace(hour=start['hour'],
                                               minute=start['minute'],
                                               second=start['second'],
                                               microsecond=0),
                        datetime.now().replace(hour=end['hour'],
                                               minute=end['minute'],
                                               second=end['second'],
                                               microsecond=0) ])
            start = current['end']
        return res

def register(name, uri, raise_exception=True):
    '''Register 'task' as sensor and task.'''
    try:
        for qualifier in ['sensor', 'task']:
            NameServer().register(qualifier, name, uri)
    except RuntimeError as err:
        log_exception('Failed to register as %s' % qualifier, *sys.exc_info())
        if raise_exception:
            raise err

def device_exist_assert(device_id, aquanta):
    '''Verify that 'device_id' exist for this aquanta account.

    It exits with exit code data error if the device is not found or the device
    list could not be read.

    '''
    try:
        if device_id not in aquanta:
            debug('%d device does not exist' % device_id)
            sys.exit(os.EX_DATAERR)
    except (RuntimeError, requests.exceptions.RequestException):
        debug('Could not access Aquanta device list')
        sys.exit(os.EX_DATAERR)

def main():
    '''Start and register a water heater Task and water heater Sensor.'''
    # pylint: disable=too-many-locals
    base = os.path.splitext(__file__)[0]
    module_name = os.path.basename(base)
    config = init(base + '.log')['Aquanta']
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    aquanta = Aquanta(config['email'], config['password'])
    device_id = int(config['device_id'])
    device_exist_assert(device_id, aquanta)

    task = WaterHeater(aquanta[device_id], settings)

    Pyro5.config.COMMTIMEOUT = 5
    daemon = Pyro5.api.Daemon()
    uri = daemon.register(task)
    register(module_name, uri, raise_exception=True)

    scheduler = SchedulerProxy()
    watchdog = WatchdogProxy()
    power_simulator = PowerSimulatorProxy()
    debug("... is now ready to run")
    while True:
        settings.load()

        watchdog.register(os.getpid(), module_name)
        watchdog.kick(os.getpid())
        register(module_name, uri, raise_exception=False)

        # Self-testing: on basic operation failure unregister from the
        # scheduler.
        try:
            task.temperature # pylint: disable=pointless-statement
            scheduler.register_task(uri)
        except RuntimeError:
            debug('Self-test failed, unregister from the scheduler')
            try:
                scheduler.unregister_task(uri)
            except RuntimeError:
                pass

        while True:
            now = datetime.now()
            timeout = 60 - (now.second + now.microsecond/1000000.0)
            next_cycle = now + timedelta(seconds=timeout)
            sockets, _, _ = select(daemon.sockets, [], [], timeout)
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break
        try:
            task.adjust_priority()
        except RuntimeError:
            log_exception('Could not adjust priority', *sys.exc_info())

        try:
            if not scheduler.is_on_pause():
                task.prevent_auto_start()
        except RuntimeError as err:
            debug(str(err))
        if datetime.now() > task.target_time:
            try:
                _, target_time = power_simulator.next_power_window(task.power)
                task.target_time = parser.parse(target_time)
                debug('target_time updated to %s' % task.target_time)
            except (ValueError, RuntimeError) as err:
                debug(str(err))

if __name__ == "__main__":
    main()
