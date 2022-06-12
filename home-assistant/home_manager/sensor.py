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

from datetime import datetime, timedelta
from logging import getLogger

import Pyro5.api
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.sensor import (STATE_CLASS_MEASUREMENT,
                                             STATE_CLASS_TOTAL_INCREASING,
                                             SensorEntity)
from homeassistant.const import (DEGREE, DEVICE_CLASS_BATTERY,
                                 DEVICE_CLASS_ENERGY, DEVICE_CLASS_HUMIDITY,
                                 DEVICE_CLASS_POWER, DEVICE_CLASS_TEMPERATURE,
                                 ENERGY_KILO_WATT_HOUR, LENGTH_MILES,
                                 PERCENTAGE, POWER_KILO_WATT,
                                 SPEED_MILES_PER_HOUR, TEMP_FAHRENHEIT)
from homeassistant.helpers.update_coordinator import (CoordinatorEntity,
                                                      DataUpdateCoordinator)
from pyemvue.enums import Scale

from .const import DOMAIN  # pylint: disable=import-error

LOGGER = getLogger(__name__)

SCALE_UNITS = {'second': POWER_KILO_WATT,
         'minute': POWER_KILO_WATT,
         'hour': ENERGY_KILO_WATT_HOUR,
         'day': ENERGY_KILO_WATT_HOUR}

SCALES={'minute': Scale.MINUTE.value,
        'day': Scale.DAY.value}

SCALE_AND_INTERVAL = {'minute': timedelta(minutes=1),
                      'day': timedelta(minutes=5)}

CLASS_AND_UNITS = {'°F': {'unit': TEMP_FAHRENHEIT,
                          'device_class': DEVICE_CLASS_TEMPERATURE},
                   '%': {'unit': PERCENTAGE,
                         'device_class': DEVICE_CLASS_BATTERY},
                   'mi': {'unit': LENGTH_MILES,
                          'device_class': None},
                   'mph': {'unit': SPEED_MILES_PER_HOUR,
                           'device_class': None},
                   '°': {'unit': DEGREE,
                         'device_class': None},
                   '$/kWh': {'unit': '{currency}/kWh',
                             'device_class': None},
                   'humidity': {'unit': PERCENTAGE,
                                'device_class': DEVICE_CLASS_HUMIDITY},
                   'minutes': {'unit': 'minutes',
                               'device_class': None}}

def locate(path):
    nameserver = Pyro5.api.locate_ns()
    return Pyro5.api.Proxy(nameserver.lookup(path))

def update_power_data(sensor_name, scale):
    async def inner():
        data = {}
        try:
            sensor = locate('home-manager.sensor.%s' % sensor_name)
            record = sensor.read(scale=SCALES[scale])
        except (RuntimeError, Pyro5.errors.PyroError) as err:
            print(err)
            return data
        for key, value in record.items():
            if key != 'net':
                data[key] = abs(value)
        total = sum([v for k, v in record.items() \
                     if k not in ['net', 'solar', 'from grid', 'to grid']])
        data['other'] = -(total + record['solar'] - record['net'])
        return data
    return inner

def update_generic_data(sensor_name):
    async def inner():
        data = {}
        try:
            sensor = locate('home-manager.sensor.%s' % sensor_name)
            record = sensor.read()
            units = sensor.units()
        except (RuntimeError, Pyro5.errors.PyroError):
            return data
        for key, value in record.items():
            if units[key] in CLASS_AND_UNITS.keys():
                data[key] = {'value': value,
                             'unit': CLASS_AND_UNITS[units[key]]}
        return data
    return inner

async def update_monitor_data():
    try:
        sensor = locate('home-manager.sensor.monitor')
        record = sensor.read()
        return {key:not value for key, value in record.items()}
    except (RuntimeError, Pyro5.errors.PyroError):
        return {}

def update_task_data(task_path):
    async def inner():
        data = {}
        try:
            task = locate(task_path)
            data = {'priority': task.priority,
                    'is_runnable': task.is_runnable(),
                    'is_stoppable': task.is_stoppable()}
        except (RuntimeError, Pyro5.errors.PyroError):
            pass
        return data
    return inner

async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    for scale, interval in SCALE_AND_INTERVAL.items():
        coordinator = DataUpdateCoordinator(
            hass, LOGGER, name="sensor",
            update_method=update_power_data('power', scale),
            update_interval=interval)
        await coordinator.async_refresh()
        for key, _ in coordinator.data.items():
            add_entities([PowerSensor(coordinator, 'power', key, scale)])

    coordinator = DataUpdateCoordinator(
        hass, LOGGER, name="sensor",
        update_method=update_power_data('power_simulator', 'minute'),
        update_interval=timedelta(minutes=1))
    await coordinator.async_refresh()
    for key, _ in coordinator.data.items():
        add_entities([PowerSensor(coordinator, 'power_simulator', key, 'minute')])

    for sensor in ['water_heater', 'car', 'utility_rate', 'weather', 'pool',
                   'model3_car']:
        coordinator = DataUpdateCoordinator(
            hass, LOGGER, name="sensor",
            update_method=update_generic_data(sensor),
            update_interval=timedelta(minutes=1))
        await coordinator.async_refresh()
        for key, _ in coordinator.data.items():
            add_entities([GenericSensor(coordinator, sensor, key)])

    prefix = 'home-manager.task.'
    nameserver = Pyro5.api.locate_ns()
    for path, _ in nameserver.list().items():
        if not path.startswith(prefix):
            continue
        coordinator = DataUpdateCoordinator(
            hass, LOGGER, name="sensor",
            update_method=update_task_data(path),
            update_interval=timedelta(minutes=1))
        await coordinator.async_refresh()
        task_name = path[len(prefix):]
        for key, _ in coordinator.data.items():
            if key == 'priority':
                add_entities([TaskPrioritySensor(coordinator, task_name)])
            else:
                add_entities([BinarySensor(coordinator, task_name, key)])

    coordinator = DataUpdateCoordinator(
        hass, LOGGER, name="sensor",
        update_method=update_generic_data('pool_pump'),
        update_interval=timedelta(minutes=1))
    await coordinator.async_refresh()
    add_entities([RemainingTimeSensor(coordinator, 'pool_pump')])

    coordinator = DataUpdateCoordinator(
        hass, LOGGER, name="sensor",
        update_method=update_monitor_data,
        update_interval=timedelta(seconds=5))
    await coordinator.async_refresh()
    for key, _ in coordinator.data.items():
        add_entities([BinarySensor(coordinator, 'monitor', key, 'problem')])

class PowerSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, parent, key, scale):
        super().__init__(coordinator)
        self._parent = parent
        self._key = key
        self._scale = scale

    @property
    def name(self):
        return '%s (%s)' % (self._key, self._scale)

    @property
    def state(self):
        return self.coordinator.data[self._key]

    @property
    def state_class(self):
        if self.unit_of_measurement == ENERGY_KILO_WATT_HOUR:
            return STATE_CLASS_TOTAL_INCREASING
        return STATE_CLASS_MEASUREMENT

    @property
    def unit_of_measurement(self):
        return SCALE_UNITS[self._scale]

    @property
    def device_class(self):
        if self.unit_of_measurement == ENERGY_KILO_WATT_HOUR:
            return DEVICE_CLASS_ENERGY
        return DEVICE_CLASS_POWER

    @property
    def unique_id(self):
        return 'sensor.%s.%s.%s.%s' \
            % (DOMAIN, self._parent, self.name, self._scale)

    @property
    def device_info(self):
        identifiers = {(DOMAIN,
                        '%s.%s.%s' % (self._parent, self._key, self._scale))}
        return {'identifiers': identifiers,
                'name': self.name}

    @property
    def last_reset(self):
        if self.unit_of_measurement == ENERGY_KILO_WATT_HOUR:
            return datetime.now().replace(hour=0, minute=0, second=0,
                                          microsecond=0)
        return None

class GenericSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, parent, key):
        super().__init__(coordinator)
        self._parent = parent
        self._key = key
        self._unit = self.coordinator.data[self._key]['unit']

    @property
    def name(self):
        return self._key

    @property
    def device_class(self):
        return self._unit['device_class']

    @property
    def state(self):
        return self.coordinator.data[self._key]['value']

    @property
    def state_class(self):
        if self._unit['unit'] == LENGTH_MILES:
            return STATE_CLASS_TOTAL_INCREASING
        return STATE_CLASS_MEASUREMENT

    @property
    def unit_of_measurement(self):
        return self._unit['unit']

    @property
    def unique_id(self):
        return 'sensor.%s.%s.%s' % (DOMAIN, self._parent, self.name)

    @property
    def device_info(self):
        identifiers = {(DOMAIN,
                        '%s.%s' % (self._parent, self._key))}
        return {'identifiers': identifiers,
                'name': self.name}

class TaskPrioritySensor(CoordinatorEntity, NumberEntity):
    def __init__(self, coordinator, name):
        super().__init__(coordinator)
        self._name = name

    @property
    def value(self):
        return self.coordinator.data['priority']

    @property
    def name(self):
        return '%s priority' % self._name

    @property
    def min_value(self):
        return 1

    @property
    def max_value(self):
        return 4

    @property
    def step(self):
        return 1

    @property
    def mode(self):
        return 'auto'

    @property
    def unique_id(self):
        return 'sensor.%s.%s.priority' % (DOMAIN, self.name)

class BinarySensor(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, name, key, device_class='lock'):
        super().__init__(coordinator)
        self._name = name
        self._key = key
        self._device_class = device_class

    @property
    def is_on(self):
        return self.coordinator.data[self._key]

    @property
    def name(self):
        return '%s %s' % (self._name, self._key)

    @property
    def unique_id(self):
        return 'sensor.%s.%s.%s' % (DOMAIN, self.name, self._key)

    @property
    def device_class(self):
        return self._device_class

class RemainingTimeSensor(CoordinatorEntity, NumberEntity):
    def __init__(self, coordinator, name):
        super().__init__(coordinator)
        self._name = name

    @property
    def value(self):
        return self.coordinator.data['remaining_runtime']['value']

    @property
    def name(self):
        return '%s remaining time' % self._name

    @property
    def min_value(self):
        return 0

    @property
    def max_value(self):
        return 10000

    @property
    def step(self):
        return 1

    @property
    def mode(self):
        return 'auto'

    @property
    def unique_id(self):
        return 'sensor.%s.%s.remaining_runtime' % (DOMAIN, self.name)
