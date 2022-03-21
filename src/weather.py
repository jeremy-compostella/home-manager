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

'''Weather Sensor and a weather forecast Service implementation.'''

import os
import random
import sys
from datetime import datetime, timedelta
from select import select
from string import ascii_lowercase
from time import sleep

import pyowm
import Pyro5.api
import requests
from cachetools import TTLCache
from dateutil import parser, tz
from geopy.geocoders import Nominatim
from scipy.interpolate import interp1d

from monitor import MonitorProxy
from sensor import Sensor
from tools import NameServer, debug, init, log_exception, miles, my_excepthook
from watchdog import WatchdogProxy


class WeatherSensor(Sensor):
    '''Provide instantaneous weather information as a Sensor.

    It uses the OpenWeather API.

    '''
    #pylint: disable=too-few-public-methods
    def __init__(self, key, latitude, longitude):
        self.mgr = pyowm.OWM(key).weather_manager()
        self.latitude = latitude
        self.longitude = longitude
        self.cache = TTLCache(1, timedelta(seconds=59), datetime.now)

    @Pyro5.api.expose
    def read(self, **kwargs) -> dict:
        '''Return the current weather conditions.

        The conditions includes the 'temperature', 'wind_speed', 'wind_degree',
        'weather_code' and 'humidity' level information.

        For more details on the 'weather_code' field, consult
        https://openweathermap.org/weather-conditions.

        '''
        if 'weather' not in self.cache:
            obs = self.mgr.weather_at_coords(self.latitude, self.longitude)
            weather = obs.weather
            temperature = weather.temperature(unit='fahrenheit')['temp']
            self.cache['weather'] = {'temperature': temperature,
                                     'wind_speed': miles(weather.wnd['speed']),
                                     'wind_degree': weather.wnd['deg'],
                                     'weather_code': weather.weather_code,
                                     'humidity': weather.humidity}
        return self.cache['weather']

    @Pyro5.api.expose
    def units(self, **kwargs):
        return {'temperature': '°F',
                'wind_speed': 'mph',
                'wind_degree': '°',
                'weather_code': 'number',
                'humidity': 'humidity'}

class WeatherForecastService:
    '''Provide weather forecast information.

    It uses on the National Weather Service (NWS) API.

    '''
    #pylint: disable=too-few-public-methods
    API = 'https://api.weather.gov'
    DEGREE = {'N': 0, 'NNE': 22.5, 'NE': 45.0, 'ENE': 67.5,
              'E': 90.0, 'ESE': 112.5, 'SE': 135.0, 'SSE': 157.5,
              'S': 180.0, 'SSW': 202.5, 'SW': 225.0, 'WSW': 247.5,
              'W': 270.0, 'WNW': 292.5, 'NW': 315.0, 'NNW': 337.5}
    CONDITION = ['temperature', 'wind_speed', 'wind_degree']

    def __init__(self, latitude, longitude, monitor):
        self.latitude = latitude
        self.longitude = longitude
        self.monitor = monitor
        self.timezone = None
        self.interp = {}
        self.last_attempt = datetime.min

    @staticmethod
    def _get(url: str) -> dict:
        # The server often return stale data for identical request. The
        # following generates a random string to make each request different.
        flags = ''.join(random.choice(ascii_lowercase) for i in range(10))
        headers = {'accept': 'application/geo+json', 'Feature-Flags': flags}
        for _ in range(3):
            response = requests.get(url, headers=headers, timeout=3)
            if response.ok:
                return response.json()
            sleep(.2)
        raise RuntimeError('Could not access %s' % url)

    def _load_forecast_data(self):
        debug('Loading Forecast data')
        self.last_attempt = datetime.now()
        data = self._get(self.API + '/points/%.2f,%.2f' %
                         (self.latitude, self.longitude))
        try:
            self.timezone = tz.gettz(data['properties']['timeZone'])
            forecast_url = data['properties']['forecastHourly'] + '?units=us'
            periods = self._get(forecast_url)['properties']['periods']
        except KeyError as err:
            raise RuntimeError from err
        now = datetime.now().astimezone(self.timezone)
        valid_data = now <= (self._str2time(periods[0]['startTime'])
                             + timedelta(hours=2))
        if valid_data:
            times = [self._str2time(period['startTime']).timestamp() \
                     for period in periods]
            conditions = [self._conditions(period) for period in periods]
            for cond in self.CONDITION:
                self.interp[cond] = interp1d(times,
                                            [c[cond] for c in conditions])
        else:
            debug('%s forecast period is outdated' % periods[0]['startTime'])
        self.monitor.track('weather forecast data', valid_data)

    def _str2time(self, string: str):
        return parser.parse(string).astimezone(self.timezone)

    def _conditions(self, period):
        return {'temperature': period['temperature'],
                'wind_speed': int(period['windSpeed'].split(' ')[0]),
                'wind_degree': self.DEGREE[period['windDirection']]}

    def _forecast_and_timezone(self):
        interp = self.interp.get('temperature', None)
        if self.timezone is None or interp is None \
           or datetime.now() > self.last_attempt + timedelta(hours=1):
            self._load_forecast_data()
            interp = self.interp.get('temperature', None)
        if self.timezone is None or interp is None:
            raise RuntimeError('Could not get forecast data')

    @Pyro5.api.expose
    def conditions_at(self, target: datetime) -> dict:
        '''Return the condition at TARGET time.

        The conditions include the 'temperature', 'wind_speed' and
        'wind_degree'.

        '''
        if isinstance(target, str):
            target = parser.parse(target)
        self._forecast_and_timezone()
        timestamp = target.astimezone(self.timezone).timestamp()
        try:
            return {cond:self.interp[cond](timestamp).item() \
                    for cond in self.CONDITION}
        except ValueError as err:
            raise RuntimeError('%s weather data is not available' % target) \
                from err

    def _temperatures(self, hours):
        self._forecast_and_timezone()
        start = self.interp['temperature'].x[0]
        return [self.interp['temperature'](start + i * 60 * 60).item() \
                for i in range(hours)]

    @Pyro5.api.expose
    def minimum_temperature(self, hours=24):
        '''Minimum temperature in the next HOURS hours.'''
        return min(self._temperatures(hours))

    @Pyro5.api.expose
    def maximum_temperature(self, hours=24):
        '''Maximum temperature in the next HOURS hours.'''
        return max(self._temperatures(hours))

class WeatherProxy(Sensor):
    '''Helper class for weather Sensor and Service.

    This class is a wrapper of the weather sensor and service with exception
    handlers. It provides convenience for services using the weather Sensor
    and Service by suppressing the burden of locating them and handling the
    various remote object related errors.

    This class also introduces some direct attributes such as 'temperature',
    'wind_speed', 'wind_degree', 'weather_code' and 'humidity' providing
    simpler access to the current weather condition information. These fields
    can also be retrieved all at once using the 'read' method.

    It introduces the 'temperature_at(datetime)', 'wind_speed_at(datetime)' and
    'wind_direction_at(datetime)' methods providing a simpler access to
    targeted weather forecast information. These fields can also be retrieved
    all at once using the 'conditions_at(datetime)' method.

    '''
    def __init__(self, max_attempt=2, timeout=None):
        self.max_attempt = max_attempt
        self.timeout = timeout
        self.service = None
        self.sensor = None

    def __attempt(self, qualifier, func, *args):
        for _ in range(self.max_attempt):
            if not getattr(self, qualifier):
                try:
                    setattr(self, qualifier,
                            NameServer().locate(qualifier, 'weather'))
                    setattr(getattr(self, qualifier),
                            '_pyroTimeout', self.timeout)
                except Pyro5.errors.NamingError:
                    log_exception('Failed to locate the weather %s'
                                  % qualifier, *sys.exc_info())
                except Pyro5.errors.CommunicationError:
                    log_exception('Cannot communicate with the nameserver',
                                  *sys.exc_info())
            if getattr(self, qualifier):
                try:
                    return getattr(getattr(self, qualifier), func)(*args)
                except Pyro5.errors.PyroError:
                    log_exception('Communication failed with the weather %s'
                                  % qualifier, *sys.exc_info())
                    setattr(self, qualifier, None)
        raise RuntimeError('Could not communicate with the weather %s'
                           % qualifier)

    def read(self, **kwargs) -> dict:
        '''Return the instantaneous weather condition.'''
        return self.__attempt('sensor', 'read')

    def units(self, **kwargs) -> dict:
        '''Return the instantaneous weather condition.'''
        return self.__attempt('sensor', 'units')

    def conditions_at(self, date: datetime) -> dict:
        '''Return the forecast weather condition at 'date'.'''
        return self.__attempt('service', 'conditions_at', date)

    def _forecast(self, field):
        def inner(date):
            return self.conditions_at(date)[field[:-3]]
        return inner

    def __getattr__(self, name):
        if name in ['temperature', 'wind_speed', 'wind_degree',
                    'weather_code', 'humidity']:
            return self.read()[name]
        if name in ['temperature_at', 'wind_speed_at', 'wind_degree_at']:
            return self._forecast(name)
        if name in ['minimum_temperature', 'maximum_temperature']:
            def inner(*args, **kwargs):
                return self.__attempt('service', name, *args, **kwargs)
            return inner
        raise AttributeError("'%s' has no attribute '%s'"
                             % (self.__class__.name, name))

def main():
    '''Register and run the weather Sensor and weather forecast Service.'''
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    module_name = os.path.basename(base)
    config = init(base + '.log')

    locator = Nominatim(user_agent=config['general']['application'])
    location = locator.geocode(config['general']['address'])
    daemon = Pyro5.api.Daemon()

    nameserver = NameServer()
    service = WeatherForecastService(location.latitude, location.longitude,
                                     MonitorProxy())
    service_uri = daemon.register(service)
    nameserver.register_service(module_name, service_uri)
    sensor = WeatherSensor(config['OpenWeather']['key'],
                           location.latitude, location.longitude)
    sensor_uri = daemon.register(sensor)
    nameserver.register_sensor(module_name, sensor_uri)

    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        watchdog.register(os.getpid(), module_name)
        watchdog.kick(os.getpid())

        try:
            nameserver.register_service(module_name, service_uri)
            nameserver.register_sensor(module_name, sensor_uri)
        except RuntimeError:
            log_exception('Failed to register the sensor or the service',
                          *sys.exc_info())

        sockets, _, _ = select(daemon.sockets, [], [], 30)
        if sockets:
            daemon.events(sockets)

if __name__ == "__main__":
    main()
