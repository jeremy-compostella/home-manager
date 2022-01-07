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

'''This module provides tool functions and classes for the entire project.'''

import logging
import os
import re
import shelve
import sys
import traceback
from configparser import ConfigParser
from logging.handlers import TimedRotatingFileHandler
from os.path import basename, splitext

import Pyro5.api

_LOGGER  = None
_CONFIG = None

def _create_logger(filename):
    name = splitext(basename(filename))[0].replace("_", " ")
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    handler = TimedRotatingFileHandler(filename=filename, when="midnight",
                                       interval=1)
    handler.suffix = "%Y%m%d"
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    logger.debug("%s is initializing...", name)
    return logger

def init(log_file=None):
    global _CONFIG
    _CONFIG = ConfigParser()
    _CONFIG.read(os.getenv('HOME') + '/etc/home_manager.conf')
    if log_file:
        global _LOGGER
        _LOGGER = _create_logger(log_file)
    return _CONFIG

def debug(text):
    '''Record text to the log file.'''
    if _LOGGER:
        _LOGGER.debug(text)

def log_exception(msg, exc_type, exc_value, exc_traceback):
    '''Record the msg and the exception to the log file.'''
    debug(msg + ', %s' % exc_type)
    for line in traceback.format_exception(exc_type, exc_value, exc_traceback):
        debug(line[:-1])

class Settings:
    '''Represent key/value pair settings.

    Settings can be loaded from the configuration file. All the key/value pair
    under the 'settings' are loaded as attributes of the Settings object.

    '''
    def __init__(self, filename: str, defaults: dict):
        self.__filename = filename
        self.__keys = defaults.keys()
        for key, value in defaults.items():
            setattr(self, key, value)
        self.load()

    def load(self):
        '''Load the settings from filename supplied at construction.'''
        if not os.path.exists(self.__filename):
            return
        config = ConfigParser()
        config.read(self.__filename)
        if 'settings' not in config:
            raise ValueError('Invalid settings file %s' % self.__filename)
        for key in self.__keys:
            if key in config['settings']:
                try:
                    setattr(self, key, float(config['settings'][key]))
                except ValueError:
                    setattr(self, key, bool(config['settings'][key]))

def get_storage():
    '''Return a shelve object for dynamic data storage.'''
    return shelve.open(os.getenv('HOME') + '/storage', protocol=2)

class NameServer:
    QUALIFIERS = ['sensor', 'service', 'task']

    def __init__(self):
        self.nameserver = None
        self.base_uri = _CONFIG['general']['base_uri']

    def __call(self, method, *args):
        for _ in range(2):
            try:
                self.nameserver = Pyro5.api.locate_ns()
            except Pyro5.errors.PyroError:
                log_exception('Cannot locate the nameserver', *sys.exc_info())
            if self.nameserver:
                try:
                    return getattr(self.nameserver, method)(*args)
                except Pyro5.errors.NamingError:
                    debug('Unknown %s' % args)
                except Pyro5.errors.PyroError:
                    log_exception('Failed to communicate with the nameserver',
                                  *sys.exc_info())
        raise RuntimeError('Could not access the nameserver')

    def path(self, qualifier, name):
        return '%s.%s.%s' % (self.base_uri, qualifier, name)

    def generator(self, qualifier):
        pattern = re.compile('%s.%s\\..*' % (self.base_uri, qualifier))
        preffix_len = (len(self.base_uri) + len(qualifier) + 2)
        for name, uri in self.__call('list').items():
            if pattern.search(name):
                yield name[preffix_len:], Pyro5.api.Proxy(uri)

    def register(self, qualifier, name, uri):
        if qualifier not in self.QUALIFIERS:
            raise ValueError('Invalid qualifier %s' % qualifier)
        self.__call('register', self.path(qualifier, name), uri)

    def locate(self, qualifier, name):
        if qualifier not in self.QUALIFIERS:
            raise ValueError('Invalid qualifier %s' % qualifier)
        uri = self.__call('lookup', self.path(qualifier, name))
        return Pyro5.api.Proxy(uri)

    def __getattr__(self, name):
        if name in [q + 's' for q in self.QUALIFIERS]:
            def generator():
                return self.generator(name[:-1])
            return generator
        try:
            action, qualifier = name.split('_')
        except ValueError:
            raise AttributeError("'%s' has no attribute '%s'"
                                 % (self.__class__.name, name))
        if action == 'register':
            def register(*args):
                self.register(qualifier, *args)
            return register
        if action == 'locate':
            def locate(*args):
                return self.locate(qualifier, *args)
            return locate
        raise AttributeError("'%s' has no attribute '%s'"
                             % (self.__class__.name, name))

def fahrenheit(celsius):
    return celsius * 9 / 5 + 32

def celsius(fahrenheit):
    return (fahrenheit - 32) * 5 / 9

def miles(kilometers):
    return kilometers * 1.60934

def kilometers(miles):
    return miles / 1.60934

def meter_per_second(mph):
    return mph / 2.237
