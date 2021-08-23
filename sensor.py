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

import json
import logging
import pyowm
import time

from datetime import datetime, timedelta
from pyemvue import PyEmVue
from pyemvue.enums import Scale, Unit
from wirelesstagpy import WirelessTags
from multiprocessing.connection import Client

class Sensor:
    def read():
        return { }

class MyOpenWeather(Sensor):
    cache = { }

    def __try(self, fun):
        for i in range(5):
            try:
                return fun()
            except:
                time.sleep(3)
        return None

    def __init__(self, config):
        owm=pyowm.OWM(config["key"])
        self.mgr = owm.weather_manager()
        self.place = "%s,%s" % (config["city"], config["country"])

    def read(self):
        obs = self.__try(lambda: self.mgr.weather_at_place(self.place))
        if obs:
            w = obs.weather
            self.cache = { 'outdoor temp': w.temperature(unit='fahrenheit')['temp'],
                           'humidity':w.humidity,
                           'status':w.status,
                           'detailed status':w.detailed_status }
        return self.cache

    def isNightTime(self):
        obs = self.__try(lambda: self.mgr.weather_at_place(self.place))
        if not obs:
            return False
        sunrise = datetime.fromtimestamp(obs.weather.sunrise_time())
        sunset = datetime.fromtimestamp(obs.weather.sunset_time())
        return not sunrise < datetime.now() < sunset

class MyVue2(Sensor):
    deviceGids = []
    mapping = None
    usage = { }

    def __connect(self):
        for i in range(3):
            try:
                ret = self.vue.login(id_token=self.data['idToken'],
                                     access_token=self.data['accessToken'],
                                     refresh_token=self.data['refreshToken'],
                                     token_storage_file=self.tokenFile)
                if ret:
                    return

                logging.warning("Unexpected error in __connect")
                sleep(5)
            except:
                raise Exception("Communication Error",
                                "Fail to communicate with Emporia server")

    def __init__(self, config):
        self.vue = PyEmVue()
        self.tokenFile = config["token_file"]
        self.vue.login(username=config["login"],
                       password=config["password"],
                       token_storage_file=self.tokenFile)
        with open(self.tokenFile) as f:
            self.data = json.load(f)
        self.__connect()
        devices = self.vue.get_devices()
        for device in devices:
            self.deviceGids.append(device.device_gid)
        self.mapping = config["map"].split(",")

    def __read(self, scale):
        for i in range(3):
            try:
                return self.vue.get_devices_usage(self.deviceGids, None,
                                                  scale=scale,
                                                  unit=Unit.KWH.value)
            except:
                logging.warning("Unexpected error in __read")
                time.sleep(5)
        raise Exception("Communication Error",
                        "Fail to communicate with Emporia server")

    def read(self, scale=Scale.MINUTE.value):
        usage = self.__read(scale)
        if len(usage) < len(self.mapping):
            logging.debug("devices list too short (%d), reconnecting...",
                          len(usage))
            self.__connect()
            usage = self.__read(scale)
            if len(usage) < len(self.mapping):
                raise Exception("Communication Error",
                                "device list still too short")
        factor={ Scale.SECOND.value: 60 * 60,
                 Scale.MINUTE.value: 60,
                 Scale.MINUTES_15.value: 4,
                 Scale.HOUR.value: 1 }
        for i in range(len(self.mapping)):
            self.usage[self.mapping[i]] = usage[i].usage * factor[scale]
        return self.usage

class EmporiaProxy(Sensor):
    def __init__(self, config):
        self.address = (config['host'], int(config['port']))

    def read(self, scale=Scale.MINUTE.value):
        try:
            proxy = Client(self.address)
            proxy.send(scale)
            data = proxy.recv()
            proxy.close()
            return data
        except:
            return {}

class MyWirelessTag(Sensor):
    expirationTime = None

    def __connect(self):
        if self.expirationTime == None or \
           datetime.now() > self.expirationTime:
            self.api = WirelessTags(username=self.config['login'],
                                    password=self.config['password'])
            self.expirationTime = datetime.now() + timedelta(0, 10 * 60)

    def __init__(self, config):
        self.config = config
        self.__connect()

    def read(self):
        self.__connect()
        value = { }
        for uuid, tag in self.api.load_tags().items():
            value[tag.name] = tag.temperature * 1.8 + 32
        return value

class CarData(Sensor):
    def __init__(self, config):
        self.address = (config['host'], int(config['port']))
        self.__datetime = datetime(1970, 1, 1)
        self.__cache = None

    def read(self):
        try:
            data = Client(self.address).recv()
            if data:
                self.__datetime = data.pop('time')
                self.__cache = data
        except:
            pass
        return self.__cache

    @property
    def datetime(self):
        return self.__datetime
