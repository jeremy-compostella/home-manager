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
import threading

from datetime import datetime, timedelta

from consumer import MyEcobee, MyWallBox
from sensor import MyOpenWeather, EmporiaProxy
from tools import init, debug, wait_for_next_minute, read_settings

class PowerStatistics(threading.Thread):
    def __init__(self, size, vue):
        super().__init__()
        self.size = size
        self.vue = vue
        self.lock = threading.Lock()
        self.window = []

    def run(self):
        while True:
            with self.lock:
                try:
                    self.window.append(self.vue.read())
                except:
                    if len(self.window) > 0:
                        self.window.pop(0)
                if len(self.window) > self.size:
                    self.window.pop(0)
            wait_for_next_minute()

    @staticmethod
    def __net_for(usage, ignore = []):
        net = usage['net']
        for consumer in ignore:
            net -= consumer.totalPower(usage)
        return net

    def available_for(self, consumer, ignore = []):
        with self.lock:
            count = net = 0
            for usage in self.window[-2:]:
                net += self.__net_for(usage, ignore)
                count += 1
        return -1 * net / (consumer.power[-1] * count)

    def covered_by_production(self, consumer, ignore = []):
        with self.lock:
            net = used = 0
            for usage in self.window:
                if consumer.isRunning(usage):
                    net += self.__net_for(usage, ignore)
                    used += consumer.totalPower(usage)
        return 1 if used == 0 else -1 * (net - used) / used

def load_database(filename):
    return list(csv.DictReader(open(filename, 'r'),
                               fieldnames=['temperature', 'rate'],
                               quoting=csv.QUOTE_NONNUMERIC))

def estimate(database, weather, deviation):
    item = None
    prev= None

    outdoor = weather['outdoor temp']
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

    return timedelta(minutes=deviation * rate)

DEFAULT_SETTINGS = { 'min_run':15,
                     'min_coverage':.8,
                     'unplugged_min_coverage':.85,
                     'fully_charged_min_coverage':1,
                     'temp_offset':0 }

def main():
    prefix = os.path.splitext(__file__)[0]
    config = init(prefix + '.log')

    if not 'adjustable_program' in config['Ecobee']:
        sys.exit("No adjustable program found in the Ecobee section")

    hvac = MyEcobee(config['Ecobee'])
    weather = MyOpenWeather(config['OpenWeather'])
    charger = MyWallBox(config['Wallbox'])
    settings = read_settings(prefix + '.ini', DEFAULT_SETTINGS)
    stat = PowerStatistics(settings.min_run,
                           EmporiaProxy(config['EmporiaProxy']))
    stat.start()
    program = hvac.get_program(config['Ecobee']['adjustable_program'])

    database = load_database("hvac_database.csv")

    debug("... is now ready to run")
    late_schedule = False
    while True:
        settings = read_settings(prefix + '.ini', DEFAULT_SETTINGS)

        if program.start.weekday() != datetime.today().weekday():
            debug('Loading program')
            program.load()

        if program.is_over():
            if program.has_been_alterated:
                program.restore()
                debug("program schedule restored - [ %s, %s ]" %
                      (program.start, program.stop))
            late_schedule = False
            wait_for_next_minute()
            continue

        if program.is_running:
            debug('HVAC is running and %.02f%% is covered by the production' %
                  (100 * stat.covered_by_production(hvac, ignore = [ charger ])))
            if program.has_run_for() >= timedelta(minutes=settings.min_run):
                if charger.isConnected() and \
                   not charger.isFullyCharged() and \
                   not late_schedule:
                    debug('Stopping HVAC by 30 minutes')
                    program.start = datetime.now() + timedelta(minutes=30)
                elif stat.covered_by_production(hvac, [ charger ]) < settings.min_coverage:
                    debug('Production does not cover %d%% of power need' %
                          (settings.min_coverage * 100))
                    late_schedule = False
                    program.start = datetime.now() + timedelta(minutes=30)
            wait_for_next_minute()
            continue

        # Do not ridiculously start too soon
        if program.time_remaining() > timedelta(hours=5):
            wait_for_next_minute()
            continue

        available_for_hvac = stat.available_for(hvac, [ charger ])
        debug('Current production could cover %.02f%% of HVAC' %
              (available_for_hvac * 100))
        # Early schedule: We want to handle two situations:
        # - If the car has left the garage, we want to get as soon and
        #   as close as possible to desired temperature to have
        #   available power for when the car is back in the garage and
        #   need to charge. Even if we have to get part of the power
        #   from the grid.
        # - If the car is sitting in the garage and fully charged but
        #   we have enough to cover 100% of the HVAC system, let's
        #   take advantage of it and improve the comfort at home.
        if (not charger.isConnected() and \
            available_for_hvac >= settings.unplugged_min_coverage) or \
           (charger.isFullyCharged() and \
            available_for_hvac >= settings.fully_charged_min_coverage):
            debug('Starting early schedule')
            program.start = datetime.now()
            wait_for_next_minute()
            continue

        # Late schedule
        deviation = program.temperature_deviation()
        deviation += -1 * settings.temp_offset if deviation > 0 else settings.temp_offset
        required = estimate(database, weather.read(), deviation)
        debug("%s/%s to change the temperature_deviation by %.01fF" %
              (required, program.time_remaining(), deviation))
        if program.time_remaining() <= required and \
           available_for_hvac >= settings.min_coverage:
            debug('Time to start HVAC to reach the desired temperature on time')
            program.start = datetime.now()
            late_schedule = True
        elif program.starting_in_less_than(timedelta(minutes=3)):
            debug('Postponing by 30 minutes')
            program.start = datetime.now() + timedelta(minutes=33)

        wait_for_next_minute()

if __name__ == "__main__":
    main()
