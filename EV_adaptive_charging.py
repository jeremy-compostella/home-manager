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

from consumer import *
from datetime import datetime, timedelta
from sensor import *
from tools import *

def log_state(usage, ev):
    s = ', '.join([ f'{k}: {v:.2f}' for k, v in usage.items() \
                    if k == 'net' or abs(v) > 0.1 ])
    if ev.isCharging():
        s += ", added: %.2f KWh" % ev.getAddedEnergy()
    debug(s)

def stop_charge_and_sleep(msg, ev, seconds):
    debug(msg)
    ev.stop()
    time.sleep(seconds)

DEFAULT_SETTINGS = { 'coefficient':1,
                     'minimal_charge':0,
                     'maximize':False }

def main():
    prefix = os.path.splitext(__file__)[0]
    config = init(prefix + '.log')
    ev = MyWallBox(config['Wallbox'])
    consumers = []
    for c in config['general']['consumers'].split(','):
        if 'class' in config[c]:
            consumers.append(globals()[config[c]['class']](config[c]))
        else:
            consumers.append(Consumer(config[c]))
    vue = MyVue2(config['Emporia'])
    utility = get_utility()
    debug("... is now ready to run")
    while True:
        if not ev.isConnected():
            stop_charge_and_sleep("Waiting for car connection", ev, 10)
            continue

        if ev.isFullyCharged():
            stop_charge_and_sleep("Fully charged, nothing to do", ev, 60)
            continue

        entered_at = datetime.now()
        while True:
            try:
                usage=vue.read(scale=Scale.SECOND.value)
                break
            except:
                notify("%s sensor read failed" % type(vue).__name__)
                if entered_at + timedelta(seconds=90) < datetime.now():
                    ev.stop()
                time.sleep(15)
                continue

        log_state(usage, ev)
        available = (usage["net"] - ev.totalPower(usage)) * -1

        for c in consumers:
            if not c.isRunning(usage) and c.isAboutToStart():
                available -= c.power[-1]
                debug("Anticipating need for %s" % c.name)

        settings = read_settings(prefix + '.ini', DEFAULT_SETTINGS)
        maximize = False
        if not utility.isOnPeak():
            if ev.power[0] * settings.coefficient < available < ev.power[0]:
                debug("Enforcing charge rate of %.02f KW" % ev.power[0])
                available = ev.power[0]
            if settings.minimal_charge > 0 and available < settings.minimal_charge:
                debug("Enforcing minimal charge rate of %.02f KW" %
                      settings.minimal_charge)
                available = settings.minimal_charge
            maximize = settings.maximize

        ev.runWith(available, maximize=maximize)
        time.sleep(15)

if __name__ == "__main__":
    main()
