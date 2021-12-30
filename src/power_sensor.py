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

'''This module implements a power usage sensor based on the Emporia Vue Gen2
device.

'''

import os
import sys
from datetime import datetime, timedelta
from enum import Enum
from select import select
from tempfile import mkdtemp
from time import sleep

import botocore
import Pyro5.api
import requests
from pyemvue import PyEmVue
from pyemvue.device import VueDevice
from pyemvue.enums import Scale, Unit

from sensor import Sensor
from tools import NameServer, Settings, debug, init, log_exception
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'delay_on_time_unit': 0.8,
                    'attempt_delay': 0.5,
                    'max_loop_duration': 20,
                    'max_identical': 3}

class RecordScale(Enum):
    '''Task priority levels.'''
    SECOND = Scale.SECOND.value
    MINUTE = Scale.MINUTE.value
    DAY = Scale.DAY.value

SUPPORTED_SCALES = {s:s.name.lower() for s in RecordScale}

class CacheEntry:
    '''Represent a cached information for a particular scale.

    The cached information is considered expired when the scale time unit has
    rolled over. For instance, a RecordScale.SECOND.value cache entry expires on each
    second, a RecordScale.MINUTE cache entry expires on each new minute ...

    '''
    def __init__(self, scale):
        if scale not in SUPPORTED_SCALES.keys():
            raise ValueError('%s is not a supported scale' % scale)
        self.scale = scale
        self.expiration = None
        self.identical = 0
        self._value = None

    # pylint: disable=inconsistent-return-statements
    def __expire_at(self) -> datetime:
        now = datetime.now()
        kwargs = {'microsecond': 0}
        if self.scale == RecordScale.SECOND:
            return (now + timedelta(seconds=1)).replace(**kwargs)
        kwargs['second'] = 0
        return (now + timedelta(minutes=1)).replace(**kwargs)

    @property
    def value(self) -> dict:
        '''Return the current cache entry stored value.'''
        return self._value

    @value.setter
    def value(self, value: dict) -> None:
        if self.value == value:
            self.identical += 1
        else:
            self.identical = 0
        self.expiration = self.__expire_at()
        self._value = value

    def has_expired(self) -> bool:
        '''Return True if the entry value has expired.'''
        return not self.expiration or datetime.now() > self.expiration

class PowerSensor(Sensor):
    '''This Sensor class implementation provides power consumption readings.

    '''
    # pylint: disable=too-few-public-methods
    def __init__(self, vue: PyEmVue, device: VueDevice, device_map: list,
                 settings: Settings):
        self.vue = vue
        self.device = device
        self.device_map = device_map
        self.settings = settings
        self.cache = {scale:CacheEntry(scale) for scale in RecordScale}

    def __get_device_list_usage(self, scale: Scale) -> dict:
        for attempt in ['first', 'final']:
            for inner_attempt in ['first', 'second', 'final']:
                try:
                    gids = [self.device.device_gid]
                    return self.vue.get_device_list_usage(gids, None,
                                                          scale=scale.value,
                                                          unit=Unit.KWH.value)
                except requests.exceptions.RequestException:
                    log_exception('%s: Devices usage read failed on %s attempt'
                                  % (scale, inner_attempt), *sys.exc_info())
                    if inner_attempt != 'final':
                        sleep(self.settings.attempt_delay)
            if attempt != 'final':
                debug('%s: Try re-login in' % scale)
                self.vue.login(token_storage_file=self.vue.token_storage_file)
        raise RuntimeError('Could not read the device list usage')

    def __parse(self, usage) -> dict:
        result = {}
        for device in usage.values():
            for channel in device.channels.values():
                if channel.name not in [ 'TotalUsage', 'Balance' ]:
                    result[channel.name] = channel.usage
                if channel.nested_devices:
                    result.update(self.__parse(channel.nested_devices))
        return result

    def __load(self, scale):
        usage = {}
        for attempt in ['first', 'final']:
            usage = self.__parse(self.__get_device_list_usage(scale))
            if len(self.device_map) <= len(usage):
                return usage
            if attempt != 'final':
                sleep(self.settings.attempt_delay)
        raise RuntimeError('Could not load a valid usage measurements, %s'
                           % usage)

    def __convert(self, usage: dict, scale: Scale) -> dict:
        factor={RecordScale.SECOND: 60 * 60,
                RecordScale.MINUTE: 60,
                RecordScale.DAY: 1}[scale]
        device_map = self.device_map.copy()
        if scale == RecordScale.DAY:
            device_map.insert(1, 'to grid')
            device_map.insert(1, 'from grid')
        return {k:v * factor for k, (_, v) in zip(device_map, usage.items())}

    @Pyro5.api.expose
    def read(self, **kwargs: dict) -> dict:
        '''Return an instant record from the sensor.

        The optional SCALE keyword argument, limited to
        RecordScale.SECOND, RecordScale.MINUTE,
        RecordScale.HOUR and RecordScale.DAY, indicates which time
        unit resolution can be supplied to read with a different scale
        order. By default, the resolution is RecordScale.MINUTE.

        '''
        scale = RecordScale(kwargs.get('scale', RecordScale.MINUTE))
        if scale not in self.cache.keys():
            raise ValueError('%s is not a supported scale' % scale)
        if not self.cache[scale].has_expired():
            if scale == RecordScale.DAY:
                debug('from cache: %s' % self.cache[scale].value)
            return self.cache[scale].value
        for attempt in [ 'first', 'second', 'final']:
            if scale != RecordScale.SECOND:
                # It can take a little while before the latest completed time
                # unit data is available on the server. To prevent pulling more
                # often than necessary, we make sure that 500 ms have elapsed
                # since the time unit has completed.
                now = datetime.now()
                delay_on_time_unit = self.settings.delay_on_time_unit
                if now.second == 0 and delay_on_time_unit > 0 and \
                   now.microsecond < delay_on_time_unit * 1000000:
                    delay = delay_on_time_unit - now.microsecond / 1000000
                    debug('%s: A little bit early, delay by %.3fs'
                          % (scale, delay))
                    sleep(delay)
            try:
                raw = self.__load(scale)
            except (requests.exceptions.RequestException,
                    botocore.exceptions.BotoCoreError) as err:
                msg = '%s: Failed to load sensor data from the server' % scale
                log_exception(msg, *sys.exc_info())
                raise RuntimeError(msg) from err
            if scale == RecordScale.DAY:
                debug('Raw: %s' % raw)
            usage = self.__convert(raw, scale)
            # We successfully loaded a record from the server. Unfortunately,
            # sometimes this new record is actually the one. Most likely
            # because the server has not received the latest data from the
            # sensor or was not done processing them.
            if usage == self.cache[scale].value:
                debug('%s: Identical record on %s attempt' % (scale, attempt))
                if attempt != 'final':
                    delay = self.settings.attempt_delay
                    if delay > 0:
                        debug("Let's retry in %.3fs" % delay)
                        sleep(delay)
                    else:
                        debug("Let's retry")
                    continue
            self.cache[scale].value = usage
            if self.cache[scale].identical > self.settings.max_identical:
                raise RuntimeError('Too many identical record in a row for %s'
                                   % scale)
            if scale == RecordScale.DAY:
                debug('brand new: %s' % self.cache[scale].value)
            return usage

    @Pyro5.api.expose
    def units(self, **kwargs: dict) -> dict:
        scale = RecordScale(kwargs.get('scale', RecordScale.MINUTE))
        if scale not in self.cache.keys():
            raise ValueError('%s is not a supported scale' % scale)
        record = self.cache[scale].value
        if not record:
            record = self.read(scale=scale)
        return {k:'kWh' if scale == RecordScale.DAY else 'kW' \
                for k, v in record.items()}

# pylint: disable=missing-docstring
def main():
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')['Emporia']
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    vue = PyEmVue()
    vue.login(username=config["login"], password=config["password"],
              token_storage_file=os.path.join(mkdtemp(), 'emporia.json'))
    devices = vue.get_devices()
    if len(devices) == 0:
        debug('No devices associated to this account')
        return
    sensor = None
    for device in devices:
        if device.device_gid == int(config['device_id']) and \
           device.channels:
            sensor = PowerSensor(vue, device, config['map'].split(","),
                                 settings)
            break
    if not sensor:
        debug('Could not find %d device' % config['device_id'])
        return

    daemon = Pyro5.api.Daemon()
    uri = daemon.register(sensor)

    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        settings.load()

        watchdog.register(os.getpid(), 'power_sensor')
        watchdog.kick(os.getpid())

        try:
            NameServer().register_sensor('power', uri)
        except RuntimeError:
            log_exception('Failed to register the sensor',
                          *sys.exc_info())

        sockets, _, _ = select(daemon.sockets, [], [],
                               # pylint: disable=maybe-no-member
                               settings.max_loop_duration)
        if sockets:
            daemon.events(sockets)

if __name__ == "__main__":
    main()
