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
import time

from datetime import datetime, timedelta
from statistics import median
from subprocess import Popen

from bluetooth import discover_devices
from pyemvue.enums import Scale

from consumer import MyEcobee, MyWallBox
from sensor import Sensor, EmporiaProxy, CarData
from tools import init, alert, debug, SensorLogReader

status = {}
status_lock = threading.Lock()
def update_status(name, value):
    with status_lock:
        status[name] = value
    debug('%s:%s' % (name, value))

def test_loop(name, msg, end_msg = None, sleep = 15, end = False, min_failure = 1,
              on_fail=lambda *args: None):
    def inner(fun):
        def run_until_success(*args, **kwargs):
            failed = 0
            while True:
                ret = fun(*args, **kwargs)
                update_status(name, ret)
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
                    on_fail()
                    alert(msg)
                time.sleep(sleep)
        return run_until_success
    return inner

reader = None
class UsageReader(Sensor):
    """Encapsulation of the EmporiaProxy reader as test_loop."""

    def __init__(self, config):
        self.proxy = EmporiaProxy(config)

    @test_loop('Emporia read', "Failed to access Emporia", "Emporia is back",
               min_failure = 2, end = True)
    def read(self, scale=Scale.MINUTE.value):
        try:
            return self.proxy.read(scale)
        except:
            return False

# Heating, Ventilation, and Air Conditioning (HVAC)
# -------------------------------------------------
@test_loop('Heat pump yellow',
           "Heat pump running while air handler is stopped",
           "Heat pump/air handler is back to normal",
           min_failure = 2, sleep = 60)
def hvac_yellow():
    """When the Yellow wire is shunt by float T-switch the air handler
stops running but the Heat Pump is still running.
    """
    usage = reader.read()
    return not (usage['A/C'] > .1 and usage['air handler'] < .1)

@test_loop('heat pump red',
           "Air handler is running while Heat Pump is stopped",
           "Heat pump/air handler is back to normal",
           min_failure = 20, sleep = 60)
def hvac_red():
    """When the Red wire is shunt by float T-switch, the air handler keeps
running but the condensation and the heat pump do not. To avoid false
positive, we use a large min_failure parameter as my HVAC is
configured to run the air-handler for a little while after stopping
the A/C.
    """
    usage = reader.read()
    return not (usage['A/C'] < .1 and usage['air handler'] > .1)

@test_loop('ecobee alive',
           "Ecobee has become inaccessible", "Ecobee is back",
           sleep = 60, min_failure = 5)
def sensor_is_running(device):
    """Under certain conditions (no internet, HVAC issues), the entire
HVAC system is shut down and the ecobee is not powered anymore"""
    return device.read(cache=False)

# TODO: Install temperature sensors and monitor the delta T

# Pool Filtering System
# ---------------------
class PoolRanLongEnough(threading.Thread):
    """When the Pool Pump malfunction, it stops itself in less than 2
    minutes (see July 7th 2021 capacitor death).
    """
    start_running = None
    def __init__(self, config, seconds):
        super().__init__()
        self.seconds = seconds
        self.sensor = config['sensors']

    @test_loop('PoolRanLongEnough',
               "Pool stopped after a few minutes",
               sleep=60)
    def run(self):
        usage = reader.read()
        if not self.start_running:
            if usage[self.sensor] > .1:
                self.start_running = datetime.now()
            return True
        if usage[self.sensor] > .1:
            return True
        if datetime.now() > self.start_running + timedelta(seconds=self.seconds):
            self.start_running = None
            return True
        return False

# TODO: Out of range power consumption (June 21 2021: pool filter head
# cracked)

class PoolFilterIsClean(threading.Thread):
    """When the pool filter is dirty, the power consumption of the pool
pump drops.
    """
    def __init__(self, config):
        super().__init__()
        self.sensor = config['sensors']

    @test_loop('PoolFilterIsClean', 'Pool filter is dirty',
               sleep=24 * 60 * 60)
    def run(self):
        reader = SensorLogReader(datetime.now() - timedelta(days=1))
        power = []
        for current in iter(reader):
            if current[self.sensor] >= .4:
                power.append(current[self.sensor])
        return median(power) > 1.55

_charger_lock = threading.Lock()
_charger = None
def car_is_plugged_in():
    if not _charger:
        return False
    with _charger_lock:
        return _charger.isConnected()

# Car Charging and Monitoring
# ---------------------------
class CarIsPluggedIn(threading.Thread):
    """Detect when the car is in the garage but not plugged-in."""
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.plugged_in = self.in_the_garage = datetime(1970, 1, 1)

    @test_loop('CarIsPluggedIn',
               'The car is in the garage but is not plugged in',
               end_msg = 'The car is now plugged in',
               sleep = 15)
    def run(self):
        if car_is_plugged_in():
            self.plugged_in = datetime.now()
            self.in_the_garage = datetime(1970, 1, 1)
            return True
        # Car is not connected, let's give it some time to exit the
        # garage
        now = datetime.now()
        if now < self.plugged_in + timedelta(minutes=10):
            return True
        # Car detection
        if self.in_the_garage == datetime(1970, 1, 1):
            if self.config['obd_MAC'] in discover_devices():
                debug('Car entered the garage')
                self.in_the_garage = now
            return True
        # Car has been detected for a while but it still is not
        # plugged in
        return now < self.in_the_garage + timedelta(minutes=10)

class CarStateOfCharge(threading.Thread):
    """Car is connected but its state of charge is not up to date."""
    def __init__(self, config):
        super().__init__()
        self.sensor = CarData(config)
        self.plugged_in = datetime(1970, 1, 1)

    @test_loop('CarStateOfCharge',
               'Car has been plugged in but the state of charge is outdated',
               sleep=30)
    def run(self):
        if not car_is_plugged_in():
            self.plugged_in = datetime(1970, 1, 1)
        elif not self.plugged_in:
            self.plugged_in = datetime.now()
        if datetime.now() < self.plugged_in + timedelta(seconds=4 * 60):
            return True
        return self.sensor.read() and \
            self.sensor.datetime >= self.plugged_in - timedelta(seconds=10 * 60)

# Internet Connection / Wifi
# --------------------------
def restart_wifi():
    """Restart the wifi module using the RFKILL command."""
    debug('Restarting wifi...')
    with Popen(['rfkill', 'block', 'wifi']) as process:
        process.wait()
    time.sleep(10)
    with Popen(['rfkill', 'unblock', 'wifi']) as process:
        process.wait()
@test_loop('Internet access',
           'Lost Internet Access', 'Internet access restored',
           on_fail=restart_wifi, min_failure=12)
def internet_access():
    """Sometimes the wifi router misbehaves and the communications start
failing. Device disconnection and re-connection usually fixes it.
    """
    with status_lock:
        if 'Emporia read' in status and 'ecobee alive' in status:
            return bool(status['Emporia read'] or status['ecobee alive'])
    return True

def main():
    config = init(os.path.splitext(__file__)[0] + ".log")

    global reader
    reader = UsageReader(config['EmporiaProxy'])
    global _charger
    _charger = MyWallBox(config['Wallbox'])

    threading.Thread(target=hvac_yellow).start()
    threading.Thread(target=hvac_red).start()
    PoolRanLongEnough(config['Pool'], 3 * 60).start()
    PoolFilterIsClean(config['Pool']).start()
    CarIsPluggedIn(config['CarData']).start()
    CarStateOfCharge(config['CarData']).start()
    ecobee = MyEcobee(config['Ecobee'])
    threading.Thread(target=lambda: sensor_is_running(ecobee)).start()
    threading.Thread(target=internet_access).start()

    debug("... is now ready to run")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
