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
import os
import sys

from datetime import datetime, timedelta

from consumer import MyEcobee, MyWallBox
from sensor import MyOpenWeather, MyVue2
from tools import init, debug, notify, wait_for_next_minute

def load_database(filename):
    return list(csv.DictReader(open(filename, 'r'),
                               fieldnames=['temperature', 'rate'],
                               quoting=csv.QUOTE_NONNUMERIC))

def cannot_warm_up(weather):
    bad = [ {'status':'Clouds', 'detailed status':'overcast clouds' },
            {'status':'Clouds', 'detailed status':'broken clouds' },
            {'status':'Thunderstorm' },
            {'status':'Rain' },
            {'status':'Mist' } ]
    for desc in bad:
        if weather['status'] == desc['status']:
            if 'detailed status' in desc:
                if weather['detailed status'] == desc['detailed status']:
                    return True
            else:
                return True

def estimate(database, weather, indoor, goal):
    item = None
    prev= None

    outdoor = weather['outdoor temp']
    if cannot_warm_up(weather):
        debug("The weather is too bad to allow the air to warm up")
        if outdoor <= goal + 10 and indoor <= goal + 8:
            debug("The outdoor and indoor temperatures are low")
            return 0

    if outdoor < database[0]['temperature']:
        item = database[0]
    elif outdoor > database[-1]['temperature']:
        item = database[-1]
    else:
        for item in database:
            if item['temperature'] >= outdoor:
                break
            prev = item
    rate = item['rate']
    if item['temperature'] != outdoor and prev:
        rate = (item['rate'] + prev['rate']) / 2

    minutes = (indoor - goal) * rate
    if cannot_warm_up(weather):
        minutes /= 2
    return minutes

def main():
    prefix = os.path.splitext(__file__)[0]
    config = init(prefix + '.log')

    if not 'adjustable_program' in config['Ecobee']:
        sys.exit("No adjustable program found in the Ecobee section")

    program = config['Ecobee']['adjustable_program']

    hvac = MyEcobee(config['Ecobee'])
    weather = MyOpenWeather(config['OpenWeather'])
    charger = MyWallBox(config['Wallbox'])
    vue = MyVue2(config['Emporia'])

    database = load_database("hvac_database.csv")
    saved = {}

    debug("... is now ready to run")
    early_schedule = False
    while True:
        info = hvac.programInfo(program)
        if not info:
            wait_for_next_minute()
            continue

        if saved and datetime.now() >= saved['stop']:
            hvac.setProgramSchedule(program, saved['start'], saved['stop'])
            notify("HVAC: '%s' Program schedule restored" % program)
            saved = None
            early_schedule = False

        if datetime.now() >= info['stop']:
            wait_for_next_minute()
            continue

        # Early schedule: if the car is not plugged-in and the right
        # conditions are met, start PROGRAM earlier to get the house
        # to the right temperature before the car is back and ready to
        # be charged.
        if early_schedule:
            if charger.isConnected():
                hvac.setProgramSchedule(program, saved['start'], saved['stop'])
                debug('HVAC: Stopping early schedule')
                early_schedule = False
                continue
            if datetime.now() >= info['start']:
                early_schedule = False
            wait_for_next_minute()
            continue
        if datetime.now() < info['start'] and \
           not charger.isConnected() and \
           hvac.power[-1] + vue.read()['net'] < .3:
            if not saved:
                saved = info
            hvac.setProgramSchedule(program, datetime.now(), saved['stop'])
            early_schedule = True
            debug('HVAC: Starting early schedule')
            wait_for_next_minute()
            continue

        # If the car is not connected or is fully charged, prioritize
        # comfort over consumption by not postponing PROGRAM.
        if not charger.isConnected() or charger.isFullyCharged():
            debug("HVAC: use power while available")
            wait_for_next_minute()
            continue

        # Late schedule: based on the home thermal property and
        # current outdoor and indoor temperatures, predict the
        # required time to get the house to the right temperature and
        # delay the starting time of PROGRAM accordingly.
        if datetime.now() < info['start'] - timedelta(minutes=5) or \
           datetime.now() > info['start']:
            wait_for_next_minute()
            continue

        allocated = (info['stop'] - info['start']).seconds / 60
        required = estimate(database, weather.read(),
                            info['current'], info['target'] + .8)
        debug("It should take %d minutes to go from %.01fF to %.01fF" %
              (required, info['current'], info['target'] + .8))
        if allocated > round(required / 30) * 30:
            hvac.setProgramSchedule(program, info['start'] + timedelta(minutes=30),
                                    info['stop'])
            debug("HVAC: '%s' program schedule postponed by 30 minutes" % program)
            if not saved:
                saved = info

        wait_for_next_minute()

if __name__ == "__main__":
    main()
