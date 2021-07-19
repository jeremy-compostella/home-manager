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
import csv
import logging
import os
import time

from datetime import datetime

from sensor import *
from consumer import *

class SensorLogWriter:
    def __setup(self):
        self.f = open(self.filename, 'a', newline='')
        self.writer = csv.DictWriter(self.f, self.fieldnames)
        self.endofday = datetime.now().replace(hour=23, minute=59, second=59)

    def __init__(self, filename, fieldnames):
        self.filename = filename
        self.fieldnames = fieldnames
        self.__setup()

    def rotate(self):
        self.f.close()
        os.rename(self.filename,
                  self.filename + "." + self.endofday.strftime("%Y%m%d"))
        time.sleep(1)
        self.__setup()

    def log(self, rowdict):
        if datetime.now() > self.endofday:
            self.rotate()
        if os.stat(self.filename).st_size == 0:
            self.writer.writeheader()
        self.writer.writerow(rowdict)
        self.f.flush()

def wait_for_next_minute():
    t = datetime.now()
    sleeptime = 60 - (t.second + t.microsecond/1000000.0)
    if sleeptime > 0:
        time.sleep(sleeptime)

def main():
    config = configparser.ConfigParser()
    config.read('home.ini')

    sensors = [ ]
    for s in config['general']['sensors'].split(','):
        c = eval(config[s]['class'])
        sensors.append(c(config[s]))

    while True:
        header = [ 'time' ]
        try:
            for sensor in sensors:
                header += sensor.read().keys()
            break
        except:
            logging.warning("Read from sensor failed")
            time.sleep(60)

    logger = SensorLogWriter(config['general']['sensor_db'], header)
    wait_for_next_minute()
    while True:
        t = datetime.now()
        row = { 'time': t.strftime("%m/%d/%Y %H:%M:%S") }
        try:
            for sensor in sensors:
                row.update(sensor.read().items())
            logger.log(row)
        except:
            logging.warning("Read from sensor failed")
        wait_for_next_minute()

if __name__ == "__main__":
    main()
