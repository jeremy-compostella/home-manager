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

from consumer import *
from sensor import *
from tools import *

def load_database(filename):
    return list(csv.DictReader(open(filename, 'r'),
                               fieldnames=['temperature', 'rate'],
                               quoting=csv.QUOTE_NONNUMERIC))

def estimate(database, outdoor, indoor, goal):
    item = None
    prev= None
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
    return (indoor - goal) * rate

def main():
    config, logger = init(os.path.splitext(__file__)[0]+'.log')

    if not 'adjustable_program' in config['Ecobee']:
        sys.exit("No adjustable program found in the Ecobee section")

    program = config['Ecobee']['adjustable_program']

    ecobee = MyEcobee(config['Ecobee'])
    ev = MyWallBox(config['Wallbox'], logger)
    weather = MyOpenWeather(config['OpenWeather'])

    database = load_database("hvac_database.csv")
    saved = None

    debug("... is now ready to run")
    while True:
        info = ecobee.programInfo(program)

        if datetime.now() >= info['stop']:
            if saved:
                ecobee.setProgramSchedule(program, info['start'], info['stop'])
                notify("'%s' Program schedule restored" % program)
                saved = None
            continue

        if not ev.isConnected() or ev.isFullyCharged():
            continue

        if datetime.now() < info['start'] - timedelta(minutes=5) or \
           datetime.now() > info['start']:
            wait_for_next_minute()
            continue

        allocated = (info['stop'] - info['start']).seconds / 60
        required = estimate(database, weather.read()['outdoor temp'],
                            info['current'], info['target']) * .95
        if allocated > required:
            ecobee.setProgramSchedule(program, info['start'] + timedelta(minutes=30),
                                      info['stop'])
            notify("'%s' program schedule postponed by 30 minutes" % program)
            if not saved:
                saved = info

        time.sleep(60 * 29)

if __name__ == "__main__":
    main()
