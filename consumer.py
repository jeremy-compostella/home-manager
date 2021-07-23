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

import pytz
import shelve
import time
import sensor
import math
import requests

from math import floor
from datetime import datetime, timedelta
from pyecobee import *
from statistics import mean
from wallbox import Wallbox
from stat_sensor import SensorLogReader

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
        power = 0
        for usage in iter(reader):
            power = max(power, self.totalPower(usage))
        self._power = [ power ]

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
                return self.__detectSchedule()
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

class MyPoolPump(Consumer):
    def __init__(self, config):
        Consumer.__init__(self, config)

class MyWaterHeater(Consumer):
    def __init__(self, config):
        Consumer.__init__(self, config)

def minutes_to_datetime(minutes):
    return datetime.now().replace(hour=floor(minutes/60),
                                  minute=minutes % 60,
                                  second=0, microsecond=0)

def slot(hour, minute):
    return hour * 2 + round(minute / 30)

class MyEcobee(Sensor, Consumer):
    ecobee = None
    __identifier = None

    def __refreshTokens(self):
        self.ecobee.refresh_tokens()
        db = shelve.open(self.config["shelve_db"], protocol=2)
        db[self.config["name"]] = self.ecobee
        db.close()

    def __init__(self, config):
        Consumer.__init__(self, config)
        self.config = config
        try:
            db = shelve.open(config["shelve_db"], protocol=2)
            ecobee = db[config["name"]]
            db.close()
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
        thermostats = self.__try(lambda: self.ecobee.request_thermostats(sel))
        self.__identifier = thermostats.thermostat_list[0].identifier

    def __try(self, fun):
        for i in range(3):
            try:
                return fun()
            except EcobeeApiException as e:
                if e.status_code == 14:
                    self.__refreshTokens()
            except:
                pass
        return None

    temperature_cache = {}
    def temperatures(self):
        if not self.ecobee:
            return {}
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_sensors=True)
        thermostats = self.__try(lambda: self.ecobee.request_thermostats(sel))
        if thermostats == 'unknown' or thermostats == None:
            return self.temperature_cache
        temps = {}
        for s in thermostats.thermostat_list[0].remote_sensors:
            temps[s.name] = int(s.capability[0].value) / 10
        self.temperature_cache = temps
        return temps

    def read(self):
        return self.temperatures()

    def __program(self):
        if not self.ecobee:
            return None
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_program=True)
        thermostats = self.__try(lambda: self.ecobee.request_thermostats(sel))
        if thermostats == None:
            return None
        try:
            return thermostats.thermostat_list[0].program
        except IndexError:
            return None

    def programInfo(self, name):
        program = self.__program()
        if not program:
            return None
        climate = [ c for c in program.climates if c.name == name ][0]
        today = program.schedule[datetime.today().weekday()]
        minutes = 0
        start = None
        climateSensors=[ x.name for x in climate.sensors ]
        temps = { k:v for (k, v) in self.temperatures().items()
                  if k in climateSensors }
        for p in today:
            if p != climate.climate_ref and start:
                return { 'start': start,
                         'stop':minutes_to_datetime(minutes),
                         'current':mean(temps.values()),
                         'target':float(climate.cool_temp) / 10 }
            if not start and p == climate.climate_ref:
                start = minutes_to_datetime(minutes)
            minutes += 30
        return None

    def setProgramSchedule(self, name, start, stop):
        program = self.__program()
        if not program:
            return None
        climate = [ c for c in program.climates if c.name == name ][0]
        today = program.schedule[datetime.today().weekday()]
        prev = None
        for i in range(0, slot(start.hour, start.minute)):
            if today[i] == climate.climate_ref:
                if not prev:
                    raise Exception('Fatal error', 'Fatal error')
                today[i] = prev
            else:
                prev = today[i]
        for i in range(slot(start.hour, start.minute),
                       slot(stop.hour, stop.minute)):
            today[i] = climate.climate_ref
        for i in range(slot(23, 30), slot(stop.hour, stop.minute) - 1, -1):
            if (today[i] != climate.climate_ref):
                prev = today[i]
            else:
                today[i] = prev
        sel = Selection(selection_type=SelectionType.REGISTERED.value,
                        selection_match='',
                        include_program=True)
        return self.ecobee.update_thermostats(sel,
             thermostat=Thermostat(identifier=self.__identifier,
                                   program=program))

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
        thermostats = self.__try(lambda: self.ecobee.request_thermostats(sel))
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
        climateSensors=[ x.name for x in climate.sensors ]
        temps = { k:v for (k, v) in self.temperatures().items()
                  if k in climateSensors }
        current = mean(temps.values())
        if current > climate.cool_temp / 10 or current < climate.heat_temp / 10:
            return True
        return False

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
                self.logger.warning("Unexpected error in __try")
                time.sleep(5)
        raise Exception("Communication Error",
                        "Fail to communicate with Wallbox server")

    def __connect(self):
        if self.connectionExpiration == None or \
           datetime.now() > self.connectionExpiration:
            self.__try(lambda: self.w.authenticate())
            self.connectionExpiration = datetime.now() + timedelta(hours = 1)

    def __init__(self, config, logger, chargerID = None):
        Consumer.__init__(self, config)
        self.logger = logger
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

    def __readStatus(self):
        if self.status and datetime.now() < self.statusExpiration:
            return self.status
        self.__connect()
        self.status = self.__try(lambda: self.w.getChargerStatus(self.charger))
        self.statusExpiration = datetime.now() + timedelta(seconds=15)
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
            self.logger.debug("Waiting for '%s'", name)
            time.sleep(3)
        self.logger.warning("Giving up on '%s'", name)

    def startCharging(self, maxCurrent = None):
        if self.isCharging():
            return
        if not self.isConnected():
            self.logger.debug("Not connected, cannot charge")
            return
        if maxCurrent:
            self.setMaxChargingCurrent(maxCurrent)
        self.logger.debug("Start charging at %dA - %.2f KWh added",
                          maxCurrent, self.getAddedEnergy())
        self.__try(lambda: self.w.resumeChargingSession(self.charger))
        self.__waitFor(lambda: self.isCharging(), "isCharging()", 30)

    def stopCharging(self):
        self.logger.debug("Stop charging - %.2f KWh added", self.getAddedEnergy())
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
            self.logger.warning("Out of bound maxCurrent")
            return
        if self.getMaxChargingCurrent() == maxCurrent:
            return
        self.logger.debug("Adjusting from %dA to %dA - %.2f KWh added",
                          self.getMaxChargingCurrent(), maxCurrent,
                          self.getAddedEnergy())
        self.__try(lambda: self.w.setMaxChargingCurrent(self.charger, maxCurrent))
        self.__waitFor(lambda: (self.getMaxChargingCurrent() == maxCurrent),
                       "maxCurrent == %d" % (maxCurrent), 10)

    def isRunning(self):
        return self.isCharging()

    def stop(self):
        if self.isCharging():
            self.stopCharging()
        self.setMaxChargingCurrent(self.getMinAvailableCurrent())

    def powerToCurrent(self, power):
        try:
            cur = self.getMinAvailableCurrent() + len(self.power) - 1
            return cur - next(i for i, p in enumerate(reversed(self.power)) if p <= power)
        except StopIteration:
            return 0

    def runWith(self, power):
        current = self.powerToCurrent(power)
        if current == 0:
            return self.stop()
        if not self.isCharging():
            self.startCharging(current)
        else:
            self.setMaxChargingCurrent(current)
