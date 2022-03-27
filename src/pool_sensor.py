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

'''This module implements a pool temperature sensor based on a WirelessTags
device.

'''

import os
import sys
from datetime import datetime, timedelta
from select import select

import Pyro5.api
from wirelesstagpy import WirelessTags

from monitor import MonitorProxy
from sensor import Sensor
from tools import (NameServer, Settings, debug, fahrenheit, init,
                   log_exception, my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'max_loop_duration': 20}

class TemperatureSensor(Sensor):
    '''WirelessTags temperature sensor.'''
    def __init__(self, api, uuid):
        self._uuid = uuid
        self._temperature = self._tags_to_temperature(api.load_tags())
        debug('Temperature at init: %.2f°F' % self._temperature)
        self._latest_update = datetime.now()
        self._api = api
        def update(tags, events):
            return self._update(tags, events)
        api.start_monitoring(update)

    def __del__(self):
        self._api.stop_monitoring()

    def _tags_to_temperature(self, spec):
        return fahrenheit(spec[self._uuid].temperature)

    def _update(self, tags, events):
        del events
        if self._uuid in tags \
           and self._temperature != self._tags_to_temperature(tags):
            self._temperature = self._tags_to_temperature(tags)
            self._latest_update = datetime.now()
            debug('Temperature update: %.2f°F' % self._temperature)

    @Pyro5.api.expose
    def read(self, **kwargs):
        if datetime.now() > self._latest_update + timedelta(hours=8):
            raise RuntimeError('Outdated data')
        return {'temperature': self._temperature}

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {key:'°F' for key in self.read()}

def main():
    '''Register and run the pool temperature Sensor.'''
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')['WirelessTags']
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    sensor = TemperatureSensor(WirelessTags(config['login'],
                                            config['password']),
                               config['uuid'])
    daemon = Pyro5.api.Daemon()
    uri = daemon.register(sensor)

    watchdog = WatchdogProxy()
    monitor = MonitorProxy()
    monitor.track('pool sensor operational', True)
    debug("... is now ready to run")
    while True:
        settings.load()

        watchdog.register(os.getpid(), 'pool_sensor')
        watchdog.kick(os.getpid())

        if datetime.now() > sensor._latest_update + timedelta(hours=8):
            debug('No update in eight hours, recreate the sensor object')
            new_sensor = TemperatureSensor(WirelessTags(config['login'],
                                                        config['password']),
                                           config['uuid'])
            if sensor._temperature == new_sensor._temperature:
                debug('New sensor temperature is the same.')
                monitor.track('pool sensor operational', False)
            sensor = new_sensor
            uri = daemon.register(sensor)

        try:
            NameServer().register_sensor('pool', uri)
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
