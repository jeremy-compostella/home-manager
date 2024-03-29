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

'''This module implements a pool pump task on Migro switch.

'''
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from select import select
from statistics import mean

import Pyro5
import requests
from cachetools import TTLCache
from dateutil import parser
from scipy.interpolate import interp1d
from websocket import create_connection

from monitor import MonitorProxy
from power_simulator import PowerSimulatorProxy
from scheduler import Priority, SchedulerProxy, Task
from sensor import Sensor, SensorReader
from tools import (NameServer, Settings, debug, get_database, init,
                   log_exception, my_excepthook)
from watchdog import WatchdogProxy
from weather import WeatherProxy

DEFAULT_SETTINGS = {'power_sensor_key': 'pool',
                    'min_run_time': timedelta(minutes=7),
                    'power': 2,
                    'clean_filter_threshold': 1.55,
                    'priority_adjustment_frequency': 10}

MODULE_NAME = 'pool_pump'

class Ewelink:
    '''Helper class to communicate with the Ewelink server.'''

    APP_ID = 'R8Oq3y0eSZSYdKccHlrQzT1ACCOUT9Gv'
    APP_SECRET = b'1ve5Qk9GXfUhKAn1svnKwpAlxXkMarru'

    def __init__(self, login, password, timeout=5, region='us'):
        self._credentials = {'login': login, 'password': password}
        self._timeout = timeout
        self._info = \
            {'api': f'https://{region}-api.coolkit.cc:8080/api/',
             'dispatch': f'https://{region}-dispa.coolkit.cc:8080/dispatch/'}
        self._cache = TTLCache(1, timedelta(seconds=60), datetime.now)
        self._login()

    def _login(self):
        self._session = requests.Session()
        timestamp = int(time.time())
        app_details = {'email': self._credentials['login'],
                       'password': self._credentials['password'],
                       'version': '6',
                       'ts': timestamp,
                       'nonce': str(timestamp)[:8],
                       'appid': self.APP_ID}
        hex_dig = hmac.new(self.APP_SECRET,
                           str.encode(json.dumps(app_details)),
                           digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(hex_dig).decode()
        self._headers = {'Authorization': 'Sign ' + sign,
                         'Content-Type': 'application/json;charset=UTF-8'}
        resp = self._post('api', 'user/login', app_details)
        if 'error' in resp:
            raise RuntimeError('Ewelink: login error')
        self._info.update({'token': resp['at'],
                           'apikey': resp['user']['apikey']})
        self._headers.update({'Authorization' : 'Bearer ' +
                              self._info['token']})

    def _post(self, subdomain, path, data=None):
        '''POST HTTP request for Ewelink PATH with DATA.'''
        resp = self._session.post(self._info[subdomain] + path,
                                  headers=self._headers,
                                  json=data, timeout=self._timeout)
        if not resp.ok:
            print(resp)
            raise RuntimeError(f'Ewelink: Failed to POST {path}')
        return resp.json()

    def _get(self, path, params):
        '''GET HTTP request for Ewelink PATH with PARAMS.'''
        for _ in range(2):
            resp = self._session.get(self._info['api'] + path, params=params,
                                     headers=self._headers,
                                     timeout=self._timeout)
            if not resp.ok:
                raise RuntimeError(f'Ewelink: Failed to GET {path}')
            res = resp.json()
            if res.get('error', None) == 406:
                debug('GET error, logging-in...')
                self._login()
        return resp.json()

    def _devices(self):
        '''Get the list of Ewelink devices.'''
        cache = self._cache.get('devices', None)
        if cache is not None:
            return cache
        timestamp = int(time.time())
        devices = self._get('user/device',
                            {'appid': self.APP_ID,
                             'nonce': str(timestamp)[:8],
                             'ts': timestamp,
                             'version': 8,
                             'getTags': 1})['devicelist']
        self._cache['devices'] = {device['deviceid']:device \
                                  for device in devices}
        return self._cache['devices']

    def __len__(self):
        return len(self._devices())

    def __contains__(self, key):
        return key in self._devices().keys()

    def __iter__(self):
        return iter(self._devices().items())

    def __getitem__(self, key):
        return self._devices()[key]

    def __setitem__(self, key, value):
        payload = {'action': 'update',
                   'params': value,
                   'controlType': 4,
                   'deviceid': key}
        self._update(payload)

    def _base_payload(self):
        timestamp = int(time.time())
        return {'userAgent': 'app',
                'apikey': self._info['apikey'],
                'sequence': str(timestamp),
                'ts': timestamp}

    def _websocket(self):
        if self._info.get('domain', None) is None:
            resp = self._post('dispatch', 'app')
            if 'domain' not in resp:
                raise RuntimeError('Ewelink: Failed to read websocket domain')
            self._info['domain'] = resp['domain']
        websocket = create_connection(f'wss://{self._info["domain"]}:8080/api/ws',
                                      timeout=self._timeout)
        timestamp = int(time.time())
        payload = self._base_payload()
        payload.update({'action': 'userOnline',
                        'version': 6,
                        'nonce': str(timestamp)[:8],
                        'at': self._info['token']})
        websocket.send(json.dumps(payload))
        if json.loads(websocket.recv())['error'] != 0:
            websocket.close()
            raise RuntimeError('Ewelink: Failed to establish websocket')
        return websocket

    def _update(self, payload):
        '''Update status with PAYLOAD.'''
        websocket = self._websocket()
        payload.update(self._base_payload())
        websocket.send(json.dumps(payload))
        resp = websocket.recv()
        websocket.close()
        self._cache.pop('devices', None)
        if json.loads(resp)['error'] != 0:
            raise RuntimeError(f'Ewelink: action {payload["action"]} failed')

class PoolPump(Task, Sensor):
    '''This task uses a Migro switch to control a pool pump. '''
    # pylint: disable=too-many-instance-attributes
    def __init__(self, device_id, ewelink, settings):
        Task.__init__(self, Priority.LOW, keys=[settings.power_sensor_key])
        self._id = device_id
        self._ewelink = ewelink
        self._settings = settings
        self.healthy = True
        self.filter_is_clean = True
        self._powers = []
        self.started_at = None
        self.target_time = datetime.min
        self.remaining_runtime = timedelta()
        self.last_update = datetime.now()
        self.update_remaining_runtime()

    def update_remaining_runtime(self):
        '''Update the remaining runtime counter.'''
        now = datetime.now()
        if self.is_running():
            if not self.started_at:
                self.started_at = datetime.now()
            self.remaining_runtime -= now - max(self.last_update,
                                                self.started_at)
        if self.remaining_runtime < timedelta():
            self.remaining_runtime = timedelta()
        debug(f'Remaining runtime: {self.remaining_runtime}')
        self.last_update = now

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def start(self):
        debug('Starting')
        self._ewelink[self._id] = {'switch': 'on'}
        self.started_at = datetime.now()
        if self.healthy is False:
            debug('Mark healthy in an attempt to recover on start')
            self.healthy = True
        self._powers = []

    @Pyro5.api.expose
    @Pyro5.api.oneway
    def stop(self):
        debug('Stopping')
        self.started_at = None
        self._ewelink[self._id] = {'switch': 'off'}
        debug('Stopped')

    @Pyro5.api.expose
    def is_running(self):
        try:
            device = self._ewelink[self._id]
        except KeyError:
            #pylint: disable=raise-missing-from
            raise RuntimeError(f'Ewelink: could not find {self._id} device')
        return device['params']['switch'] == 'on'

    def has_been_running_for(self):
        '''Return the time the pool pump has been running.'''
        if self.is_running():
            # Handle the situation where it has been started without using the
            # start() method.
            if not self.started_at:
                self.started_at = datetime.now()
            return datetime.now() - self.started_at
        return timedelta()

    @Pyro5.api.expose
    def is_stoppable(self):
        return self.has_been_running_for() > self._settings.min_run_time \
            and self._ewelink[self._id]['online']

    @Pyro5.api.expose
    def is_runnable(self):
        return self.remaining_runtime > timedelta() \
            and self._ewelink[self._id]['online']

    @Pyro5.api.expose
    def meet_running_criteria(self, ratio, power=0) -> bool:
        debug(f'meet_running_criteria({ratio:.3f}, {power:.3f})')
        if self.has_been_running_for() > timedelta(minutes=2):
            self.healthy = power > .2
            self._powers.append(power)
            self.filter_is_clean = \
                mean(self._powers) > self._settings.clean_filter_threshold
        return self.is_runnable() and ratio >= .9

    @property
    @Pyro5.api.expose
    def desc(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.priority.name,
                               self.remaining_runtime)

    @property
    @Pyro5.api.expose
    def power(self):
        return max(self._powers) if self._powers else self._settings.power

    @Pyro5.api.expose
    def read(self, **kwargs):
        return {'remaining_runtime': int(self.remaining_runtime.seconds / 60)}

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {'remaining_runtime': 'minutes'}

    def adjust_priority(self):
        '''Update the priority according to the target time'''
        now = datetime.now()
        if now < self.target_time - self.remaining_runtime * 1.5 \
           or self.remaining_runtime == timedelta():
            self.priority = Priority.LOW
        elif now < self.target_time - self.remaining_runtime:
            self.priority = Priority.MEDIUM
        else:
            self.priority = Priority.HIGH

def already_ran_today_for(min_power = .5):
    '''Return how long the pool pump has been running today based.

    It uses the database power table.'''
    with get_database() as database:
        minutes = 0
        req = f'SELECT {DEFAULT_SETTINGS["power_sensor_key"]} FROM power '
        req += 'WHERE timestamp >= \'%s\'' \
            % datetime.now().strftime('%Y-%m-%d 00:00:00')
        cursor = database.cursor()
        cursor.execute(req)
        for (power,) in cursor:
            if power > min_power:
                minutes += 1
        return timedelta(minutes=minutes)

def configure_cycle(task, power_simulator, weather, pool_sensor):
    '''Compute and set the current cycle target time and runtime.'''
    fun = interp1d([52, 75], [60, 5 * 60], fill_value=(60, 5 * 60),
                       bounds_error=False)
    try:
        _, target_time = power_simulator.next_power_window(task.power)
        target_time = parser.parse(target_time)
        try:
            temp = pool_sensor.read()['temperature']
        except RuntimeError:
            temp = weather.minimum_temperature()
        remaining_runtime = timedelta(minutes=round(fun(temp).item()))
        if datetime.now().date() == target_time.date():
            remaining_runtime -= already_ran_today_for(task.power / 4)
        task.remaining_runtime = remaining_runtime
        task.target_time = target_time
        debug(f'target_time updated to {task.target_time}')
        return True
    except (ValueError, RuntimeError, sqlite3.OperationalError) as err:
        debug(str(err))
        return False

def main():
    '''Register and run the pool pump task.'''
    # pylint: disable=too-many-locals,too-many-statements
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')['Ewelink']
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    ewelink = Ewelink(config['login'], config['password'])
    if config['device_id'] not in ewelink:
        raise RuntimeError(f'{config["device_id"]} device does not exist')
    task = PoolPump(config['device_id'], ewelink, settings)

    Pyro5.config.COMMTIMEOUT = 5
    daemon = Pyro5.api.Daemon()
    nameserver = NameServer()
    uri = daemon.register(task)
    nameserver.register_task(MODULE_NAME, uri)
    nameserver.register_sensor(MODULE_NAME, uri)

    scheduler = SchedulerProxy()
    watchdog = WatchdogProxy()
    power_simulator = PowerSimulatorProxy()
    weather = WeatherProxy(timeout=3)
    monitor = MonitorProxy()
    pool_sensor = SensorReader('pool')
    cycle_end = datetime.min
    cycle = -1
    debug('... is now ready to run')
    while True:
        settings.load()

        watchdog.register(os.getpid(), MODULE_NAME)
        watchdog.kick(os.getpid())

        if datetime.now() > cycle_end:
            if configure_cycle(task, power_simulator, weather, pool_sensor):
                cycle_end = datetime.combine(datetime.now().date(),
                                             dtime(hour=23, minute=59))

        try:
            task.update_remaining_runtime()
        except RuntimeError:
            log_exception('Could not update remaining runtime',
                          *sys.exc_info())

        try:
            nameserver.register_task(MODULE_NAME, uri)
            nameserver.register_sensor(MODULE_NAME, uri)
        except RuntimeError:
            log_exception('Failed to register the task and sensor',
                          *sys.exc_info())

        # Self-testing: on basic operation failure unregister from the
        # scheduler.
        try:
            task.is_running() # pylint: disable=pointless-statement
            monitor.track('ewelink service', True)
            if task.healthy:
                monitor.track('pool pump operational', True)
                scheduler.register_task(uri)
            else:
                monitor.track('pool pump operational', False)
                debug('Pool pump does not operate property, unregister...')
                scheduler.unregister_task(uri)
                task.stop()
        except RuntimeError:
            log_exception('Self-test failed, unregister from the scheduler',
                          *sys.exc_info())
            scheduler.unregister_task(uri)
            monitor.track('ewelink service', False)

        monitor.track('pool filter is clean', task.filter_is_clean)

        while True:
            now = datetime.now()
            timeout = 60 - (now.second + now.microsecond/1000000.0)
            next_cycle = now + timedelta(seconds=timeout)
            sockets, _, _ = select(daemon.sockets, [], [])
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break

        # pylint: disable=maybe-no-member
        if cycle == -1 \
           or cycle > settings.priority_adjustment_frequency:
            try:
                task.adjust_priority()
            except RuntimeError:
                log_exception('Could not adjust priority', *sys.exc_info())
            cycle = 0
        else:
            cycle += 1

if __name__ == '__main__':
    main()
