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
import time

from datetime import datetime, timedelta
from pyemvue.enums import Scale

from consumer import Consumer, MyWallBox
from sensor import MyVue2, CarData
from tools import init, debug, get_utility, read_settings

def log_state(usage, charger):
    log = ', '.join([ f'{k}: {v:.2f}' for k, v in usage.items() \
                      if k == 'net' or abs(v) > 0.1 ])
    if charger.isCharging():
        log += ", added: %.2f KWh" % charger.getAddedEnergy()
    debug(log)

def stop_charge_and_sleep(msg, charger, seconds):
    debug(msg)
    charger.stop()
    time.sleep(seconds)

DEFAULT_SETTINGS = { 'coefficient':1,
                     'minimal_power':0,
                     'maximize':False,
                     'emergency_soc_threshold':0,
                     'emergency_charge_power':0 }

def main():
    prefix = os.path.splitext(__file__)[0]
    config = init(prefix + '.log')
    charger = MyWallBox(config['Wallbox'])
    consumers = []
    for current in config['general']['consumers'].split(','):
        if 'class' in config[current]:
            klass = getattr(__import__('consumer'), config[current]['class'])
            consumers.append(klass(config[current]))
        else:
            consumers.append(Consumer(config[current]))
    vue = MyVue2(config['Emporia'])
    car_data = CarData(config['CarData'])
    utility = get_utility()
    debug("... is now ready to run")
    while True:
        if not charger.isConnected():
            stop_charge_and_sleep("Waiting for car connection", charger, 10)
            continue

        if charger.isFullyCharged():
            stop_charge_and_sleep("Fully charged, nothing to do", charger, 60)
            continue

        entered_at = datetime.now()
        while True:
            try:
                usage=vue.read(scale=Scale.SECOND.value)
                break
            except:
                if entered_at + timedelta(seconds=90) < datetime.now():
                    charger.stop()
                time.sleep(15)
                continue

        log_state(usage, charger)
        available = (usage["net"] - charger.totalPower(usage)) * -1

        for consumer in consumers:
            if not consumer.isRunning(usage) and consumer.isAboutToStart():
                available -= consumer.power[-1]
                debug("Anticipating need for %s" % consumer.name)

        settings = read_settings(prefix + '.ini', DEFAULT_SETTINGS)
        maximize = False
        if not utility or not utility.isOnPeak():
            min_power = charger.power[0]
            if min_power * settings.coefficient < available < min_power:
                debug("Enforcing minimal charge power of %.02f KW" % min_power)
                available = charger.power[0]
            soc = car_data.read()['EV SoC']
            if soc < settings.emergency_soc_threshold and \
               available < settings.emergency_charge_power:
                debug("Low SoC (%.1f%%), force charging at %.1fKWh" %
                      (soc, settings.emergency_charge_power))
                available = settings.emergency_charge_power
            if settings.minimal_power > 0 and available < settings.minimal_power:
                debug("Enforcing minimal charge power of %.02f KW" %
                      settings.minimal_power)
                available = settings.minimal_power
            maximize = settings.maximize

        entered_at = datetime.now()
        charger.runWith(available, maximize=maximize)
        remaining = 15 - (datetime.now() - entered_at).seconds
        if remaining > 0:
            time.sleep(remaining)

if __name__ == "__main__":
    main()
