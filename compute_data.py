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
import re

from math import floor
from statistics import median
from tools import SensorLogReader

def rate(entry):
    return entry['Minutes'] / (entry['Indoor Start'] - entry['Indoor Stop'])

exclude = [ 'sensor.csv.20210702',
            'sensor.csv.20210703',
            'sensor.csv.20210612' ]

def main():
    pattern = re.compile("^sensor\.csv\.[0-9]+$")
    database = []
    for filename in os.listdir("."):
        if filename == 'sensor.csv' or \
           (not filename in exclude and pattern.search(filename)):
            reader = SensorLogReader(filename=filename)
            row = {}
            start = None
            power = []
            for current in iter(reader):
                if current['time'].hour < 12 or current['time'].hour > 15:
                    continue

                if start:
                    power.append(current['air handler'] + current['A/C'])
                    if current['A/C'] < .5 or \
                       ((current['time'] - start).seconds / 60) >= 120:
                        row['Minutes'] = (current['time'] - start).seconds / 60
                        if row['Minutes'] <= 60:
                            start = None
                            continue
                        row['Outdoor Stop'] = current['outdoor temp']
                        row['Indoor Stop'] = (current['Living Room'] + current['Home']) / 2
                        row['Power'] = median(power)
                        break
                elif current['A/C'] > .5:
                    start = current['time']
                    row['Outdoor Start'] = current['outdoor temp']
                    row['Indoor Start'] = (current['Living Room'] + current['Home']) / 2
            if start and 'Minutes' in row:
                database.append(row)

    with open('hvac_database.csv', 'w', newline='') as file:
        writer = csv.writer(file)
        database.sort(key=lambda x: x['Outdoor Start'])
        while len(database) > 0:
            current = database[0]
            temp = floor(current['Outdoor Start'])
            rates = [ rate(x) for x in database if floor(x['Outdoor Start']) == temp ]
            powers = [ x['Power'] for x in database if floor(x['Outdoor Start']) == temp ]
            writer.writerow([ temp, median(rates), median(powers) ])
            database = [ x for x in database if floor(x['Outdoor Start']) != temp]

if __name__ == "__main__":
    main()
