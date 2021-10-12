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

import math
import pytz
import requests
import sensor
import sys
import time

from datetime import datetime, timedelta
from math import floor, ceil
from pyecobee import *
from statistics import mean, median
from tools import *
from wallbox import Wallbox

class Consumer:
    __validUpTo = None

    def __detectSchedule(self):
        try:
            reader = SensorLogReader(datetime.now() - timedelta(days=1))
        except FileNotFoundError:
            print("File not found")
            return
        running = False
        almost_a_day = timedelta(0, 24 * 60 * 60 - 60)
        for val in iter(reader):
            if self.isRunning(val):
                if not running:
                    self._schedule.append(val['time'] + almost_a_day)
                    running = True
                continue
            running = False
        self.__validUpTo = datetime.now().replace(hour=23, minute=59)

    def __updateSchedule(self):
        if self.__validUpTo and datetime.now() >= self.__validUpTo:
            self.schedule = [ ]
            self.__detectSchedule()

    def __detectPower(self):
        try:
            reader = SensorLogReader(datetime.now() - timedelta(days=1))
        except FileNotFoundError:
            return [ 1 ]
        power = []
        for usage in iter(reader):
            if self.totalPower(usage) > .8:
                power.append(self.totalPower(usage))
        self._power = [ median(power) ]

    def __init__(self, config):
        self._name = config.name
        self.description = config['description']
        self._sensors = config['sensors'].split(',')
        self._power = [ ]
        if 'power' in config:
            if config['power'] == 'auto':
                self.__detectPower()
            else:
                self._power = [ float(config['power']) ]
        self._schedule = []
        if 'schedule' in config:
            if config['schedule'] == 'auto':
                self.__detectSchedule()
                return
            for schedule in config['schedule'].split(","):
                t = datetime.now()
                start = [ int(x) for x in schedule.split(':') ]
                t = t.replace(hour=start[0], minute=start[1],
                              second=start[2] if len(start) == 3 else 0)
                self._schedule.append(t)

    @property
    def name(self):
        return self._name

    @property
    def sensors(self):
        return self._sensors

    @property
    def power(self):
        return self._power

    def totalPower(self, usage):
        cur = 0
        for sensor in self._sensors:
            cur += usage[sensor]
        return cur

    def isRunning(self, usage):
        return self.totalPower(usage) >= 0.1

    def isAboutToStart(self):
        self.__updateSchedule()
        now = datetime.now()
        for t in self._schedule:
            if t - timedelta(0, 90) <= now <= t + timedelta(0, 30):
                return True
        return False

class OtherSection:
    name = 'Other'
    dictionary = { 'description':'Other',
                   'sensors':'' }
    def __init__(self):
        pass
    def __contains__(self, key):
        key in self.dictionary
    def __getitem__(self, key):
        return self.dictionary[key]

class Other(Consumer):
    def __init__(self, consumers):
        Consumer.__init__(self, OtherSection())
        self._sensors = [ ]
        for c in consumers:
            self._sensors += c.sensors

    def totalPower(self, usage):
        return -1 * (sum([ usage[s] for s in self.sensors ]) + usage['solar'] - usage['net'])

class MyEcobeeProgram():
    def __get_program(self):
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_program=True)
        thermostats = self.ecobee.request_thermostats(sel)
        return thermostats.thermostat_list[0].program

    def __reschedule(self, start, stop):
        program = self.__get_program()
        climate = [ c for c in program.climates if c.name == self.name ][0]
        today = program.schedule[start.weekday()]
        prev = None
        for i in range(0, slot(start.hour, start.minute, ceil)):
            if today[i] == climate.climate_ref:
                today[i] = prev
            else:
                prev = today[i]
        for i in range(slot(start.hour, start.minute, floor),
                       slot(stop.hour, stop.minute, ceil)):
            today[i] = climate.climate_ref
        for i in range(slot(23, 30), slot(stop.hour, stop.minute, floor) - 1, -1):
            if today[i] != climate.climate_ref:
                prev = today[i]
            else:
                today[i] = prev
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_program=True)
        self._started_at = datetime.now() if start <= datetime.now() < stop else None
        thermostat=Thermostat(identifier=self.ecobee.identifier,
                              program=program)
        self.ecobee.update_thermostats(sel, thermostat=thermostat)
        self._start = start
        self._stop = stop

    def restore(self):
        self.__reschedule(self._saved_start, self._saved_stop)
        self._alterated = False

    def read(self):
        program = self.__get_program()
        climate = [ c for c in program.climates if c.name == self.name ][0]
        today = program.schedule[datetime.today().weekday()]
        minutes = 0
        start = None
        climate_sensors=[ x.name for x in climate.sensors ]
        for p in today:
            if p != climate.climate_ref and start:
                return { 'start':start,
                         'stop':minutes_to_datetime(minutes),
                         'sensors':climate_sensors,
                         'target':float(climate.cool_temp) / 10 }
                return
            if not start and p == climate.climate_ref:
                start = minutes_to_datetime(minutes)
            minutes += 30
        raise NameError("Could not find '%s' program" % self.name)

    def load(self):
        settings = self.read()
        self._start = self._saved_start = settings['start']
        self._stop = self._saved_stop = settings['stop']
        self.sensors = settings['sensors']
        self.target = settings['target']
        if self._start <= datetime.now() < self._stop:
            self._started_at = self._start
        else:
            self._started_at = None
        self._alterated = False

    def __init__(self, ecobee, name):
        self.name = name
        self.ecobee = ecobee
        self.load()
        self._started_at = None

    @property
    def start(self):
        return self._start

    @start.setter
    def start(self, value):
        value = value.replace(minute=(floor(value.minute / 30) * 30),
                              second=0, microsecond=0)
        if value > self._stop:
            raise ValueError('start must be before stop')
        for _ in range(3):
            self.__reschedule(value, self._stop)
            try:
                settings=self.read()
            except NameError as e:
                if value == self._stop:
                    break
            if settings['start'] == value:
                break
            debug('New configuration not applied')

    @property
    def stop(self):
        return self._stop

    @stop.setter
    def stop(self, value):
        value = value.replace(second=0, microsecond=0)
        minutes=ceil(value.minute / 30) * 30
        if minutes == 60:
            value = value.replace(hour=value.hour + 1, minute=0)
        else:
            value = value.replace(minute=minutes)
        if value < self._start:
            raise ValueError('stop must be after start')
        if value != self._stop:
            self.__reschedule(self._start, value)

    @property
    def is_running(self):
        return self._start <= datetime.now() <= self._stop

    @property
    def has_been_alterated(self):
        return self._start != self._saved_start or \
            self._stop != self._saved_stop

    def has_run_for(self):
        if not self.is_running:
            return timedelta(minutes=0)
        return datetime.now() - (self._started_at if self._started_at else self._start)

    def temperature_deviation(self):
        temps = { k:v for (k, v) in self.ecobee.temperatures().items()
                 if k in self.sensors }
        return mean(temps.values()) - self.target

    def time_remaining(self):
        return self._stop - datetime.now()

    def is_over(self):
        return datetime.now() >= self._stop

    def starting_in_less_than(self, delta):
        return self._start - datetime.now() <= delta

def minutes_to_datetime(minutes):
    return datetime.now().replace(hour=floor(minutes/60),
                                  minute=minutes % 60,
                                  second=0, microsecond=0)

def slot(hour, minute, round_fun=round):
    return hour * 2 + round_fun(minute / 30)

class MyEcobee(Sensor, Consumer):
    ecobee = None
    identifier = None

    def __refreshTokens(self):
        self.ecobee.refresh_tokens()
        with get_storage() as db:
            db[self.config["name"]] = self.ecobee

    def __init__(self, config):
        Consumer.__init__(self, config)
        self.config = config
        try:
            with get_storage() as db:
                ecobee = db[config["name"]]
        except KeyError:
            print("KeyError")
            return

        if ecobee.authorization_token is None or \
           ecobee.access_token is None:
            return

        self.ecobee = ecobee
        if datetime.now(pytz.utc) >= self.ecobee.access_token_expires_on:
            self.__refreshTokens()

        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='')
        thermostats = self.request_thermostats(sel)
        self.identifier = thermostats.thermostat_list[0].identifier

    def __try(self, fun):
        for i in range(3):
            try:
                return fun()
            except EcobeeApiException as e:
                if e.status_code == 14:
                    debug("Ecobee: Failed due to expired tokens")
                    self.__refreshTokens()
                else:
                    debug("Ecobee: e.status_code=%d" % e.status_code)
            except:
                debug("Ecobee: unexpected exception")
        return None

    def request_thermostats(self, *args, **kwargs):
        return self.__try(lambda: self.ecobee.request_thermostats(*args, **kwargs))

    def update_thermostats(self, *args, **kwargs):
        return self.__try(lambda: self.ecobee.update_thermostats(*args, **kwargs))

    temperature_cache = {}
    def temperatures(self, cache = True):
        if not self.ecobee:
            return {}
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_sensors=True)
        thermostats = self.request_thermostats(sel)
        if thermostats == 'unknown' or thermostats == None:
            if cache:
                return self.temperature_cache
            else:
                return None
        temps = {}
        for s in thermostats.thermostat_list[0].remote_sensors:
            temps[s.name] = int(s.capability[0].value) / 10
        self.temperature_cache = temps
        return temps

    def read(self, cache = True):
        return self.temperatures(cache)

    def mode(self):
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='', include_settings=True)
        thermostats = self.request_thermostats(sel)
        if thermostats == 'unknown' or thermostats == None:
            return None
        return thermostats.thermostat_list[0].settings.hvac_mode

    def isAboutToStart(self):
        if not self.ecobee:
            return False
        t = time.localtime()
        if t.tm_min not in [ 0, 29, 30, 59 ]:
            return False
        if t.tm_min in [ 29, 59 ]:
            if t.tm_sec < 20:
                return False
        elif t.tm_sec > 50:
            return False
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_program=True, include_equipment_status=True)
        thermostats = self.request_thermostats(sel)
        if thermostats == None:
            return False
        thermostat = thermostats.thermostat_list[0]
        if thermostat.equipment_status != "" and \
           thermostat.equipment_status != "fan":
            return False
        program = thermostats.thermostat_list[0].program
        today = program.schedule[datetime.today().weekday()]
        slot = t.tm_hour * 2 + round((t.tm_min + 1) / 30)
        climate = [ x for x in program.climates if x.climate_ref == today[slot] ][0]
        climate_sensors=[ x.name for x in climate.sensors ]
        temps = { k:v for (k, v) in self.temperatures().items()
                  if k in climate_sensors }
        current = mean(temps.values())
        if current >= climate.cool_temp / 10 + .5 or \
           current <= climate.heat_temp / 10 - .5:
            return True
        return False

    def get_program(self, name):
        return MyEcobeeProgram(self, name)

class MyWallBox(Consumer):
    MIN_AVAILABLE_CURRENT = 6
    FULLY_CHARGED_STATUS  = "Connected: waiting for car demand"
    CONNECTED_STATUS      = [ "Charging",
                              FULLY_CHARGED_STATUS,
                              "Connected: waiting for next schedule",
                              "Paused by user" ]

    connectionExpiration = None
    status = None
    statusExpiration = None

    def __try(self, fun):
        for i in range(5):
            try:
                return fun()
            except:
                debug("Unexpected error in %s.__try" % type(self).__name__)
                time.sleep(5)
        raise Exception("Communication Error",
                        "Fail to communicate with Wallbox server")

    def __connect(self):
        if self.connectionExpiration == None or \
           datetime.now() > self.connectionExpiration:
            self.__try(lambda: self.w.authenticate())
            self.connectionExpiration = datetime.now() + timedelta(hours = 1)

    def __init__(self, config, chargerID = None):
        Consumer.__init__(self, config)
        self.w = Wallbox(config["login"], config["password"], requestGetTimeout=30)
        self.__connect()
        if chargerID:
            self.charger = chargerID
        else:
            chargers = self.__try(lambda: self.w.getChargersList())
            if len(chargers) != 1:
                sys.exit(1, "Only one charger expected")
            self.charger = chargers[0]
        w_amp = range(self.getMinAvailableCurrent(), self.getMaxAvailableCurrent())
        self._power = [ i * 240 / 1000 for i in w_amp ]
        if 'current_power' in config:
            for current, power in eval(config['current_power']).items():
                self._power[current - self.getMinAvailableCurrent()] = power

    def __readStatus(self):
        if self.status and datetime.now() < self.statusExpiration:
            return self.status
        self.__connect()
        self.status = self.__try(lambda: self.w.getChargerStatus(self.charger))
        self.statusExpiration = datetime.now() + timedelta(seconds=3)
        return self.status

    def isConnected(self):
        """Return True if the car is connected to the charger"""
        self.__readStatus()
        return self.status["status_description"] in self.CONNECTED_STATUS

    def isCharging(self):
        """Return True if the car is charging"""
        self.__readStatus()
        return self.status["status_description"] == "Charging"

    def isFullyCharged(self):
        """Return True if the car is fully charged"""
        self.__readStatus()
        return self.status["status_description"] == self.FULLY_CHARGED_STATUS

    def getAddedEnergy(self):
        """Return the added energy of the current charging session"""
        self.__readStatus()
        return self.status["added_energy"]

    def carIsSlowingDownTheCharge(self):
        """When almost fully charged, the car reduces the charging rate."""
        self.__readStatus()
        return self.isCharging() and \
            self.status["charging_power"] < \
            self.status["config_data"]["max_charging_current"] * 0.240 * 0.7

    def __invalidateCache(self):
        self.status = None

    def __waitFor(self, condition, name, timeout, sleep = 3):
        for i in range(math.ceil(timeout / sleep)):
            self.__invalidateCache()
            self.__readStatus()
            if condition():
                return
            debug("Waiting for '%s'" % name)
            time.sleep(3)
        debug("Giving up on '%s'" % name)

    def startCharging(self, maxCurrent = None):
        if self.isCharging():
            return
        if not self.isConnected():
            debug("Not connected, cannot charge")
            return
        if maxCurrent:
            self.setMaxChargingCurrent(maxCurrent)
        debug("Start charging at %dA - %.2f KWh added" %
              (maxCurrent, self.getAddedEnergy()))
        self.__try(lambda: self.w.resumeChargingSession(self.charger))
        self.__waitFor(lambda: self.isCharging(), "isCharging()", 30)

    def stopCharging(self):
        debug("Stop charging - %.2f KWh added" % self.getAddedEnergy())
        self.__try(lambda: self.w.pauseChargingSession(self.charger))
        self.__waitFor(lambda: not self.isCharging(), "not isCharging()", 40)

    def getMaxChargingCurrent(self):
        self.__readStatus()
        return self.status["config_data"]["max_charging_current"]

    def getMinAvailableCurrent(self):
        return self.MIN_AVAILABLE_CURRENT

    def getMaxAvailableCurrent(self):
        self.__readStatus()
        return self.status['max_available_power']

    def setMaxChargingCurrent(self, maxCurrent):
        if maxCurrent < self.getMinAvailableCurrent() or \
           maxCurrent > self.getMaxAvailableCurrent():
            debug("Out of bound maxCurrent")
            return
        if self.getMaxChargingCurrent() == maxCurrent:
            return
        debug("Adjusting from %dA to %dA - %.2f KWh added" %
              (self.getMaxChargingCurrent(), maxCurrent, self.getAddedEnergy()))
        self.__try(lambda: self.w.setMaxChargingCurrent(self.charger, maxCurrent))
        self.__waitFor(lambda: (self.getMaxChargingCurrent() == maxCurrent),
                       "maxCurrent == %d" % (maxCurrent), 10)

    def isRunning(self):
        return self.isCharging()

    def stop(self):
        if self.isCharging():
            self.stopCharging()
        self.setMaxChargingCurrent(self.getMinAvailableCurrent())

    def powerToCurrent(self, power, maximize=False):
        try:
            cur = next(i for i, p in enumerate(reversed(self.power)) if p <= power)
            cur = len(self.power) - cur - 1
        except StopIteration:
            return 0
        if maximize:
            try:
                if power - self.power[cur] > self.power[cur + 1] - power:
                    cur += 1
            except IndexError:
                pass
        return cur + self.getMinAvailableCurrent()

    def runWith(self, power, maximize=False):
        current = self.powerToCurrent(power, maximize)
        if current == 0:
            return self.stop()
        if not self.isCharging():
            self.startCharging(current)
        else:
            self.setMaxChargingCurrent(current)
