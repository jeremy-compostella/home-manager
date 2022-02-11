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

'''This module implements a watchdog service processes.'''

import errno
import os
import signal
import sys
from abc import abstractmethod
from datetime import datetime, timedelta
from select import select
from time import sleep

import Pyro5.api

from monitor import MonitorProxy
from tools import NameServer, Settings, debug, init, log_exception

DEFAULT_SETTINGS = {'max_loop_duration': 5}
MODULE_NAME = 'watchdog'

class Process:
    '''Class representing a process (aka. a service).'''
    def __init__(self, name: str, pid: int, timeout: timedelta):
        self.name = name
        self.pid = pid
        self.timeout = timeout
        self.reset_timer()

    def reset_timer(self) -> None:
        '''Reset the timer.'''
        self.expiration_time = datetime.now() + self.timeout

    def timer_has_expired(self) -> bool:
        '''Return true if the timer has expired'''
        return datetime.now() > self.expiration_time

    def kill(self, signal_number) -> None:
        '''Send the signal_number signal to the process.'''
        os.kill(self.pid, signal_number)

    def is_alive(self) -> bool:
        '''Return True if the process is alive.'''
        try:
            self.kill(0)
            return True
        except OSError as err:
            if err.errno == errno.ESRCH:
                return False
            if err.errno == errno.EPERM:
                return True
            raise

    def __repr__(self):
        return '%s(%d)' % (self.name, self.pid)

class WatchdogInterface:
    '''Scheduler publicly available interface.'''

    @abstractmethod
    def register(self, pid: int, name: str, timeout: timedelta = None):
        '''Add a process to the list of monitored processes.

        Processes are identified by a PID and a NAME.  If the TIMEOUT argument
        is not set, a default timeout of 3 minutes timeout is used.

        '''

    @abstractmethod
    def unregister(self, pid: int) -> None:
        '''Unregister a process.'''

    @abstractmethod
    def kick(self, pid: int) -> None:
        '''Reset the watchdog timer of a particular process.'''

class Watchdog(WatchdogInterface):
    '''Watchdog class exposed as a pyro object.

    Processes register themselves using the Watchdog.register() method. Once
    they have registered, if they do not call the Watchdog.kick() method for a
    defined duration, the watchdog service kills them.

    '''
    def __init__(self, monitor):
        self._processes = {}
        self._monitor = monitor

    @property
    @Pyro5.api.expose
    def desc(self):
        '''List processes formatted as string.'''
        return ['%s' % process for process in self._processes.values()]

    @Pyro5.api.expose
    def register(self, pid: int, name: str, timeout: timedelta = None):
        if not timeout:
            timeout = timedelta(minutes=3)
        if pid not in self._processes:
            process = Process(name, pid, timeout)
            self._processes[pid] = process
            debug('Start monitoring %s' % process)

    @Pyro5.api.expose
    def unregister(self, pid: int) -> None:
        if pid in self._processes:
            debug('Stop monitoring %s' % self._processes[pid])
            del self._processes[pid]

    @Pyro5.api.expose
    def kick(self, pid: int) -> None:
        self._processes[pid].reset_timer()

    def monitor(self) -> None:
        '''Verify the monitored processes and report status to the monitor.

        If any process is missing, it is automatically removed from the list of
        registered processes.

        '''
        for process in self._processes.copy().values():
            alive = process.is_alive()
            try:
                self._monitor.track('process ' + process.name, alive)
            except RuntimeError:
                pass
            if not alive:
                debug('Process %s does not exist anymore' % process)
                self.unregister(process.pid)

    def kill_hung_processes(self) -> None:
        '''Kill processes which have not reset their watchdog timer in time.'''
        hung = [proc for proc in self._processes.values() \
                if proc.timer_has_expired()]
        for process in hung:
            debug('Killing %s hung process' % process)
            process.kill(signal.SIGTERM)
            for _ in range(3):
                if not process.is_alive():
                    break
                sleep(1)
            if process.is_alive():
                process.kill(signal.SIGKILL)
            self.unregister(process.pid)

class WatchdogProxy(WatchdogInterface):
    '''Helper class for watchdog service users.

    This class is a wrapper with exception handler of the watchdog service. It
    provides convenience for services using the watchdog by suppressing the
    burden of locating the watchdog and handling the various remote object
    related errors.

    '''
    def __init__(self, max_attempt=2):
        self._watchdog = None
        self.max_attempt = max_attempt

    def __attempt(self, func):
        for attempt in range(self.max_attempt):
            if not self._watchdog:
                try:
                    self._watchdog = NameServer().locate_service(MODULE_NAME)
                except Pyro5.errors.NamingError:
                    if attempt == self.max_attempt - 1:
                        log_exception('Failed to locate the watchdog',
                                      *sys.exc_info())
                except Pyro5.errors.CommunicationError:
                    if attempt == self.max_attempt - 1:
                        log_exception('Cannot communicate with the nameserver',
                                      *sys.exc_info())
            if self._watchdog:
                try:
                    return func()
                except Pyro5.errors.PyroError:
                    if attempt == self.max_attempt - 1:
                        log_exception('Communication failed with the watchdog',
                                      *sys.exc_info())
                    self._watchdog = None
        return None

    def register(self, pid: int, name: str, timeout: timedelta = None):
        self.__attempt(lambda: self._watchdog.register(pid, name, timeout))

    def unregister(self, pid: int) -> None:
        self.__attempt(lambda: self._watchdog.unregister(pid))

    def kick(self, pid: int) -> None:
        self.__attempt(lambda: self._watchdog.kick(pid))

# pylint: disable=missing-docstring
def main():
    base = os.path.splitext(__file__)[0]
    init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    watchdog = Watchdog(MonitorProxy())
    daemon = Pyro5.api.Daemon()
    uri = daemon.register(watchdog)
    nameserver = NameServer()
    nameserver.register_service(MODULE_NAME, uri)

    debug("... is now ready to run")
    while True:
        try:
            nameserver.register_service(MODULE_NAME, uri)
        except RuntimeError:
            log_exception('Failed to register the watchdog service',
                          *sys.exc_info())

        sockets, _, _ = select(daemon.sockets, [], [],
                               # pylint: disable=maybe-no-member
                               settings.max_loop_duration)
        if sockets:
            daemon.events(sockets)
        watchdog.monitor()
        watchdog.kill_hung_processes()

if __name__ == "__main__":
    main()
