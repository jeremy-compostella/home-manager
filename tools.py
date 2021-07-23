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

import configparser
import logging
import os
import smtplib
import time

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

_mailer = None
_logger = None

class _MyEmail:
    def __init__(self, config):
        self.config = config

    def send(self, msg):
        msg['From'] = self.config['from']
        msg['To'] = self.config['to']

        server = smtplib.SMTP_SSL(self.config['server'],
                                  int(self.config['port']),
                                  timeout=60)
        server.ehlo()
        server.login(self.config['login'], self.config['password'])
        server.sendmail(msg['From'], msg['To'], msg.as_string())

    def sendMIMEText(self, subject, body = ''):
        msg = MIMEMultipart('related')
        msg.preamble = 'This is a multi-part message in MIME format.'
        msg['Subject'] = subject
        alternative = MIMEMultipart('alternative')
        msg.attach(alternative)
        alternative.attach(MIMEText(body.encode('utf-8'), 'plain', _charset='utf-8'))
        self.send(msg)

def _create_logger(filename):
    name = os.path.splitext(os.path.basename(filename))[0].replace("_", " ")
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    handler = TimedRotatingFileHandler(filename=filename, when="midnight",
                                       interval=1)
    handler.suffix = "%Y%m%d"
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    logger.debug("%s is initializing..." % name)
    return logger

def init(log_file = None):
    config = configparser.ConfigParser()
    config.read("home.ini")

    global mailer
    _mailer = _MyEmail(config['Email'])
    if log_file:
        global _logger
        _logger = _create_logger(log_file)
        return config, _logger
    return config

def debug(text):
    if _logger:
        _logger.debug(text)

def notify(text):
    if _logger:
        _logger.warning(text)
    _mailer.sendMIMEText(text)

def sendEmail(msg):
    _mailer.send(msg)

def wait_for_next_minute():
    t = datetime.now()
    sleeptime = 60 - (t.second + t.microsecond/1000000.0)
    if sleeptime > 0:
        time.sleep(sleeptime)
