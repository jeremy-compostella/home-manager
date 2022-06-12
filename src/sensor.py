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

'''This module provides the Sensor interface class.'''

import sys
from abc import abstractmethod
from datetime import datetime, timedelta

import Pyro5.api

from tools import NameServer, debug, log_exception


class Sensor:
    '''This class is the interface a Sensor should implement.'''
    @abstractmethod
    def read(self, **kwargs: dict) -> dict:
        '''Return a sensor record.'''

    @abstractmethod
    def units(self, **kwargs: dict) -> dict:
        '''Return the sensor unit mapping'''

class SensorReader(Sensor):
    '''This class is sensor wrapper with  error management.

    It discharges the caller from having to handle various exceptions. On a
    sensor read() failure, the wrapper returns None. The caller can use the
    time_elapsed_since_latest_record() method to know the time elapsed since it
    successfully retrieved a record.

    '''
    def __init__(self, name, timeout = 5):
        self.name = name
        self.timeout = timeout
        self.latest_read = None
        self.sensor = None

    def __attempt(self, method, **kwargs):
        for _ in range(2):
            if not self.sensor:
                try:
                    self.sensor = NameServer().locate_sensor(self.name)
                    # pylint: disable=protected-access
                    self.sensor._pyroTimeout = self.timeout
                except RuntimeError:
                    log_exception('Failed to locate the %s sensor' % self.name,
                                  *sys.exc_info())
            if self.sensor:
                try:
                    return getattr(self.sensor, method)(**kwargs)
                except (Pyro5.errors.PyroError, Pyro5.errors.ConnectionClosedError):
                    log_exception('Communication failed with the %s sensor' %
                                  self.name, *sys.exc_info())
                    debug("".join(Pyro5.errors.get_pyro_traceback()))
                except RuntimeError:
                    log_exception('Failed to read a new %s sensor record' %
                                  self.name, *sys.exc_info())
                    debug("".join(Pyro5.errors.get_pyro_traceback()))
                self.sensor = None
        return None

    def read(self, **kwargs) -> dict:
        '''Read a sensor record.

        It returns an empty dictionary if the sensor read() method raises an
        Pyro5.errors.CommunicationError or RuntimeError exception.

        '''
        record = self.__attempt('read', **kwargs)
        if record is not None:
            self.latest_read = datetime.now()
        return record

    def units(self, **kwargs):
        return self.__attempt('units', **kwargs)

    def time_elapsed_since_latest_record(self) -> timedelta:
        '''Time elapsed since read() successfully retrieved a record.'''
        if not self.latest_read:
            self.latest_read = datetime.now() - timedelta(minutes=1)
        return datetime.now() - self.latest_read
