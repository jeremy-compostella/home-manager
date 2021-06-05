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
import time

from sensor import *
from consumer import *

from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

MOD_DESC=os.path.splitext(os.path.basename(__file__))[0].replace("_", " ")

def createLogger(filename):
    logger = logging.getLogger(MOD_DESC)
    logger.setLevel(logging.DEBUG)
    handler = TimedRotatingFileHandler(filename=filename, when="midnight",
                                       interval=1)
    handler.suffix = "%Y%m%d"
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    return logger

def logStatus(logger, usage, ev):
    actives = { k:v for (k, v) in usage.items() if k == 'net' or abs(v) > 0.1 }
    s = ', '.join([ "%s: %.02f" % (x[0], x[1]) for x in actives.items() ])
    if ev.isCharging():
        s += ", added: %.2f KWh" % ev.getAddedEnergy()
    logger.debug(s)

def main():
    config = configparser.ConfigParser()
    config.read("home.ini")

    logger = createLogger(os.path.splitext(__file__)[0]+'.log')
    logger.debug("%s is initializing..." % MOD_DESC)

    ev = MyWallBox(config['Wallbox'], logger)
    consumers = [ MyPoolPump(config['Pool']),
                  MyWaterHeater(config['Aquanta']),
                  MyEcobee(config['Ecobee']) ]
    weather = MyOpenWeather(config['OpenWeather'])
    vue = MyVue2(config['Emporia'])

    logger.debug("%s is ready to run" % MOD_DESC)
    while True:
        if weather.isNightTime():
            logger.debug("No solar production at night")
            ev.stop()
            time.sleep(60 * 5)
            continue

        if not ev.isConnected():
            logger.debug("Waiting for car connection")
            ev.stop()
            time.sleep(15)
            continue

        if ev.isFullyCharged():
            logger.debug("Fully charged, nothing to do")
            ev.stop()
            time.sleep(60)
            continue

        entered_at = datetime.now()
        while True:
            try:
                usage=vue.read(scale=Scale.SECOND.value)
                break
            except:
                logger.warning("%s sensor read failed" % type(vue).__name__)
                if entered_at + timedelta(seconds=90) < datetime.now():
                    ev.stop()
                time.sleep(15)
                continue

        logStatus(logger, usage, ev)
        available = (usage["net"] - ev.totalPower(usage)) * -1

        for c in consumers:
            if not c.isRunning(usage) and c.isAboutToStart():
                available -= c.power[-1]
                logger.debug("Anticipating need for %s" % c.name)

        try:
            target = [ x for x in ev.power if x <= available ][-1]
        except IndexError:
            target = 0
        ev.runWith(target)
        time.sleep(15)

if __name__ == "__main__":
    main()
