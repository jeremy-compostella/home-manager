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

'''Process the database and generate models like the HVAC performance model.'''

import argparse
from datetime import timedelta
from statistics import mean, median

import numpy as np
import pylab as plt
import statsmodels.api as sm
from dateutil.parser import parse as parse_time
from scipy.interpolate import Rbf, interp1d

from tools import (db_dict_factory, db_dict_to_table, db_table_to_dict,
                   get_database)

SETTINGS = {'min_running_power': 4.5,
            'power_sensor_keys': ['a_c', 'air_handler'],
            'min_stop': 20}

class DataPoint:
    def __init__(self, database, start, power):
        self.database = database
        self.start = self.end = start
        self._usage = [power]
        self._outdoor = self._indoor = self._indoor_change = None

    def add(self, time, power):
        self.end = time
        self._usage.append(power)

    @property
    def power(self):
        return median(self._usage)

    @power.setter
    def power(self, power):
        self._usage.append(power)

    def duration(self):
        return self.end - self.start

    def _field_at(self, field, table, time):
        cursor = self.database.cursor()

        prefix = 'SELECT timestamp, %s FROM %s ' % (field, table)
        req = prefix + \
            'WHERE timestamp <= \'%s\' ORDER BY timestamp DESC LIMIT 1' % time
        cursor.execute(req)
        before = cursor.fetchone()

        req = prefix + \
            'WHERE timestamp >= \'%s\' ORDER BY timestamp ASC LIMIT 1' % time
        cursor.execute(req)
        after = cursor.fetchone()

        if after == before:
            return before[field]
        zero = parse_time(before['timestamp'])
        fun = interp1d([0, (parse_time(after['timestamp']) - zero).seconds],
                       [before[field], after[field]], fill_value="extrapolate")
        return fun((time - zero).seconds).item()

    def outdoor(self):
        if self._outdoor is None:
            start = self._field_at('temperature', 'weather', self.start)
            end = self._field_at('temperature', 'weather', self.end)
            self._outdoor = mean([start, end])
        return self._outdoor

    def indoor(self):
        if self._indoor is None:
            start = self._field_at('home', 'hvac', self.start)
            end = self._field_at('home', 'hvac', self.end)
            self._indoor = mean([start, end])
        return self._indoor

    def indoor_change(self):
        if self._indoor_change is None:
            start = self._field_at('home', 'hvac', self.start)
            end = self._field_at('home', 'hvac', self.end)
            self._indoor_change = end - start
        return self._indoor_change

    def valid(self):
        return (self.outdoor() - self.indoor()) * self.indoor_change() > 0

class HVACModel:
    '''Estimate the power and efficiency at an outdoor temperature.

    This model is built out of statistics computed from data collected over six
    months.

    '''
    def __init__(self, datapoints=None):
        if datapoints:
            datapoints.sort(key=lambda x: x.outdoor())
            points = [point for point in datapoints \
                      if point.indoor_change() != 0]
            power = sm.nonparametric.lowess([p.power for p in points],
                                            [p.outdoor() for p in points],
                                            frac=0.15, is_sorted=True)
            time = sm.nonparametric.lowess([(point.duration().seconds / 60) /
                                            abs(point.indoor_change()) \
                                            for point in points],
                                           [p.outdoor() for p in points],
                                           frac=.3)
            data = [{'temperature': power[i][0],
                     'power': power[i][1],
                     'minute_per_degree': time[i][1]} \
                    for i, _ in enumerate(power)]
        else:
            data = db_table_to_dict('hvac_model')
        self._power_model = interp1d([point['temperature'] for point in data],
                                     [point['power'] for point in data],
                                     fill_value="extrapolate")
        self._time_model = interp1d([point['temperature'] for point in data],
                                     [point['minute_per_degree'] \
                                      for point in data],
                                     fill_value="extrapolate")

    def power(self, temperature):
        '''Power used by the system running at 'temperature'.'''
        return self._power_model(temperature).item()

    def time(self, temperature):
        '''Time necessary to change the temperature by one degree.'''
        return timedelta(minutes=self._time_model(temperature).item())

    def plot(self):
        _, ax1 = plt.subplots()
        temperatures = self._power_model.x

        ax1.set(xlabel='Outdoor Temperature °F', ylabel='kW')
        ax1.plot(temperatures,
                 self._power_model.y,
                 label='Power', color='#1f77b4')
        ax1.legend(loc='upper left')
        ax1.set_title('Daytime HVAC System Performance Model')
        ax2 = ax1.twinx()
        ax2.set(ylabel='Minutes / °F')
        ax2.plot(temperatures,
                 self._time_model.y,
                 label='Time', color='#ff7f0e')
        ax2.legend(loc='lower right')

    def save(self):
        db_dict_to_table([{'temperature': self._power_model.x[i],
                           'power': self._power_model.y[i],
                           'minute_per_degree': self._time_model.y[i]}
                          for i in range(0, len(self._power_model.x))],
                         'hvac_model')

class HomeModel:
    '''Estimate the indoor temperature change in one minute.

    This estimation should theoretically factor in plenty of data such as house
    sun exposition, weather, indoor temperature, insulation parameters ... etc
    but they are all ignored in this model.

    This model is built out of statistics computed from data collected over six
    months. The statistics are turned into points which are smoothed using a
    Bezier curve.

    '''
    def __init__(self, datapoints=None):
        self.data = None
        if datapoints:
            self.data = [{'indoor': point.indoor(),
                          'outdoor': point.outdoor(),
                          'degree_per_minute': point.indoor_change() /
                          (point.duration().seconds / 60)}
                         for point in datapoints if point.valid()]

        if self.data is None:
            self.data = db_table_to_dict('home_model')
        self._time_model = Rbf([x['indoor'] for x in self.data],
                               [y['outdoor'] for y in self.data],
                               [z['degree_per_minute'] for z in self.data],
                               epsilon=.01)

    def degree_per_minute(self, indoor, outdoor):
        '''Temperature change in degree over a minute of time.

        It returns the estimated temperature of the house when exposed at an
        outdoor 'temperature'. The returned value can be positive or negative.

        '''
        return self._time_model(indoor, outdoor).item()

    def plot(self):
        edges = np.linspace(min(self.data, key=lambda x: x['outdoor'])['outdoor'],
                            max(self.data, key=lambda x: x['outdoor'])['outdoor'],
                            800)
        centers = edges[:-1] + np.diff(edges[:2])[0] / 2.
        plt.pcolormesh(*np.meshgrid(edges, edges),
                       self._time_model(*np.meshgrid(centers, centers)),
                       shading='flat', cmap='RdBu_r', vmin=-0.05, vmax=0.05)
        plt.title('Home Thermal Model')
        plt.colorbar(label='°F / minute')
        plt.xlim(min(self.data, key=lambda x: x['indoor'])['indoor'],
                 max(self.data, key=lambda x: x['indoor'])['indoor'])
        plt.xlabel('Indoor temperature (°F)')
        plt.ylabel('Outdoor temperature (°F)')

    def save(self):
        db_dict_to_table(self.data, 'home_model')

def hvac_usage(power):
    return sum([power[key] for key in SETTINGS['power_sensor_keys']])

def skip_session(cursor):
    while True:
        row = cursor.fetchone()
        if row is None:
            return
        if hvac_usage(row) > SETTINGS['min_running_power']:
            continue
        return

def build_datapoints(min_hour, max_hour, min_duration, max_duration,
                     predicate):
    points = []
    with get_database() as database:
        database.row_factory = db_dict_factory
        cursor = database.cursor()
        cursor.execute('SELECT * FROM power ORDER BY timestamp ASC')
        while True:
            row = cursor.fetchone()
            if row is None:
                return points
            if parse_time(row['timestamp']).month in [10, 11]:
                continue
            usage = hvac_usage(row)
            if predicate(row):
                point = DataPoint(database, parse_time(row['timestamp']),
                                  usage)
                if not min_hour <= point.start.hour <= max_hour:
                    continue
                while True:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    usage = hvac_usage(row)
                    if predicate(row):
                        point.add(parse_time(row['timestamp']), usage)
                        if point.duration() < max_duration:
                            continue
                        points.append(point)
                        skip_session(cursor)
                        break
                    if min_duration <= point.duration() <= max_duration:
                        points.append(point)
                    break

def hvac_has_stopped_for_long_enough(row):
    if not hasattr(hvac_has_stopped_for_long_enough, 'count'):
        hvac_has_stopped_for_long_enough.count = SETTINGS['min_stop']
    if hvac_usage(row) < 0.2:
        hvac_has_stopped_for_long_enough.count -= 1
    else:
        hvac_has_stopped_for_long_enough.count = SETTINGS['min_stop']
        return False
    return hvac_has_stopped_for_long_enough.count <= 0

MODELS = {'home':
          {'class': HomeModel,
           'hour_range': [0, 23],
           'duration_range': [60, 90],
           'predicate': hvac_has_stopped_for_long_enough},
          'hvac':
          {'class': HVACModel,
           'hour_range': [8,  17],
           'duration_range': [25, 90],
           'predicate': lambda row: hvac_usage(row) > SETTINGS['min_running_power']}}

def get_parser():
    parser = argparse.ArgumentParser(description='Compute models.')
    parser.add_argument('model_name', choices=MODELS.keys(),
                        type=str, help='Model to work on')
    parser.add_argument('source', choices=['generate', 'database'],
                        type=str, help='Origin of the model data')
    parser.add_argument('action', choices=['plot', 'save', 'plot;save'],
                        type=str, help='Action to perform with the model')
    return parser

def main():
    args = get_parser().parse_args()
    desc = MODELS[args.model_name]
    if args.source == 'generate':
        points = build_datapoints(desc['hour_range'][0],
                                  desc['hour_range'][1],
                                  timedelta(minutes=desc['duration_range'][0]),
                                  timedelta(minutes=desc['duration_range'][1]),
                                  desc['predicate'])
        model = desc['class'](points)
    elif args.source == 'database':
        model = desc['class']()
    if args.action == 'plot;save' or args.action == 'plot':
        model.plot()
        plt.grid(visible=True, which='both', axis='both', linestyle='dotted')
        plt.show()
    if args.action == 'plot;save' or args.action == 'save':
        answer = input("Are you sure you want to update the database? ")
        if answer.lower() in ["y", "yes"]:
            model.save()
        else:
            print('Aborting')

if __name__ == "__main__":
    main()
