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

'''This module implements an HVAC Task based on the Ecobee thermostat.'''

import os
import signal
import sys
import threading
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from enum import IntEnum
from math import ceil, floor
from select import select
from time import sleep

import bezier
import numpy as np
import pyecobee
import Pyro5.api
import pytz
import requests
from cachetools import TTLCache
from dateutil import parser
from pyecobee import HoldType, Selection, SelectionType, Thermostat

from monitor import MonitorProxy
from power_simulator import PowerSimulatorProxy
from scheduler import Priority, SchedulerProxy, Task
from sensor import Sensor
from tools import NameServer, Settings, debug, get_storage, init, log_exception
from watchdog import WatchdogProxy
from weather import WeatherProxy

DEFAULT_SETTINGS = {'min_run_time': 60 * 7,
                    'min_pause': 60 * 5,
                    'temperature_offset': 2,
                    'goal_time': dtime(hour=22, minute=30),
                    'goal_temperature': 73,
                    'comfort_zone': [71, 78],
                    'power_sensor_keys': ['A/C', 'air handler'],
                    'temperature_sensor': 'Home'}

class Mode(IntEnum):
    '''Define the themostat operating mode.'''
    COOL = -1
    AUTO = 0
    HEAT = 1

class HVACModel:
    '''Estimate the power and efficiency at a outdoor temperature.

    This model is built out of statistics computed from data collected over six
    months. The statistics are turned into points which are smoothed using a
    Bezier curve.

    '''
    def __init__(self):
        with get_storage() as storage:
            data = storage['hvac_model']
        nodes = np.asfortranarray([[r[key] for r in data] for key \
                                   in ['temperature', 'power']])
        self.power_curve = bezier.Curve(nodes, degree=len(data) - 1)
        nodes = np.asfortranarray([[r[key] for r in data] for key \
                                   in ['temperature', 'minute_per_degree']])
        self.time_curve = bezier.Curve(nodes, degree=len(data) - 1)

    def _compute_t(self, temperature):
        return (temperature - self.time_curve.nodes[0][0]) \
            / (self.time_curve.nodes[0][-1] - self.time_curve.nodes[0][0])

    def _power(self, temperature):
        if temperature <= self.power_curve.nodes[0][0]:
            return self.power_curve.nodes[1][0]
        if temperature >= self.power_curve.nodes[0][-1]:
            return self.power_curve.nodes[1][-1]
        return self.power_curve.evaluate(self._compute_t(temperature))[1][0]

    def power(self, temperature):
        '''Power used by the system running at 'temperature'.'''
        return self._power(temperature).item()

    def _time(self, temperature):
        if temperature <= self.time_curve.nodes[0][0]:
            return self.time_curve.nodes[1][0]
        if temperature >= self.time_curve.nodes[0][-1]:
            return self.time_curve.nodes[1][-1]
        return self.time_curve.evaluate(self._compute_t(temperature))[1][0]

    def time(self, temperature):
        '''Time necessary to change the temperature by one degree.'''
        return timedelta(minutes=self._time(temperature))

class HomeModel:
    # pylint: disable=too-few-public-methods
    '''Estimate the indoor temperature change in one minute.

    This estimation should theoretically factor in plenty of data such as house
    sun exposition, weather, indoor temperature, insulation parameters ... etc
    but they are all ignored in this model.

    This model is built out of statistics computed from data collected over six
    months. The statistics are turned into points which are smoothed using a
    Bezier curve.

    '''
    def __init__(self):
        with get_storage() as storage:
            data = storage['home_model']
        nodes = np.asfortranarray([[r[key] for r in data] for key \
                                   in ['temperature', 'degree_per_minute']])
        self.curve = bezier.Curve(nodes, degree=len(data) - 1)

    def _compute_t(self, temperature):
        return (temperature - self.curve.nodes[0][0]) \
            / (self.curve.nodes[0][-1] - self.curve.nodes[0][0])

    def _degree_per_minute(self, temperature):
        if temperature <= self.curve.nodes[0][0]:
            return self.curve.nodes[1][0]
        if temperature >= self.curve.nodes[0][-1]:
            return self.curve.nodes[1][-1]
        return self.curve.evaluate(self._compute_t(temperature))[1][0]

    def degree_per_minute(self, temperature):
        '''Temperature change in degree over a minute of time.

        It returns the estimated temperature of the house when exposed at an
        outdoor 'temperature'. The returned value can be positive or negative.

        '''
        return self._degree_per_minute(temperature).item()

class HVACTask(Task, Sensor):
    '''Ecobee controller HVAC system.

    This task makes use of the Ecobee hold feature to control the HVAC
    system. This task does not modify the Ecobee schedule but it expects that
    at times where the production system runs (daylight for PV for instance),
    the comfort setting temperatures are set to "unreachable" values. The task
    is going to optimally heat or cool the home depending on power availability
    and user defined target temperatures.

    '''
    # pylint: disable=too-many-instance-attributes
    def __init__(self, ecobee, device_id, settings, param):
        Task.__init__(self, Priority.LOW, power=5,
                      keys=settings.power_sensor_keys)
        self.ecobee = ecobee
        self.device_id = device_id
        self.settings = settings
        self.param = param
        self._started_at = None
        self._stopped_at = datetime.min
        self.cache = TTLCache(5, timedelta(seconds=3), datetime.now)
        self.model = HVACModel()

    def _deviation(self):
        return self.indoor_temp - self.param.target_temp

    def _next_helpful_mode(self):
        deviation = self._deviation()
        if deviation == 0:
            return None
        for mode in [Mode.HEAT, Mode.COOL]:
            if self.hvac_mode not in [Mode.AUTO, mode]:
                return None
            if deviation * mode.value < 0:
                return mode
        return None

    def _estimate_runtime(self):
        mode = self._next_helpful_mode()
        if not mode:
            return timedelta()
        deviation = self._deviation()
        rate = self.model.time(self.param.outdoor_temp)
        return rate * abs(deviation)

    @property
    def min_run_time(self):
        '''Minimal run time for the HVAC.

        It is to prevent damage of the water heater by turning it on and off
        too frequently.

        '''
        return timedelta(seconds=self.settings.min_run_time)

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def start(self):
        mode = self._next_helpful_mode()
        duration = self._estimate_runtime()
        target = self.param.target_temp
        target += mode.value * self.settings.temperature_offset
        debug('Starting for %s' % duration)
        resp = self.__attempt('set_hold',
                              **{'hold_type': HoldType.HOLD_HOURS,
                                 'hold_hours': ceil(duration.seconds / 3600),
                                 'heat_hold_temp': target,
                                 'cool_hold_temp': target + (mode.value * 2)})
        if resp.status.code != 0:
            debug('Failed to start the thermostat')
            debug(resp.pretty_format())
            return
        self.cache.pop('events', None)
        self._started_at = datetime.now()

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def stop(self):
        resp = self.__attempt('resume_program', resume_all=False)
        if resp.status.code != 0:
            debug('Failed to stop the thermostat')
            debug(resp.pretty_format())
            return
        self.cache.pop('events', None)
        self._started_at = None
        self._stopped_at = datetime.now()

    @property
    def hvac_mode(self):
        '''Current HVAC mode.'''
        mode = self._load('settings').hvac_mode
        if mode == 'off':
            return None
        return getattr(Mode, self._load('settings').hvac_mode.upper())

    @hvac_mode.setter
    def hvac_mode(self, mode):
        settings = self._load('settings')
        settings.hvac_mode = mode.name.lower()
        self._update('settings', settings)

    @Pyro5.api.expose
    def is_runnable(self):
        runnable_at = self._stopped_at + \
            timedelta(seconds=self.settings.min_pause)
        if datetime.now() < runnable_at \
           or not self.hvac_mode \
           or self._estimate_runtime() < self.min_run_time:
            return False
        return True

    def _is_on_hold(self):
        for event in self._load('events'):
            if event.type == 'hold' and event.running:
                return True
        return False

    @Pyro5.api.expose
    def is_running(self):
        status = self._load('equipment_status')
        return status not in ['', 'fan'] or self._is_on_hold()

    def _has_been_running_for(self):
        if self.is_running():
            if not self._started_at:
                self._started_at = datetime.now()
            return datetime.now() - self._started_at
        return timedelta()

    @Pyro5.api.expose
    def is_stoppable(self):
        if self._has_been_running_for() > self.min_run_time:
            return self._is_on_hold()
        return False

    @Pyro5.api.expose
    def meet_running_criteria(self, ratio, power=0):
        debug('meet_running_criteria(%.3f, %.3f)' % (ratio, power))
        if self.priority == Priority.URGENT:
            return True
        if self.is_running():
            mode = self.hvac_mode
            if self._deviation() * mode.value > 0:
                debug('Target has been reached')
                return False
            if self._has_been_running_for() > self.min_run_time:
                return power > 0 \
                    and ratio >= min(1, .9 * self.param.max_available_power / power) \
                    and power > self.power * 1/3
            return True
        return ratio >= min(1, .95 * self.param.max_available_power / self.power)

    @Pyro5.api.expose
    @property
    def desc(self):
        return 'HVAC(%s, %.1f)' % (self.priority.name, self.indoor_temp)

    def adjust_priority(self):
        '''Adjust the priority based on the estimate run time.'''
        run_time = self._estimate_runtime()
        if run_time < self.min_run_time:
            self.priority = min(Priority)
            return
        count = (self.param.target_time - datetime.now()) / run_time
        priority_levels = max(Priority) - min(Priority) + 1
        if count > priority_levels or count < 0:
            self.priority = min(Priority)
        else:
            self.priority = Priority(max(Priority) - floor(count))

    def adjust_power(self):
        '''Update the power necessary to run HVAC system.'''
        self.power = self.model.power(self.param.outdoor_temp)

    @Pyro5.api.expose
    def read(self, **kwargs):
        if 'temperatures' not in self.cache:
            sensors = self._load('sensors', 'remote_sensors')
            temperatures = {s.name:int(s.capability[0].value) / 10 \
                            for s in sensors}
            self.cache['temperatures'] = temperatures
        return self.cache.get('temperatures', {})

    @property
    def indoor_temp(self):
        '''Current indoor temperature.'''
        try:
            return self.read()[self.settings.temperature_sensor]
        except KeyError as err:
            raise RuntimeError('%s temperature is not available'
                               % self.settings.temperature_sensor) from err

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {k:'°F' for k in self.read().keys()}

    def __attempt(self, method, *args, **kwargs):
        for _ in range(2):
            try:
                return getattr(self.ecobee, method)(*args, **kwargs)
            except pyecobee.exceptions.EcobeeApiException:
                self.ecobee.refresh_tokens()
            except requests.exceptions.RequestException:
                log_exception('Communication with the server failed',
                              *sys.exc_info())
        raise RuntimeError('%s(%s, %s) call failed' % (method, args, kwargs))

    def _load(self, information, field=None):
        '''Load 'information' from the Ecobee server.

        If the 'information' is still in still in the Time To Live cache, it
        returns the value from the cache.

        '''
        data = self.cache.get(information, None)
        if data:
            return data
        if not field:
            field = information
        kwargs = {'include_' + information: True}
        sel = Selection(SelectionType.REGISTERED.value, '', **kwargs)
        thermostats = self.__attempt('request_thermostats', sel)
        if thermostats in ('unknown', None):
            raise RuntimeError('Could not find the thermostat')
        try:
            thermostat = next(t for t in thermostats.thermostat_list \
                              if int(t.identifier) == self.device_id)
        except StopIteration as err:
            raise RuntimeError('Could not find the thermostat') from err
        self.cache[information] = getattr(thermostat, field)
        return self.cache[information]

    def _update(self, information, value, field=None):
        '''Set the 'information' on the Ecobee server.'''
        if not field:
            field = information
        sel = Selection(SelectionType.REGISTERED.value, '',
                        **{'include_' + information: True})
        thermostat = Thermostat(identifier=self.device_id, **{field: value})
        self.__attempt('update_thermostats', sel, thermostat=thermostat)
        self.cache.pop(information, None)

def get_ecobee():
    '''Load the ecobee service object from the storage.'''
    try:
        with get_storage() as storage:
            ecobee = storage['MyEcobee']
        if ecobee.authorization_token is None or \
           ecobee.access_token is None:
            raise ValueError()
    except (KeyError, ValueError):
        debug('Ecobee authentication data not present.')
        sys.exit(os.EX_DATAERR)

    if datetime.now(pytz.utc) >= ecobee.access_token_expires_on:
        ecobee.refresh_tokens()
        with get_storage() as storage:
            storage['MyEcobee'] = ecobee

    return ecobee

def register(name, uri, raise_exception=True):
    '''Register 'task' as sensor and task.'''
    try:
        for qualifier in ['sensor', 'task']:
            NameServer().register(qualifier, name, uri)
    except RuntimeError as err:
        log_exception('Failed to register as %s' % qualifier, *sys.exc_info())
        if raise_exception:
            raise err

class HVACParam(threading.Thread):
    '''This class provides information to the HVAC task.

    This class is a thread because some of the information can take several
    seconds to collect or compute. This class provide information such as the
    maximum available power to expect from the energy production system, the
    current outdoor temperature and the target time and temperature.

    The target time is defined as the point in time when the energy production
    system produces enough power for the HVAC system to run. The target
    temperature is the temperature the home should be at target time so that
    the temperature is going to be as close as possible to 'goal_temperature'
    at goal time.

    '''
    def __init__(self, weather, power_simulator, settings):
        super().__init__()
        self.weather = weather
        self.power_simulator = power_simulator
        self.settings = settings
        self._lock = threading.Lock()
        self._data = {}
        self._updated = True

    @property
    def max_available_power(self):
        '''Maximum power that should be available to operate the HVAC.'''
        with self._lock:
            return self._data['max_available_power']

    @property
    def outdoor_temp(self):
        '''Current outdoor temperature.'''
        with self._lock:
            return self._data['outdoor_temp']
    @property
    def target_time(self):
        '''Last point in time when the system will produce enough power.'''
        with self._lock:
            return self._data['target_time']
    @property
    def target_temp(self):
        '''Desired temperature at 'target_time'.'''
        with self._lock:
            return self._data['target_temp']

    def _update_max_available_power(self):
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0)
        try:
            available = self.power_simulator.max_available_power_at(tomorrow)
            available -= 0.0001
            with self._lock:
                self._data['max_available_power'] = available
            debug('max_available_power updated to %s' % available)
        except (RuntimeError, Pyro5.errors.PyroError):
            log_exception('Max power update failed', *sys.exc_info())

    def _update_target_time(self):
        power = self.max_available_power
        model = HVACModel()
        try:
            while True:
                _, target_time = self.power_simulator.next_power_window(power)
                target_time = parser.parse(target_time)
                temp_at_target = self.weather.temperature_at(target_time)
                hvac_power = model.power(temp_at_target)
                if hvac_power >= power:
                    with self._lock:
                        self._data['target_time'] = target_time
                        debug('Target time updated to %s' % target_time)
                        debug('Power at target time is %s' % hvac_power)
                    break
                debug('new power is %s' % hvac_power)
                power = hvac_power
        except (RuntimeError, Pyro5.errors.PyroError):
            log_exception('Target time update failed', *sys.exc_info())

    def _update_target_temperature(self):
        if datetime.now() >= self.target_time:
            return
        goal_time = datetime.combine(self.target_time.date(),
                                     self.settings.goal_time)
        target_temp = self.settings.goal_temperature
        model = HomeModel()
        try:
            minutes = int((goal_time - self.target_time).total_seconds() / 60)
            for minute in range(minutes):
                time = self.target_time + timedelta(minutes=minute)
                temp_at = self.weather.temperature_at(time)
                target_temp -= model.degree_per_minute(temp_at)
            with self._lock:
                if target_temp > self.settings.comfort_zone[1]:
                    self._data['target_temp'] = self.settings.comfort_zone[1]
                elif target_temp < self.settings.comfort_zone[0]:
                    self._data['target_temp'] = self.settings.comfort_zone[0]
                else:
                    self._data['target_temp'] = target_temp
            debug('Target temperature updated to %s' % target_temp)
        except (RuntimeError, Pyro5.errors.PyroError):
            log_exception('Target temperature update failed', *sys.exc_info())

    def is_ready(self):
        '''Return true if all this object is ready to be used.'''
        return len(self._data) == 4

    def run(self):
        while True:
            try:
                target_time = self.target_time
            except KeyError:
                target_time = datetime.min
            if datetime.now() > target_time:
                try:
                    self._update_max_available_power()
                    self._update_target_time()
                except (RuntimeError, Pyro5.errors.PyroError):
                    log_exception('Parameters update failed', *sys.exc_info())
            try:
                temperature = self.weather.temperature
            except (RuntimeError, Pyro5.errors.PyroError):
                log_exception('Temperature update failed', *sys.exc_info())
            with self._lock:
                self._data['outdoor_temp'] = temperature
            try:
                self._update_target_temperature()
            except (RuntimeError, Pyro5.errors.PyroError):
                log_exception('Uncaught exception in run()',  *sys.exc_info())
                debug(''.join(Pyro5.errors.get_pyro_traceback()))
            sleep(60)

def my_excepthook(etype, value=None, traceback=None):
    '''On uncaught exception, log the exception and kill the process.'''
    if value:
        args = (etype, value, traceback)
    else:
        args = sys.exc_info()
    log_exception('Uncaught exeption', *args)
    os.kill(os.getpid(), signal.SIGTERM)
    #
sys.excepthook = my_excepthook
threading.excepthook = my_excepthook

def main():
    '''Start and register an HVAC Task and Sensor.'''
    # pylint: disable=too-many-locals
    base = os.path.splitext(__file__)[0]
    module_name = os.path.basename(base)
    config = init(base + '.log')['Ecobee']
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    ecobee = get_ecobee()
    device_id = int(config['device_id'])
    sel = Selection(SelectionType.REGISTERED.value, '')
    thermostats = ecobee.request_thermostats(sel).thermostat_list
    thermostat = next(t for t in thermostats if int(t.identifier) == device_id)
    if not thermostat:
        debug('%d device does not exist' % device_id)
        sys.exit(os.EX_DATAERR)

    param = HVACParam(WeatherProxy(timeout=3), PowerSimulatorProxy(), settings)
    param.start()
    while not param.is_ready():
        sleep(1)

    Pyro5.config.COMMTIMEOUT = 10
    task = HVACTask(ecobee, device_id, settings, param)
    daemon = Pyro5.api.Daemon()
    uri = daemon.register(task)
    register(module_name, uri, raise_exception=True)

    scheduler = SchedulerProxy()
    watchdog = WatchdogProxy()
    monitor = MonitorProxy()
    debug("... is now ready to run")
    while True:
        settings.load()

        try:
            task.adjust_power()
            task.adjust_priority()
        except (ValueError, RuntimeError) as err:
            debug(str(err))

        watchdog.register(os.getpid(), module_name)
        watchdog.kick(os.getpid())
        register(module_name, uri, raise_exception=False)

        try:
            task.read()
            monitor.track('ecobee service', True)
            scheduler.register_task(uri)
        except RuntimeError:
            debug('Self-test failed, unregister from the scheduler')
            try:
                scheduler.unregister_task(uri)
            except RuntimeError:
                pass
            monitor.track('ecobee service', False)

        while True:
            now = datetime.now()
            timeout = 60 - (now.second + now.microsecond/1000000.0)
            next_cycle = now + timedelta(seconds=timeout)
            sockets, _, _ = select(daemon.sockets, [], [])
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break

if __name__ == "__main__":
    main()
