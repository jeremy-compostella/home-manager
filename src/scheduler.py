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

'''This module provides a scheduler service.

This service schedules tasks, start or stop them, depending on power related
criteria defined by the Task themselves and their priority. Tasks can
dynamically adjust their priority depending on their own need.

'''

import functools
import os
import sys
import time
from abc import abstractmethod
from collections import deque
from datetime import datetime, timedelta
from enum import IntEnum
from functools import reduce
from select import select
from statistics import mean

import Pyro5.api
from cachetools import TTLCache

from power_sensor import RecordScale
from sensor import SensorReader
from tools import (NameServer, Settings, debug, init, log_exception,
                   my_excepthook)
from watchdog import WatchdogProxy

DEFAULT_SETTINGS = {'window_size': 12,
                    'ignore_power_threshold': 0.1,
                    'max_record_gap': 3}

MODULE_NAME = 'scheduler'

class Priority(IntEnum):
    '''Task priority levels.'''
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4

@Pyro5.api.expose
# pylint: disable=too-many-instance-attributes
# Task has a necessary but larger number of attributes
class Task:
    '''Represent of a task and it properties.

    A task is usually coupled to an appliance or a device that it controls.

    A task defines a PRIORITY, a POWER consumption and a list of KEYS in a
    power usage record.

    It also implements the start() and stop() control methods which should
    preferably be decorated with @Pyro5.api.oneway to prevent scheduler
    execution delays.

    The start() method should always lead to the actual start of the
    appliance. If for some reasons the appliance cannot or should not be
    started anymore, the task is_runnable() method MUST return False so that
    the scheduler can make an educated decision.

    The stop() method can have no effect if the appliance still needs to
    run. For instance, if the appliance has a mandatory minimum runtime to
    prevent damage or deliver a result. If a call to the stop() method would
    have no effect, the is_stoppable() method should return False.

    Also, a task should implements a few feedback functions such as
    is_running(), is_stoppable() or meet_running_criteria() to guide the
    scheduler algorithm the best it can.

    '''

    def __init__(self, priority: Priority=None,
                 power: float=None,
                 keys: list=None,
                 auto_adjust: bool=False):
        if priority:
            self.priority = priority
        if power:
            self.power = power
        if keys:
            self.keys = keys
        self.auto_adjust = auto_adjust

    @abstractmethod
    @Pyro5.api.oneway
    def start(self):
        '''Start the task.'''

    @abstractmethod
    @Pyro5.api.oneway
    def stop(self):
        '''Stop the task.'''

    @abstractmethod
    def is_runnable(self) -> bool:
        '''Return True if the task is can be run.'''

    @abstractmethod
    def is_running(self) -> bool:
        '''Return True is the task is running, False otherwise.

        It should reflect the underlying appliance or device actual status.

        '''

    @abstractmethod
    def is_stoppable(self) -> bool:
        '''Return True is the task would stop on a stop() call. '''

    @abstractmethod
    def meet_running_criteria(self, ratio, power=0) -> bool:
        '''Return True if the all running criteria are met.

        It is the task responsibility to decide if the ratio is good
        enough for the to be scheduled or to keep running. It is not
        uncommon for a task to take device specific information to
        decide.

        '''

    def usage(self, record) -> float:
        '''Calculate the task power usage according to the RECORD.'''
        cur = 0
        for key in self.keys:
            if key in record.keys():
                cur += record[key]
        return cur

    @property
    @abstractmethod
    def desc(self) -> str:
        '''One line description of the task.

        This should include the task name, priority and optionally appliance
        specific status information. This description should be keep as short
        as possible.

        '''
        return ""

    @property
    def priority(self) -> Priority:
        '''Task PRIORITY level.'''
        return self._priority

    @priority.setter
    def priority(self, priority: Priority):
        self._priority = priority

    @property
    def power(self) -> float:
        '''Largest minimal power to start and run the appliance.'''
        return self._power

    @power.setter
    def power(self, power):
        self._power = power

    @property
    def keys(self) -> list:
        '''List of keys of the appliance in a power sensor record.'''
        return self._keys

    @keys.setter
    def keys(self, keys: list):
        self._keys = keys

    @property
    def auto_adjust(self) -> bool:
        '''The task automatically uses more power if available.

        For instance, an Electric Vehicle charger with adjustable charging rate
        should declare it minimal power consumption in the POWER attribute and
        it auto_adjust property should be True.

        '''
        return self._auto_adjust

    @auto_adjust.setter
    def auto_adjust(self, auto_adjust: bool):
        self._auto_adjust = auto_adjust

class PowerUsageSlidingWindow():
    '''Provide power usage analysis functions.

    This class provides methods to estimate how much of a (Task) is covered by
    the local power production or how much would be covered if it was running.

    Since this class manipulates Pyro proxy objects, it implements a few extra
    methods to limit the number of remote calls when possible.

    '''
    def __init__(self, size: int, ignore_power_threshold: float):
        '''Initialize a PowerUsageSlidingWindow

        SIZE defines the sliding window size. IGNORE_POWER_THRESHOLD is a
        threshold below which power consumption from a power record should be
        ignored. This threshold helps to discard any sensor data noise and
        ignore some device minimal power consumption. For instance, an air
        conditioner condenser placed outdoor may use a little bit of power to
        keep its circuitry warm at low temperature.

        '''
        self.size = int(size)
        self.ignore_power_threshold = ignore_power_threshold
        self.window: deque = deque([], self.size)

    def clear(self):
        '''Clear the power sliding window.'''
        self.window.clear()

    def update(self, record):
        '''Queue a new record to the power sliding window.'''
        for key, value in record.items():
            try:
                if 0 < value < self.ignore_power_threshold:
                    record[key] = 0
            except TypeError:
                pass
        self.window.append(record)

    @staticmethod
    def __usage(record: dict, keys: list):
        cur = 0
        for key in keys:
            if key in record.keys():
                cur += record[key]
        return cur

    @staticmethod
    def __set_usage(record: dict, keys: list, usage: float):
        usage /= len(keys)
        for key in keys:
            record[key] = usage

    @staticmethod
    def __minimize(task: Task, record: dict):
        '''Reduce the power consumption of a TASK in RECORD to its minimal
        value as defined by the power field of the task.

        '''
        keys = task.keys
        power = task.power
        record['net'] -= PowerUsageSlidingWindow.__usage(record, keys)
        PowerUsageSlidingWindow.__set_usage(record, keys, power)
        record['net'] += power

    @staticmethod
    def __suppress(task: Task, record: dict):
        '''Suppress TASK power consumption from the RECORD.'''
        keys = task.keys
        record['net'] -= PowerUsageSlidingWindow.__usage(record, keys)
        PowerUsageSlidingWindow.__set_usage(record, task.keys, 0)

    def power_used_by(self, task: Task) -> float:
        '''Calculate the power used by TASK in the latest record.'''
        return self.__usage(self.window[-1], task.keys)

    def available_for(self, task: Task,
                      minimum: list=None,
                      ignore: list=None) -> float:
        '''Estimate the ratio of power of TASK which would be covered.

        It returns a positive number representing the ratio of power of TASK
        which would be covered by the production if it were running.

        The estimation is calculated on the latest power record.

        TASK is the not running task for which the estimation must be
        calculated. MINIMUM is a list of task for which the actual power
        consumption should be replaced with the default task power
        property. IGNORE is the list of task which power consumption should be
        ignored in the calculation process.

        '''
        record = self.window[-1].copy()
        if minimum:
            for _task in minimum:
                self.__minimize(_task, record)
        if ignore:
            for _task in ignore:
                self.__suppress(_task, record)
        return max(0, -record['net'] / task.power)

    @staticmethod
    def __reducer_generator(minimize, ignore):
        # pylint: disable=unused-private-member
        def __reducer(accumulator, record):
            record = record.copy()
            if minimize:
                for task in minimize:
                    if task.usage(record) > 0:
                        PowerUsageSlidingWindow.__minimize(task, record)
            if ignore:
                for task in ignore:
                    if task.usage(record) > 0:
                        PowerUsageSlidingWindow.__suppress(task, record)
            for key, value in record.items():
                try:
                    accumulator[key] = accumulator.get(key, 0) + value
                except TypeError:
                    pass
            return accumulator
        return __reducer

    def covered_by_production(self, task: Task,
                              minimize: list=None,
                              ignore: list=None) -> float:
        '''Estimate the ratio of power of TASK covered by the power production.

        It returns a positive number representing the ratio of power of TASK
        which has been covered by the production since it started consuming
        power but limited to the sliding window time frame.

        MINIMIZE is a list of task for which the actual power consumption
        should be replaced with the default task power property if it was using
        power. IGNORE is the list of task which power consumption should be
        ignored in the calculation process.

        '''
        if task.usage(self.window[-1]) == 0:
            return 1
        running = [self.window[-1].copy()]
        for record in reversed(self.window):
            if task.usage(record) == 0:
                break
            running.append(record)
        usage = reduce(self.__reducer_generator(minimize, ignore),
                       running, { k:0.0 for k in running[0].keys() })
        total = task.usage(usage)
        return max(0, -1 * (usage['net'] - total) / total)

def compare_task(task1: Pyro5.api.Proxy, task2: Pyro5.api.Proxy) -> int:
    '''Compare TASK1 with TASK2.

    Return -1 if TASK1 is of less importance than TASK2, 1 if TASK1 is of more
    importance than TASK2 and 0 otherwise.

    '''
    if task1.priority > task2.priority:
        return 1
    if task1.priority < task2.priority:
        return -1
    if task1.auto_adjust and not task2.auto_adjust:
        return 1
    if not task1.auto_adjust and task2.auto_adjust:
        return -1
    if task1.power > task2.power:
        return 1
    return -1 if task2.power > task1.power else 0

class SchedulerInterface:
    '''Scheduler publicly available interface.'''

    @abstractmethod
    def register_task(self, uri: str):
        '''Register a runnable Task.'''

    @abstractmethod
    def unregister_task(self, uri: str):
        '''Unregister a Task.'''

    @abstractmethod
    def is_on_pause(self):
        '''Return True if the scheduler is on pause, False otherwise.'''

class Scheduler(SchedulerInterface):
    '''Responsible of electing starting and stopping tasks.

    Tasks should register themselves using the register_task() method.  The
    schedule() should be called on every cycle. A cycle length is typically one
    minute.

    '''
    def __init__(self, stat: PowerUsageSlidingWindow, timeout: float=3):
        self.uris: list = []
        self.stat = stat
        self.cache = TTLCache(5, timedelta(seconds=15), datetime.now)
        self.timeout = timeout
        self._is_on_pause = False

    def __cache(self, key, fun):
        try:
            return self.cache[key]
        except KeyError:
            self.cache[key] = fun()
            return self.cache[key]

    def __tasks(self):
        return [Pyro5.api.Proxy(uri) for uri in self.uris]

    @property
    def tasks(self):
        '''List of all the registered tasks.'''
        return self.__cache('tasks', self.__tasks)

    def __runnable(self):
        return [task for task in self.tasks if task.is_runnable()]

    @property
    def runnable(self):
        '''List of runnable tasks.'''
        return self.__cache('runnable', self.__runnable)

    def __running(self):
        return sorted([task for task in self.tasks if task.is_running()],
                      key=functools.cmp_to_key(compare_task))

    @property
    def running(self):
        '''List of running task sorted by ascending order of importance.'''
        return self.__cache('running', self.__running)

    def __adjustable(self):
        return [task for task in self.running if task.auto_adjust]

    @property
    def adjustable(self):
        '''List of running and adjustable task.'''
        return self.__cache('adjustable', self.__adjustable)

    def __stopped(self):
        return sorted([task for task in self.runnable \
                       if not task in self.running],
                      key=functools.cmp_to_key(compare_task), reverse=True)
    @property
    def stopped(self):
        '''List of stopped task sorted by descending order of importance.'''
        return self.__cache('stopped', self.__stopped)

    @Pyro5.api.oneway
    @Pyro5.api.expose
    def register_task(self, uri: str):
        if not uri in self.uris:
            self.uris.append(uri)

    @Pyro5.api.oneway
    @Pyro5.api.expose
    def unregister_task(self, uri: str):
        if uri in self.uris:
            self.uris.remove(uri)

    @staticmethod
    def __task_name(task: Pyro5.api.Proxy) -> str:
        '''Attempt to find a more defining name by querying the nameserver.'''
        try:
            nameserver = Pyro5.api.locate_ns()
            for key, value in nameserver.list().items():
                # pylint: disable=protected-access
                if Pyro5.core.URI(value) == task._pyroUri:
                    return key
        except Pyro5.errors.CommunicationError:
            pass
        return task.__repr__()

    def sanitize(self):
        '''Automatically remove unreachable remote tasks.'''
        for uri in self.uris.copy():
            for _ in range(3):
                priority = None
                try:
                    task = Pyro5.api.Proxy(uri)
                    priority = task.priority
                    running = task.is_running()
                    break
                except (Pyro5.errors.CommunicationError, RuntimeError):
                    time.sleep(1)
            if isinstance(priority, int) and isinstance(running, bool):
                continue
            name = self.__task_name(task)
            debug('Communication error with %s, removing...' %  name)
            self.uris.remove(uri)
            self.cache.clear()

    def __find_conflicting_power_keys(self) -> list:
        '''Return running tasks sharing the same power keys.

        The power consumption of tasks sharing the same keys cannot be clearly
        identified. Therefor, they do not run simultaneously.

        '''
        running_keys = [task.keys for task in self.running]
        tasks = []
        for keys in running_keys:
            for task in [task for task in self.running if task.keys == keys][1:]:
                tasks.append(task)
        return tasks

    def __find_failing_criteria(self) -> list:
        '''Return the first task not meeting its own running criteria.'''
        for task in sorted(self.running, key=lambda task: task.priority):
            ratio = self.stat.covered_by_production(task,
                                                    minimize=self.adjustable)
            power = self.stat.power_used_by(task)
            if not task.meet_running_criteria(ratio, power=power) \
               and task.is_stoppable():
                debug(('%s does not meet its running criteria ' +
                       '(ratio=%.2f, %.2f KWh)') % (task.desc, ratio, power))
                return [task]
        return []

    def __find_dimishing_adjustable(self) -> list:
        '''Return the lowest priority task diminishing adjustable task.

        If there are tasks running concurrently with adjustable tasks, this
        function identifies the lowest priority one and returns it if its
        priority is lower than the priority of the adjustable task of highest
        priority.

        '''
        if len(self.running) <= 1 or not self.adjustable:
            return []
        min_priority = max([task.priority for task in self.adjustable])
        for task in [task for task in self.running if task.is_stoppable()]:
            if not task.auto_adjust and task.priority < min_priority:
                debug("%s prevents %s to run to their full potential" % \
                      (task.desc, [adj.desc for adj in self.adjustable]))
                return [task]
        return []

    def __find_lower_priority_tasks(self) -> list:
        '''Return the list of tasks preventing a more priority task to run.'''
        for task in self.stopped:
            challengers = [challenger for challenger in self.running \
                           if compare_task(task, challenger) > 0 and \
                           challenger.is_stoppable()]
            if not challengers:
                continue
            minimum = [t for t in self.adjustable if t not in challengers]
            ratio = self.stat.available_for(task, ignore=challengers,
                                            minimum=minimum)
            if task.meet_running_criteria(ratio):
                debug("%s %s preventing %s to run" %
                      ([challenger.desc for challenger in challengers],
                       'is' if len(challengers) == 1 else 'are',
                       task.desc))
                return challengers
        return []

    def __elect_task(self) -> Pyro5.api.Proxy:
        '''Return the most suitable task to run.'''
        # The power consumption of tasks sharing the same keys cannot be
        # clearly identified. Therefor, they do not run simultaneously.
        running_keys = [task.keys for task in self.running]
        eligible = [task for task in self.stopped \
                    if not task.keys in running_keys]

        for task in eligible:
            ratio = self.stat.available_for(task, ignore=eligible,
                                            minimum=self.running)
            if self.running:
                priority = mean([t.priority for t in self.running])
            else:
                priority = 0
            if task.meet_running_criteria(ratio) and \
               task.is_runnable() and \
               (task.priority >= priority or task.auto_adjust):
                return task
        return None

    def schedule(self):
        '''This is the main function to be called on every cycle.

        This functions processes the tasks list and starts or stops tasks
        depending on power availability, the tasks priority and task specific
        running criteria.

        '''
        if self.is_on_pause():
            debug('scheduler is on pause, task scheduling aborted.')
            return
        self.cache.clear()
        if self.tasks:
            debug('Running %s' % [task.desc for task in self.running])
            debug('Stopped %s' % [task.desc for task in self.stopped])
            unrunnable = [task for task in self.tasks \
                          if not task.is_runnable()]
            if unrunnable:
                debug('Not runnable %s' % [task.desc for task in unrunnable])
        else:
            debug('No registered task')

        if self.running:
            ineligible_task_finders = [self.__find_conflicting_power_keys,
                                       self.__find_failing_criteria,
                                       self.__find_dimishing_adjustable,
                                       self.__find_lower_priority_tasks]
            for finder in ineligible_task_finders:
                tasks_to_stop = finder()
                if not tasks_to_stop:
                    continue
                for task in tasks_to_stop:
                    debug('Stopping %s' % task.desc)
                    task.stop()
                    self.running.remove(task)
                    self.stopped.append(task)
                break
        while True:
            task_to_start = self.__elect_task()
            if not task_to_start:
                break
            debug('Starting %s' % task_to_start.desc)
            task_to_start.start()
            self.stopped.remove(task_to_start)
            self.running.append(task_to_start)
        self.cache.clear()

    def stop_all(self):
        '''Stop all the running tasks.'''
        for task in self.running:
            task.stop()

    @Pyro5.api.expose
    def is_on_pause(self):
        return self._is_on_pause

    @Pyro5.api.expose
    def resume(self):
        '''Allow the scheduler to schedule tasks.'''
        if self._is_on_pause:
            debug('Resuming the scheduler.')
            self._is_on_pause = False

    @Pyro5.api.expose
    def pause(self):
        '''Prevent the scheduler from scheduling task.'''
        if not self._is_on_pause:
            debug('Putting the scheduler on pause.')
            self._is_on_pause = True

class SchedulerProxy(SchedulerInterface):
    '''Helper class for scheduler service users.

    This class is a wrapper with exception handler of the scheduler service. It
    provides convenience for services using the scheduler by suppressing the
    burden of locating the scheduler and handling the various remote object
    related errors.

    '''

    def __init__(self, max_attempt=2):
        self._scheduler = None
        self.max_attempt = max_attempt

    def __attempt(self, method, *args):
        for attempt in range(self.max_attempt):
            last_attempt = attempt == self.max_attempt - 1
            if not self._scheduler:
                try:
                    self._scheduler = NameServer().locate_service(MODULE_NAME)
                except Pyro5.errors.NamingError:
                    if last_attempt:
                        log_exception('Failed to locate the scheduler',
                                      *sys.exc_info())
                except Pyro5.errors.CommunicationError:
                    if last_attempt:
                        log_exception('Cannot communicate with the nameserver',
                                      *sys.exc_info())
            if self._scheduler:
                try:
                    return getattr(self._scheduler, method)(*args)
                except Pyro5.errors.PyroError:
                    if last_attempt:
                        log_exception('Communication failed with the scheduler',
                                      *sys.exc_info())
                    self._scheduler = None
        return None

    def register_task(self, uri):
        self.__attempt('register_task', uri)

    def unregister_task(self, uri):
        self.__attempt('unregister_task', uri)

    def is_on_pause(self):
        is_on_pause = self.__attempt('is_on_pause')
        # If we failed communicating with the scheduler, let's assume the
        # scheduler is dead and therefor, "on pause".
        return True if is_on_pause is None else is_on_pause

def main():
    '''Register and run the scheduler service.'''
    sys.excepthook = my_excepthook
    # pylint: disable=too-many-locals,too-many-statements
    base = os.path.splitext(__file__)[0]
    init(base + '.log')
    settings = Settings(base + '.ini', DEFAULT_SETTINGS)

    sensor = SensorReader('power')
    stat = PowerUsageSlidingWindow(
        # pylint: disable=maybe-no-member
        settings.window_size,
        # pylint: disable=maybe-no-member
        settings.ignore_power_threshold)
    scheduler = Scheduler(stat)

    Pyro5.config.MAX_RETRIES = 3
    daemon = Pyro5.api.Daemon()
    nameserver = NameServer()
    uri = daemon.register(scheduler)
    nameserver.register_service(MODULE_NAME, uri)

    simulator = SensorReader('power_simulator')
    watchdog = WatchdogProxy()
    debug("... is now ready to run")
    paused_locally = False
    while True:
        watchdog.register(os.getpid(), MODULE_NAME)
        watchdog.kick(os.getpid())

        try:
            nameserver.register_service(MODULE_NAME, uri)
        except RuntimeError:
            log_exception('Failed to register the scheduler service',
                          *sys.exc_info())

        while True:
            now = datetime.now()
            timeout = 60 - (now.second + now.microsecond/1000000.0)
            next_cycle = now + timedelta(seconds=timeout)
            sockets, _, _ = select(daemon.sockets, [], [], timeout)
            if sockets:
                daemon.events(sockets)
            if datetime.now() >= next_cycle:
                break

        record = sensor.read(scale=RecordScale.MINUTE)
        # pylint: disable=maybe-no-member
        if not record:
            gap = sensor.time_elapsed_since_latest_record()
            debug('No new power sensor record for %s' % gap)
            max_gap = timedelta(minutes=settings.max_record_gap)
            # No new power sensor record for more than
            # 'max_record_gap', let's try to use the power record
            # simulator instead.
            if gap > max_gap:
                record = simulator.read(scale=RecordScale.MINUTE)
                if record:
                    debug('Using a record from the simulator')
                elif simulator.time_elapsed_since_latest_record() > max_gap:
                    # Even the power simulator record cannot deliver any
                    # record, let's stop all the tasks until new records are
                    # available.
                    debug('the scheduler has not been able to read ' +
                          'any power sensor record for more than ' +
                          '%d minutes.' % settings.max_record_gap)
                    if not scheduler.is_on_pause():
                        scheduler.stop_all()
                        scheduler.pause()
                        paused_locally = True

        if not record:
            continue

        if scheduler.is_on_pause() and paused_locally:
            stat.clear()
            paused_locally = False
            scheduler.resume()

        stat.update(record)

        scheduler.sanitize()
        try:
            scheduler.schedule()
        except (Pyro5.errors.CommunicationError, RuntimeError, AttributeError):
            log_exception('schedule() failed', *sys.exc_info())
            debug(''.join(Pyro5.errors.get_pyro_traceback()))

if __name__ == "__main__":
    main()
