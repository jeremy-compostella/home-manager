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
from select import select

import Pyro5.api
from wirelesstagpy import WirelessTags

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
        def update(tags, events):
            return self._update(tags, events)
        api.start_monitoring(update)

    def _tags_to_temperature(self, spec):
        return fahrenheit(spec[self._uuid].temperature)

    def _update(self, tags, events):
        del events
        if self._uuid in tags:
            self._temperature = self._tags_to_temperature(tags)
            debug('Temperature update: %.2f°F' % self._temperature)

    @Pyro5.api.expose
    def read(self, **kwargs):
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
    debug("... is now ready to run")
    while True:
        settings.load()

        watchdog.register(os.getpid(), 'pool_sensor')
        watchdog.kick(os.getpid())

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
