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

import os
import sys
from select import select

import Pyro5.api

from sensor import Sensor
from tools import NameServer, Settings, debug, init, log_exception

DEFAULT_SETTINGS = {'max_loop_duration': 5}
MODULE_NAME = 'monitor'

class Monitor(Sensor):
    def __init__(self):
        self._states = {}

    @Pyro5.api.expose
    def track(self, name, state):
        '''Update or start tracking "name" with current value "state"'''
        if not isinstance(state, bool):
            raise TypeError('state must be a boolean')
        self._states[name] = state

    @Pyro5.api.expose
    def read(self, **kwargs):
        return self._states

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {key:'binary' for key, _ in self._states.items()}

class MonitorProxy:
    '''Helper class for monitor service users.

    This class is a wrapper with exception handler of the monitor service. It
    provides convenience for modules using the monitor by suppressing the
    burden of locating the monitor and handling the various remote object
    related errors.

    '''
    def __init__(self, max_attempt=2):
        self._monitor = None
        self.max_attempt = max_attempt

    def track(self, *args):
        for _ in range(self.max_attempt):
            if not self._monitor:
                try:
                    self._monitor = NameServer().locate_service(MODULE_NAME)
                except Pyro5.errors.NamingError:
                    log_exception('Failed to locate the monitor',
                                  *sys.exc_info())
                except Pyro5.errors.CommunicationError:
                    log_exception('Cannot communicate with the nameserver',
                                  *sys.exc_info())
            if self._monitor:
                try:
                    self._monitor.track(*args)
                except Pyro5.errors.PyroError:
                    log_exception('Communication failed with the monitor',
                                  *sys.exc_info())
                    self._monitor = None

def main():
    # pylint: disable=too-many-locals
    base = os.path.splitext(__file__)[0]
    init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    Pyro5.config.MAX_RETRIES = 3
    daemon = Pyro5.api.Daemon()
    nameserver = NameServer()
    uri = daemon.register(Monitor())
    nameserver.register_sensor(MODULE_NAME, uri)
    nameserver.register_service(MODULE_NAME, uri)

    debug("... is now ready to run")
    while True:
        try:
            nameserver.register_sensor(MODULE_NAME, uri)
            nameserver.register_service(MODULE_NAME, uri)
        except RuntimeError:
            log_exception('Failed to register the watchdog service',
                          *sys.exc_info())

        sockets, _, _ = select(daemon.sockets, [], [],
                               # pylint: disable=maybe-no-member
                       settings.max_loop_duration)
        if sockets:
            daemon.events(sockets)

if __name__ == "__main__":
    main()
