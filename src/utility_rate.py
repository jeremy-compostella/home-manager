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
'''This module implements a water heater task based on the Aquanta device.'''

import os
import select
import sys
from datetime import datetime, timedelta
from enum import IntEnum
from select import select

import Pyro5.api
from dateutil import parser

from sensor import Sensor
from tools import (NameServer, Settings, debug, init, log_exception,
                   my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'export': 0.0281,
                    'rates': ['onpeak', 'offpeak'],
                    'on_peak_rates': [0.0951, 0.0951, 0.0951, 0.0951,
	                              0.2094, 0.2094, 0.2409, 0.2409,
	                              0.2094, 0.2094, 0.0951, 0.0951],
                    'off_peak_rates': [0.0691, 0.0691, 0.0691, 0.0691,
	                               0.0727, 0.0727, 0.073, 0.073,
	                               0.0727, 0.0727, 0.0691, 0.0691],
                    'seasons': ['winter', 'winter', 'winter', 'winter',
       	                        'summer', 'summer', 'summer', 'summer',
       	                        'summer', 'summer', 'winter', 'winter'],
                    'on_peak_schedule': {'winter': [[ 5, 9 ], [ 17, 20 ]],
                                         'summer': [[ 14, 20 ]]}}

class UtilityRateSensor(Sensor):
    WEEKDAYS = IntEnum('Weekdays', 'mon tue wed thu fri sat sun', start=0)

    def __init__(self, settings):
        self.settings = settings

    def rate(self, date):
        if date.weekday() in [ self.WEEKDAYS.sat, self.WEEKDAYS.sun ]:
            rate_category = 'off_peak'
        else:
            rate_category = 'off_peak'
            season = self.settings.seasons[date.month - 1]
            on_peak_schedule = self.settings.on_peak_schedule[season]
            for schedule in on_peak_schedule:
                if schedule[0] <= date.hour <= schedule[1]:
                    rate_category = 'on_peak'
                    break
        return getattr(self.settings, rate_category + '_rates')[date.month - 1]

    @Pyro5.api.expose
    def read(self, **kwargs):
        date = kwargs.get('date', datetime.now())
        if isinstance(date, str):
            date = parser.parse(date)
        return {'from_grid': self.rate(date),
                'to_grid': self.settings.export}

    @Pyro5.api.expose
    def units(self, **kwargs):
        date = kwargs.get('date', datetime.now())
        if isinstance(date, str):
            date = parser.parse(date)
        return {k:'$/kWh' for k, _ in self.read().items()}

def register(name, uri, raise_exception=True):
    '''Register the sensor.'''
    try:
        NameServer().register_sensor(name, uri)
    except RuntimeError as err:
        log_exception('Failed to register as sensor', *sys.exc_info())
        if raise_exception:
            raise err

def main():
    '''Start and register a water heater Task and water heater Sensor.'''
    # pylint: disable=too-many-locals
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    module_name = os.path.basename(base)
    init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    sensor = UtilityRateSensor(settings)

    Pyro5.config.COMMTIMEOUT = 5
    daemon = Pyro5.api.Daemon()
    uri = daemon.register(sensor)

    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        settings.load()

        watchdog.register(os.getpid(), module_name)
        watchdog.kick(os.getpid())

        register(module_name, uri, raise_exception=False)

        while True:
            now = datetime.now()
            timeout = 60 - (now.second + now.microsecond/1000000.0)
            next_cycle = now + timedelta(seconds=timeout)
            sockets, _, _ = select(daemon.sockets, [], [], timeout)
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break

if __name__ == "__main__":
    main()
