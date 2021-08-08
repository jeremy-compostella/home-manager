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
import threading

from bluetooth import *
from consumer import *
from datetime import datetime, timedelta
from sensor import *
from statistics import median
from tools import *

def test_loop(name, msg, end_msg = None, sleep = 15, end = False, min_failure = 1):
    def inner(fun):
        def run_until_success(*args, **kwargs):
            failed = 0
            while True:
                ret = fun(*args, **kwargs)
                debug('%s:%s' % (name, ret))
                if ret:
                    if failed >= min_failure and end_msg:
                        alert(end_msg)
                    failed = 0
                    if end:
                        return ret
                    time.sleep(sleep)
                    continue
                failed += 1
                if failed == min_failure:
                    alert(msg)
                time.sleep(sleep)
        return run_until_success
    return inner

reader = None
class SensorReader:
    expiration = None
    usage = None

    def __init__(self, config):
        self.vue = MyVue2(config)
        self._usage_lock = threading.Lock()

    @test_loop('Emporia read', "Failed to access Emporia", "Emporia is back",
               min_failure = 3, end = True)
    def __read(self):
        try:
            return self.vue.read(scale=Scale.SECOND.value)
        except:
            return False

    def read(self):
        with self._usage_lock:
            if self.expiration and datetime.now() < self.expiration:
                return self.usage
            self.usage = self.__read()
            self.expiration = datetime.now() + timedelta(seconds=15)
        return self.usage

# HVAC
# ----
# 1. When the Yellow wire is shunt by float T-switch the air handler
#    stops running but the Heat Pump is still running
@test_loop('Heat pump yellow',
           "Heat pump running while air handler is stopped",
           "Heat pump/air handler is back to normal",
           min_failure = 3)
def hvac_yellow():
    usage = reader.read()
    return not (usage['A/C'] > .1 and usage['air handler'] < .1)

# 2. When the Red wire is shunt by float T-switch, the air handler
#    keeps running but the condensation and the heat pump do not. To
#    avoid false positive, we use a large min_failure parameter as my
#    HVAC is configured to run the air-handler for a few minutes after
#    stopping the A/C.
@test_loop('heat pump red',
           "Air handler is running while Heat Pump is stopped",
           "Heat pump/air handler is back to normal",
           min_failure = 32)
def hvac_red():
    usage = reader.read();
    return not (usage['A/C'] < .1 and usage['air handler'] > .1)

# 3. Test Ecobee access, in certain conditions, the entire HVAC system
#    is shut down and the ecobee is not powered anymore
@test_loop('ecobee alive',
           "Ecobee has become inaccessible", "Ecobee is back",
           sleep = 60, min_failure = 5)
def sensor_is_running(device):
    return device.read(cache=False)

# 4. TODO: Install temperature sensors and monitor the delta T

# Pool filtering system
# ---------------------
# 1. When the Pool Pump malfunction, it stops itself in less than 2
#    minutes (see July 7th 2021 capacitor death)
class PoolRanLongEnough(threading.Thread):
    startRunning = None
    def __init__(self, config, seconds):
        super(PoolRanLongEnough, self).__init__()
        self.seconds = seconds
        self.sensor = config['sensors']

    @test_loop('PoolRanLongEnough',
               "Pool stopped after a few minutes")
    def run(self):
        usage = reader.read()
        if not self.startRunning:
            if usage[self.sensor] > .1:
                self.startRunning = datetime.now()
            return True
        if usage[self.sensor] > .1:
            return True
        if datetime.now() > self.startRunning + timedelta(seconds=self.seconds):
            self.startRunning = None
            return True
        return False

# 2. TODO: Out of range power consumption (June 21 2021: pool filter
#    head cracked)
# 3. Pool filter is dirty
class PoolFilterIsClean(threading.Thread):
    def __init__(self, config):
        super(PoolFilterIsClean, self).__init__()
        self.sensor = config['sensors']

    @test_loop('PoolFilterIsClean', 'Pool filter is dirty',
               sleep=24 * 60 * 60)
    def run(self):
        reader = SensorLogReader(datetime.now() - timedelta(days=1))
        power = []
        for current in iter(reader):
            if current[self.sensor] >= .4:
                power.append(current[self.sensor])
        return median(power) > 1.8

ev_lock = threading.Lock()
ev = None
def ev_is_connected():
    if not ev:
        return False
    with ev_lock:
        return ev.isConnected()

# Car
# ----------------
# 1. Car is in the garage but not plugged-in
class CarIsPluggedIn(threading.Thread):
    def __init__(self, config):
        super(CarIsPluggedIn, self).__init__()
        self.config = config
        self.pluggedIn = self.inTheGarage = datetime(1970, 1, 1)

    @test_loop('CarIsPluggedIn',
               'The car is in the garage but is not plugged in',
               end_msg = 'The car is now plugged in',
               sleep = 15)
    def run(self):
        if ev_is_connected():
            debug('Car is plugged in')
            self.pluggedIn = datetime.now()
            self.inTheGarage = datetime(1970, 1, 1)
            return True
        # Car is not connected, let's give it some time to exit the
        # garage
        now = datetime.now()
        if now < self.pluggedIn + timedelta(minutes=10):
            return True
        # Car detection
        if self.inTheGarage == datetime(1970, 1, 1):
            if self.config['obd_MAC'] in discover_devices():
                debug('Car entered the garage')
                self.inTheGarage = now
            return True
        # Car has been detected for a while but it still is not
        # plugged in
        return now < self.inTheGarage + minutes(minutes=10)

# 2. Car is connected but its state of charge is not up to date
class CarStateOfCharge(threading.Thread):
    def __init__(self, config):
        super(CarStateOfCharge, self).__init__()
        self.sensor = CarData(config)
        self.pluggedIn = datetime(1970, 1, 1)

    @test_loop('CarStateOfCharge',
               'Car has been plugged in but the state of charge is outdated',
               sleep=30)
    def run(self):
        if not ev_is_connected():
            self.pluggedIn = datetime(1970, 1, 1)
        elif not self.pluggedIn:
            self.pluggedIn = datetime.now()
        if datetime.now() < self.pluggedIn + timedelta(seconds=4 * 60):
            return True
        return self.sensor.read() and \
            self.sensor.datetime >= self.pluggedIn - timedelta(seconds=10 * 60)

def main():
    config = init(os.path.splitext(__file__)[0] + ".log")

    global reader
    reader = SensorReader(config['Emporia'])
    global ev
    ev = MyWallBox(config['Wallbox'])

    threading.Thread(target=hvac_yellow).start()
    threading.Thread(target=hvac_red).start()
    PoolRanLongEnough(config['Pool'], 3 * 60).start()
    PoolFilterIsClean(config['Pool']).start()
    CarIsPluggedIn(config['CarData']).start()
    CarStateOfCharge(config['CarData']).start()
    ecobee = MyEcobee(config['Ecobee'])
    threading.Thread(target=lambda: sensor_is_running(ecobee)).start()

    debug("... is now ready to run")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
