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

import csv
import logging
import smtplib
import shelve
import time

from configparser import ConfigParser
from collections import namedtuple
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import IntEnum
from os.path import basename, splitext, isfile

_mailer  = None
_logger  = None
_utility = None

class _MyEmail:
    def __init__(self, config):
        self.config = config

    def send(self, msg, level = 'notify'):
        msg['From'] = self.config['from']
        msg['To'] = self.config[level]

        server = smtplib.SMTP_SSL(self.config['server'],
                                  int(self.config['port']),
                                  timeout=60)
        server.ehlo()
        server.login(self.config['login'], self.config['password'])
        server.sendmail(msg['From'], msg['To'], msg.as_string())

    def sendMIMEText(self, subject, body = '', level = 'notify'):
        msg = MIMEMultipart('related')
        msg.preamble = 'This is a multi-part message in MIME format.'
        msg['Subject'] = subject
        alternative = MIMEMultipart('alternative')
        msg.attach(alternative)
        alternative.attach(MIMEText(body.encode('utf-8'), 'plain', _charset='utf-8'))
        self.send(msg, level = level)

class SensorLogReader(csv.DictReader):
    DEFAULT_FILENAME="sensor.csv"
    date = None

    def __init__(self, date=None, filename=None):
        if not filename:
            if date:
                filename = self.DEFAULT_FILENAME + "." + date.strftime("%Y%m%d")
            else:
                filename = self.DEFAULT_FILENAME
        self.filename = filename
        if not isfile(filename):
            raise FileNotFoundError()

    def __iter__(self):
        f = open(self.filename, 'r')
        csv.DictReader.__init__(self, f)
        return self

    def __next__(self):
        d = csv.DictReader.__next__(self)
        for key, value in d.items():
            if key == 'time':
                d[key] = datetime.strptime(value, "%m/%d/%Y %H:%M:%S")
                if not self.date:
                    self.date= d[key]
            else:
                try:
                    d[key] = float(value)
                except ValueError:
                    d[key] = value
        return d

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

class Utility:
    WEEKDAYS = IntEnum('Weekdays', 'mon tue wed thu fri sat sun', start=0)
    rate = { k:0 for k in [ 'onpeak', 'offpeak', 'export' ] }

    def __init__(self, config):
        self.schedules = eval(config["schedule"])
        self.config = config

    def loadRate(self, date):
        self.rate['onpeak'] = eval(self.config['onpeak'])[date.month - 1]
        self.rate['offpeak'] = eval(self.config['offpeak'])[date.month - 1]
        self.rate['export'] = eval(self.config['export'])

    def isOnPeak(self, date=None):
        if not date:
            date = datetime.now()
        if date.weekday() in [ self.WEEKDAYS.sat, self.WEEKDAYS.sun ]:
            return False
        sched_name=self.schedules[date.month - 1]
        schedule=eval(self.config[sched_name])
        found = [ x for x in schedule if date.hour >= x[0] and date.hour < x[1] ]
        return len(found) == 1

def init(log_file = None):
    config = ConfigParser()
    config.read("home.ini")

    global _mailer
    _mailer = _MyEmail(config['Email'])
    if log_file:
        global _logger
        _logger = _create_logger(log_file)
    if 'utility' in config['general']:
        global _utility
        _utility = Utility(config[config['general']['utility']])
    return config

def debug(text):
    if _logger:
        _logger.debug(text)

def __warn(text, level = 'notify'):
    if _logger:
        _logger.warning(text)
    try:
        _mailer.sendMIMEText(text, level = level)
    except:
        debug("Failed to send an email with '%s'" % text)

def notify(text):
    __warn(text)

def alert(text):
    __warn(text, level = 'alert')

def send_email(msg):
    _mailer.send(msg)

def wait_for_next_minute():
    now = datetime.now()
    sleeptime = 60 - (now.second + now.microsecond/1000000.0)
    if sleeptime > 0:
        time.sleep(sleeptime)

def read_settings(filename, defaults):
    ret = defaults.copy()
    Settings = namedtuple('Settings', ret.keys())
    try:
        config = ConfigParser()
        config.read(filename)
        if 'settings' not in config:
            return Settings(**ret)
        for key in list(ret.keys()):
            if key in config['settings']:
                try:
                    ret[key] = float(config['settings'][key])
                except ValueError:
                    ret[key] = bool(config['settings'][key])
    except:
        pass
    return Settings(**ret)

def get_utility():
    return _utility

def get_storage():
    return shelve.open('storage', protocol=2)
