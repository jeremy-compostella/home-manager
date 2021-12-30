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

from logging import getLogger

import Pyro5.api
from homeassistant.components.switch import SwitchEntity

LOGGER = getLogger(__name__)

def locate(path):
    nameserver = Pyro5.api.locate_ns()
    return Pyro5.api.Proxy(nameserver.lookup(path))

async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    prefix = 'home-manager.task.'
    nameserver = Pyro5.api.locate_ns()
    for path, _ in nameserver.list().items():
        if path.startswith(prefix):
            add_entities([TaskSwitch(path[len(prefix):], path)])
    add_entities([SchedulerSwitch('home-manager.service.scheduler')])

class TaskSwitch(SwitchEntity):
    def __init__(self, name, path):
        super().__init__()
        self._name = name
        self._path = path
        self._cache = None

    @property
    def name(self):
        return self._name

    def __attempt(self, method, *args):
        try:
            task = locate(self._path)
            return getattr(task, method)(*args)
        except Pyro5.errors.PyroError as err:
            print('1', err)
        return None

    @property
    def is_on(self):
        if self._cache is not None:
            is_on = self._cache
            self._cache = None
            return is_on
        return self.__attempt('is_running')

    def turn_on(self, **kwargs):
        self.__attempt('start')
        self._cache = True

    def turn_off(self, **kwargs):
        self.__attempt('stop')
        self._cache = False

    @property
    def unique_id(self):
        return self._path

class SchedulerSwitch(SwitchEntity):
    def __init__(self, path):
        super().__init__()
        self._path = path

    @property
    def name(self):
        return 'scheduler'

    def __attempt(self, method, *args):
        try:
            task = locate(self._path)
            return getattr(task, method)(*args)
        except Pyro5.errors.PyroError as err:
            print(err)
            return False

    @property
    def is_on(self):
        return not self.__attempt('is_on_pause')

    def turn_on(self, **kwargs):
        self.__attempt('resume')

    def turn_off(self, **kwargs):
        self.__attempt('pause')

    @property
    def unique_id(self):
        return self._path
