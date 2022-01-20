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

'''This module logs the sensors records every minute into the database.'''

import os
import signal
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from time import sleep

import Pyro5.api

from power_sensor import RecordScale
from tools import (NameServer, db_dict_factory, db_latest_record, debug,
                   get_database, init, log_exception)
from watchdog import WatchdogProxy


def field_name(name):
    '''Turn name into SQL field name compatible string.'''
    return name.lower().replace(' ', '_').replace('/', '_')

def field_type(value):
    '''Return the SQL type of "value"'''
    if isinstance(value, float):
        return 'float'
    if isinstance(value, int):
        return 'integer'
    return 'text'

def dict_to_table_fields(data):
    '''Turn "data" dictionary into a SQL table fields description'''
    return ', '.join(['%s %s' % (field_name(key), field_type(value))
                      for key, value in data.items()])

def dict_to_table_values(data):
    '''Turn "data" dictionary into a SQL table fields assignment'''
    return ', '.join(['%s %s' % (field_name(key), field_type(value))
                      for key, value in data.items()])

def execute(cursor, *args):
    '''Execute an SQL request and handle database concurrency'''
    for attempt in range(40):
        try:
            cursor.execute(*args)
            return
        except sqlite3.OperationalError as err:
            print(attempt, err)
            sleep(.5)
    debug('%s request failed' % args)

def create_table(table_name, cursor, data):
    '''Create "table_name" table if it does not exist'''
    execute(cursor, 'SELECT count(name) ' +
            'FROM sqlite_master ' +
            'WHERE type=\'table\' AND name=?', (table_name,))
    if cursor.fetchone()['count(name)'] > 0:
        return
    req = 'CREATE table %s (timestamp timestamp PRIMARY KEY, %s)' \
        % (table_name, dict_to_table_fields(data))
    execute(cursor, req)

def my_excepthook(etype, value=None, traceback=None):
    '''On uncaught exception, log the exception and kill the process.'''
    if value:
        args = (etype, value, traceback)
    else:
        args = sys.exc_info()
    log_exception('Uncaught exeption', *args)
    os.kill(os.getpid(), signal.SIGTERM)

sys.excepthook = my_excepthook

def main():
    '''Start and register a the sensor logger service.'''
    # pylint: disable=too-many-locals
    base = os.path.splitext(__file__)[0]
    init(base + '.log')
    module_name = os.path.basename(base)

    watchdog = WatchdogProxy()
    nameserver = NameServer()
    prev = {}
    debug("... is now ready to run")
    while True:
        watchdog.register(os.getpid(), module_name)
        watchdog.kick(os.getpid())

        now = datetime.now()
        timeout = 60 - (now.second + now.microsecond/1000000.0)
        sleep(timeout)

        # Daily power record
        if now.hour == 0 and now.minute == 5:
            yesterday = (now - timedelta(minutes=10)).astimezone(timezone.utc)
            sensor = nameserver.locate_sensor('power')
            data = sensor.read(scale=RecordScale.DAY, time=yesterday)
            table = 'daily_power'
            with get_database() as database:
                database.row_factory = db_dict_factory
                cursor = database.cursor()
                create_table(table, cursor, data)
                req = 'INSERT INTO %s (timestamp, %s) VALUES (\'%s\', %s)' \
                    % (table,
                       ', '.join([field_name(key) for key in data.keys()]),
                       (datetime.now() - timedelta(minutes=10)).date(),
                       ', '.join([str(value) for value in data.values()]))
                execute(cursor, req)

        timestamp = datetime.now().replace(second=0, microsecond=0)
        for name, sensor in nameserver.sensors():
            try:
                data = sensor.read()
            except:
                debug('Could not read %s sensor' % name)
                log_exception('Could not read %s sensor' % name,
                              *sys.exc_info())
                debug(''.join(Pyro5.errors.get_pyro_traceback()))
                continue

            if data is None or data == {}:
                debug('Empty data from %s sensor, skipping' % name)
                continue

            with get_database() as database:
                database.row_factory = db_dict_factory

                cursor = database.cursor()
                create_table(name, cursor, data)

                if name not in prev:
                    prev[name] = db_latest_record(name)
                    del prev[name]['timestamp']
                data = {field_name(key): value for key, value in data.items()}
                if prev[name]:
                    if data == prev[name] \
                       and name not in ['power', 'power_simulator']:
                        debug('No change for sensor %s, skipping' % name)
                        continue
                    if len(data) > len(prev[name]):
                        for key, value in data.items():
                            if key in prev[name]:
                                continue
                            debug('Adding missing column %s' % field_name(key))
                            req = 'ALTER TABLE %s ADD COLUMN %s %s' \
                                % (name, field_name(key), field_type(value))
                            execute(cursor, req)

                req = 'INSERT INTO %s (timestamp, %s) VALUES (\'%s\', %s)' \
                    % (name,
                       ', '.join([field_name(key) for key in data.keys()]),
                       timestamp,
                       ', '.join([str(value) for value in data.values()]))
                execute(cursor, req)
                prev[name] = data

if __name__ == "__main__":
    main()
