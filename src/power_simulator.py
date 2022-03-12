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

'''This module implements a power usage simulation based on the pvlib library.

'''

import os
import sys
from bisect import bisect_right
from datetime import datetime, timedelta
from select import select
from statistics import mean

import pandas as pd
import Pyro5.api
import pytz
from cachetools import Cache
from dateutil import parser
from geopy.geocoders import Nominatim
from pvlib import location as pvlib_location
from pvlib import modelchain, pvsystem, solarposition
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS
from timezonefinder import TimezoneFinder

from power_sensor import CacheEntry, RecordScale
from sensor import Sensor
from tools import (NameServer, Settings, celsius, debug, init, log_exception,
                   meter_per_second, my_excepthook)
from watchdog import WatchdogProxy
from weather import WeatherProxy

PV_SYSTEM = {'installation_date': '2021-04-02',
             # Mount information
             'surface_tilt': 30, # Degrees
             'surface_azimuth': 180, # Degrees
             # Module: 335W High Efficiency LG NeON 2 Solar Panel
             'module_power_at_reference_conditions': 335,
             'module_reference_temperature': 20,
             'module_reference_irradiance': 100,
             'module_temperature_coefficient_of_power': -0.0025,
             'first_year_degradation': 2,
             'other_year_degradation': 0.33,
             # Micro-inverter: Enphase IQ 7
             'inverter_peak_output_power': 252.5 * 8 * 3,
             'temperature_model': 'open_rack_glass_glass',
             # Layout
             'modules_per_string': 8,
             'strings': 3}

DEFAULT_SETTINGS = {'base_power': 0.4,
                    'max_loop_duration': 60}

MODULE_NAME = 'power_simulator'

class ModelFactory:
    '''Factory pvlib Model Chain.

    'conf' is a dictionary of parameters describing the PV system.

    '''
    # pylint: disable=too-few-public-methods
    # As a factory object, it only have one method
    def __init__(self, conf):
        self.conf = conf

    def _power_at_reference_conditions(self, date):
        if not date:
            date = datetime.now()
        pdc = self.conf.module_power_at_reference_conditions / 1000
        installation_date = parser.parse(self.conf.installation_date)
        years = (date - installation_date.date()).days / 365.2422
        if years <= 1:
            return pdc * ((100 - years * self.conf.first_year_degradation)
                          / 100)
        percent = 100 - self.conf.first_year_degradation \
            - self.conf.other_year_degradation * (years - 1)
        return pdc * percent / 100

    def get_model(self, date, latitude, longitude) -> modelchain.ModelChain:
        '''ModelChain at 'date' time and latitude and longitude location.'''
        mount = pvsystem.FixedMount(surface_tilt=self.conf.surface_tilt,
                                    surface_azimuth=self.conf.surface_azimuth,
                                    racking_model='open_rack')
        module_parameters = {
            'pdc0': self._power_at_reference_conditions(date),
            'gamma_pdc': self.conf.module_temperature_coefficient_of_power,
            'temp_ref': self.conf.module_reference_temperature,
            'irrad_ref': self.conf.module_reference_irradiance}
        temperature_models = TEMPERATURE_MODEL_PARAMETERS['sapm']
        temperature_model = temperature_models[self.conf.temperature_model]
        array = pvsystem.Array(mount=mount,
                               module_parameters=module_parameters,
                               modules_per_string=self.conf.modules_per_string,
                               strings=self.conf.strings,
                               temperature_model_parameters=temperature_model)
        inverter = {'pdc0': self.conf.inverter_peak_output_power / 1000}
        system = pvsystem.PVSystem(arrays=[array],
                                   inverter_parameters=inverter)
        location = pvlib_location.Location(latitude, longitude)
        return modelchain.ModelChain(system, location,
                                     spectral_model='no_loss',
                                     aoi_model='no_loss')

class PowerSimulator(Sensor):
    '''This Sensor class implementation provides power consumption simulation.

    Similarly to the power_sensor Sensor, the 'read' method returns a record of
    power usage except that the data is rather simulated than read from an
    actual sensor. The simulation is based on a PV Model, a base power
    consumption and the Task status.

    '''
    SCALES = [RecordScale.SECOND, RecordScale.MINUTE]

    def __init__(self, model_factory, settings, weather):
        self.model_factory = model_factory
        self.settings = settings
        self.weather = weather
        self._date = None
        self._model = None
        self.cache = {scale:CacheEntry(scale) for scale in self.SCALES}
        self.daylight_cache = Cache(10)

    def model(self, date=None):
        '''Return the PV Model Chain at 'date'.

        If 'date' is None, it returns the PV Model Chain as for today.'''
        if not date:
            date = datetime.now()
        if self._date != date.date():
            self._date = date.date()
            self._model = self.model_factory.get_model(self._date,
                                                       self.settings.latitude,
                                                       self.settings.longitude)
        return self._model

    def clear_sky_local_weather(self, times):
        '''Return a local weather DataFrame assuming clear sky for 'times'.'''
        location = pvlib_location.Location(self.settings.latitude,
                                           self.settings.longitude)
        return location.get_clearsky(times)

    @staticmethod
    def _norm_date(date: datetime) -> datetime:
        return date.replace(second=0, microsecond=0)

    def power_produced_in_the_previous_minute(self):
        '''Return the estimation of power produced in the previous minute.'''
        date = self._norm_date(datetime.now()) - timedelta(minutes=1)
        times = pd.date_range(date.strftime('%Y-%m-%d %H:%M:00'),
                              date.strftime('%Y-%m-%d %H:%M:59'), freq='1S',
                              tz=pytz.timezone(self.settings.timezone))
        weather = self.clear_sky_local_weather(times)
        weather['temp_air'] = [celsius(self.weather.temperature_at(date)) \
                               for _ in range(60)]
        weather['wind_speed'] = [meter_per_second(self.weather.wind_speed_at(date)) \
                                 for _ in range(60)]
        model = self.model()
        model.run_model(weather)
        return mean(model.results.ac)

    @Pyro5.api.expose
    def read(self, **kwargs: dict) -> dict:
        '''Return an instant record of power usage.

        The optional SCALE keyword argument, limited to RecordScale.SECOND and
        RecordScale.MINUTE indicates which time unit resolution can be supplied
        to read with a different scale order. By default, the resolution is
        RecordScale.MINUTE.

        The power usage record is an estimate based on the estimation of the
        produced current, the 'base_power' and the running tasks.

        '''
        scale = RecordScale(kwargs.get('scale', RecordScale.MINUTE))
        if scale not in self.cache.keys():
            raise ValueError('%s is not a supported scale' % scale)
        if not self.cache[scale].has_expired():
            return self.cache[scale].value
        record = {key:0.0 for key in self.settings.device_map}
        if scale == RecordScale.MINUTE:
            power = self.power_produced_in_the_previous_minute()
        else:
            power = self.power
        record['solar'] = -power
        record['net'] = self.settings.base_power - power
        for task in [task for _, task in NameServer().tasks() \
                     if task.is_running()]:
            keys = task.keys
            usage = task.power / len(keys)
            for key in keys:
                record[key] = usage
            record['net'] += task.power
        self.cache[scale].value = record
        return self.cache[scale].value

    @Pyro5.api.expose
    def units(self, **kwargs: dict) -> dict:
        scale = RecordScale(kwargs.get('scale', RecordScale.MINUTE))
        if scale not in self.cache.keys():
            raise ValueError('%s is not a supported scale' % scale)
        record = self.cache[scale].value
        if not record:
            record = self.read(scale=scale)
        return {k:'kW' for k, v in record.items()}

    @property
    @Pyro5.api.expose
    def power(self) -> float:
        '''Estimated power produced at the time this method is called.'''
        return self.power_at(datetime.now(), self.weather.temperature,
                             self.weather.wind_speed)

    @Pyro5.api.expose
    def power_at(self, date: datetime, temp_air=None, wind_speed=None) -> float:
        '''Estimated power produced at 'date' time.

        If 'temp_air' or 'wind_speed' are not supplied, they are read from the
        'weather' forecast service.

        '''
        if isinstance(date, str):
            date = parser.parse(date)
        date = self._norm_date(date)
        date_str = date.strftime('%Y-%m-%d %H:%M:%S')
        times = pd.date_range(date_str, date_str, freq='1S',
                              tz=pytz.timezone(self.settings.timezone))
        if not temp_air:
            temp_air = self.weather.temperature_at(date)
        if not wind_speed:
            wind_speed = self.weather.wind_speed_at(date)
        weather = self.clear_sky_local_weather(times)
        weather['temp_air'] = celsius(temp_air)
        weather['wind_speed'] = meter_per_second(wind_speed)
        model = self.model()
        model.run_model(weather)
        return model.results.ac[0]

    @property
    def daytime(self) -> tuple:
        '''Return a couple of datetime objects defining today daytime.'''
        return self.daytime_at(datetime.now())

    def daytime_at(self, date=None):
        '''Return a couple of datetime objects defining 'date' daytime.'''
        day = date.date()
        if day in self.daylight_cache:
            return self.daylight_cache[day]
        date = self._norm_date(date)
        times = pd.date_range(date.replace(hour=0, minute=0),
                              date.replace(hour=23, minute=59),
                              freq='D',
                              tz=pytz.timezone(self.settings.timezone))
        sun = solarposition.sun_rise_set_transit_spa(times,
                                                     self.settings.latitude,
                                                     self.settings.longitude)
        sunrise = parser.parse(str(sun['sunrise'][0])).replace(tzinfo=None)
        sunset = parser.parse(str(sun['sunset'][0])).replace(tzinfo=None)
        self.daylight_cache[day] = (self._norm_date(sunrise),
                                    self._norm_date(sunset))
        return self.daylight_cache[day]

    @property
    def optimal_time(self):
        '''Time when the system is expected to produce the most today.'''
        return self.optimal_time_at(datetime.now())

    def optimal_time_at(self, date):
        '''Time when the system is expected to produce the most on date day.'''
        sunrise, sunset = self.daytime_at(date)
        return self._norm_date((sunset - sunrise) / 2 + sunrise)

    @Pyro5.api.expose
    @property
    def max_available_power(self):
        '''Maximum power available between now and the end of daytime.'''
        return self.max_available_power_at(datetime.now())

    @Pyro5.api.expose
    def max_available_power_at(self, date):
        '''Maximum power available between date and the same day dusk.'''
        if isinstance(date, str):
            date = parser.parse(date)
        _, sunset = self.daytime_at(date)
        if date > sunset:
            return 0
        if date > self.optimal_time_at(date):
            production = self.power_at(date)
        else:
            production = self.power_at(self.optimal_time_at(date))
        return production - self.settings.base_power

    class _PowerRange:
        def __init__(self, start, end, parent, reverse=False):
            self.start = start
            self.end = end - timedelta(minutes=1)
            self.parent = parent
            self.reverse = reverse
            self.length = int((self.end - self.start).seconds / 60) + 1

        def time(self, index):
            '''Return the datetime at 'index'.'''
            if self.reverse:
                index = self.length - index
            return self.start + timedelta(minutes=index)

        def __getitem__(self, index):
            return self.parent.power_at(self.time(index))

        def __len__(self):
            return self.length

    @Pyro5.api.expose
    def next_power_window(self, power: float) -> tuple:
        '''Return the next time window when 'min_power' should be available.

        Under clear sky weather conditions, it estimates when the system is
        likely going to product more than 'min_power'.

        '''
        min_power = power + self.settings.base_power
        sunrise, sunset = self.daytime
        now = self._norm_date(datetime.now())
        if self.power >= min_power:
            start = now
            to_lo = self._PowerRange(max(now, self.optimal_time),
                                     sunset, self, reverse=True)
            return (start, to_lo.time(bisect_right(to_lo, min_power)))
        if now < self.optimal_time:
            early = now
        else:
            sunrise, sunset = self.daytime_at(now + timedelta(hours=24))
            early = sunrise

        to_hi = self._PowerRange(early, self.optimal_time_at(early), self)
        to_lo = self._PowerRange(self.optimal_time_at(early), sunset,
                                 self, reverse=True)
        start = to_hi.time(bisect_right(to_hi, min_power))
        end = to_lo.time(bisect_right(to_lo, min_power))
        if start == end:
            raise ValueError('No power window for %.3f minimal power' % power)
        return (start, end)

class PowerSimulatorProxy:
    '''Helper class for the power simulator Sensor and Service.

    This class is a wrapper of the power simulator sensor and service with
    exception handlers. It provides convenience for services using the weather
    Sensor and Service by suppressing the burden of locating them and handling
    the various remote object related errors.

    '''
    # pylint: disable=too-few-public-methods
    def __init__(self, max_attempt=2):
        self.max_attempt = max_attempt
        self.service = None

    def __attempt(self, func, *args):
        for _ in range(self.max_attempt):
            try:
                self.service = NameServer().locate_service(MODULE_NAME)
            except Pyro5.errors.NamingError:
                log_exception('Failed to locate the power_simulator',
                              *sys.exc_info())
            except Pyro5.errors.CommunicationError:
                log_exception('Cannot communicate with the nameserver',
                                  *sys.exc_info())
            if self.service:
                try:
                    return getattr(self.service, func)(*args)
                except Pyro5.errors.PyroError:
                    log_exception('Communication failed with power_simulator',
                                  *sys.exc_info())
                    self.service = None
        raise RuntimeError('Could not communicate with the power_simulator')

    def __getattr__(self, name):
        if name in ['power', 'power_at', 'read', 'next_power_window',
                    'max_available_power', 'max_available_power_at']:
            def inner(*args):
                return self.__attempt(name, *args)
            return inner
        raise AttributeError("'%s' has no attribute '%s'" %
                             (self.__class__.name, name))

def main():
    '''Register and run the power_simulator Sensor and service.'''
    sys.excepthook = my_excepthook
    base = os.path.splitext(__file__)[0]
    config = init(base + '.log')['general']
    pv_system = Settings('', PV_SYSTEM)

    locator = Nominatim(user_agent=config['application'])
    point = locator.geocode(config['address'])
    timezone = TimezoneFinder().timezone_at(lat=point.latitude,
                                            lng=point.longitude)
    service_settings = DEFAULT_SETTINGS
    service_settings.update({'latitude': point.latitude,
                             'longitude': point.longitude,
                             'timezone': timezone})
    nameserver = NameServer()
    service_settings.update({'device_map':
                             nameserver.locate_sensor('power').read().keys()})
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    service = PowerSimulator(ModelFactory(pv_system), settings, WeatherProxy())
    daemon = Pyro5.api.Daemon()
    uri = daemon.register(service)

    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    while True:
        watchdog.register(os.getpid(), MODULE_NAME)
        watchdog.kick(os.getpid())

        try:
            nameserver.register_sensor(MODULE_NAME, uri)
            nameserver.register_service(MODULE_NAME, uri)
        except RuntimeError:
            log_exception('Failed to register the sensor or service',
                          *sys.exc_info())

        sockets, _, _ = select(daemon.sockets, [], [],
                               # pylint: disable=maybe-no-member
                               settings.max_loop_duration)
        if sockets:
            daemon.events(sockets)

if __name__ == "__main__":
    main()
