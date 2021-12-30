<a id="scheduler"></a>

# scheduler

This module provides a scheduler service.

This service schedules tasks, start or stop them, depending on power related
criteria defined by the Task themselves and their priority. Tasks can
dynamically adjust their priority depending on their own need.

<a id="scheduler.Priority"></a>

## Priority Objects

```python
class Priority(IntEnum)
```

Task priority levels.

<a id="scheduler.Task"></a>

## Task Objects

```python
@Pyro5.api.expose
class Task()
```

Represent of a task and it properties.

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

<a id="scheduler.Task.start"></a>

#### start

```python
@abstractmethod
@Pyro5.api.oneway
def start()
```

Start the task.

<a id="scheduler.Task.stop"></a>

#### stop

```python
@abstractmethod
@Pyro5.api.oneway
def stop()
```

Stop the task.

<a id="scheduler.Task.is_runnable"></a>

#### is\_runnable

```python
@abstractmethod
def is_runnable() -> bool
```

Return True if the task is can be run.

<a id="scheduler.Task.is_running"></a>

#### is\_running

```python
@abstractmethod
def is_running() -> bool
```

Return True is the task is running, False otherwise.

It should reflect the underlying appliance or device actual status.

<a id="scheduler.Task.is_stoppable"></a>

#### is\_stoppable

```python
@abstractmethod
def is_stoppable() -> bool
```

Return True is the task would stop on a stop() call.

<a id="scheduler.Task.meet_running_criteria"></a>

#### meet\_running\_criteria

```python
@abstractmethod
def meet_running_criteria(ratio, power=0) -> bool
```

Return True if the all running criteria are met.

It is the task responsibility to decide if the ratio is good
enough for the to be scheduled or to keep running. It is not
uncommon for a task to take device specific information to
decide.

<a id="scheduler.Task.usage"></a>

#### usage

```python
def usage(record) -> float
```

Calculate the task power usage according to the RECORD.

<a id="scheduler.Task.desc"></a>

#### desc

```python
@property
@abstractmethod
def desc() -> str
```

One line description of the task.

This should include the task name, priority and optionally appliance
specific status information. This description should be keep as short
as possible.

<a id="scheduler.Task.priority"></a>

#### priority

```python
@property
def priority() -> Priority
```

Task PRIORITY level.

<a id="scheduler.Task.power"></a>

#### power

```python
@property
def power() -> float
```

Largest minimal power to start and run the appliance.

<a id="scheduler.Task.keys"></a>

#### keys

```python
@property
def keys() -> list
```

List of keys of the appliance in a power sensor record.

<a id="scheduler.Task.auto_adjust"></a>

#### auto\_adjust

```python
@property
def auto_adjust() -> bool
```

The task automatically uses more power if available.

For instance, an Electric Vehicle charger with adjustable charging rate
should declare it minimal power consumption in the POWER attribute and
it auto_adjust property should be True.

<a id="scheduler.PowerUsageSlidingWindow"></a>

## PowerUsageSlidingWindow Objects

```python
class PowerUsageSlidingWindow()
```

Provide power usage analysis functions.

This class provides methods to estimate how much of a (Task) is covered by
the local power production or how much would be covered if it was running.

Since this class manipulates Pyro proxy objects, it implements a few extra
methods to limit the number of remote calls when possible.

<a id="scheduler.PowerUsageSlidingWindow.__init__"></a>

#### \_\_init\_\_

```python
def __init__(size: int, ignore_power_threshold: float)
```

Initialize a PowerUsageSlidingWindow

SIZE defines the sliding window size. IGNORE_POWER_THRESHOLD is a
threshold below which power consumption from a power record should be
ignored. This threshold helps to discard any sensor data noise and
ignore some device minimal power consumption. For instance, an air
conditioner condenser placed outdoor may use a little bit of power to
keep its circuitry warm at low temperature.

<a id="scheduler.PowerUsageSlidingWindow.clear"></a>

#### clear

```python
def clear()
```

Clear the power sliding window.

<a id="scheduler.PowerUsageSlidingWindow.update"></a>

#### update

```python
def update(record)
```

Queue a new record to the power sliding window.

<a id="scheduler.PowerUsageSlidingWindow.power_used_by"></a>

#### power\_used\_by

```python
def power_used_by(task: Task) -> float
```

Calculate the power used by TASK in the latest record.

<a id="scheduler.PowerUsageSlidingWindow.available_for"></a>

#### available\_for

```python
def available_for(task: Task, minimum: list = None, ignore: list = None) -> float
```

Estimate the ratio of power of TASK which would be covered.

It returns a positive number representing the ratio of power of TASK
which would be covered by the production if it were running.

The estimation is calculated on the latest power record.

TASK is the not running task for which the estimation must be
calculated. MINIMUM is a list of task for which the actual power
consumption should be replaced with the default task power
property. IGNORE is the list of task which power consumption should be
ignored in the calculation process.

<a id="scheduler.PowerUsageSlidingWindow.covered_by_production"></a>

#### covered\_by\_production

```python
def covered_by_production(task: Task, minimize: list = None, ignore: list = None) -> float
```

Estimate the ratio of power of TASK covered by the power production.

It returns a positive number representing the ratio of power of TASK
which has been covered by the production since it started consuming
power but limited to the sliding window time frame.

MINIMIZE is a list of task for which the actual power consumption
should be replaced with the default task power property if it was using
power. IGNORE is the list of task which power consumption should be
ignored in the calculation process.

<a id="scheduler.compare_task"></a>

#### compare\_task

```python
def compare_task(task1: Pyro5.api.Proxy, task2: Pyro5.api.Proxy) -> int
```

Compare TASK1 with TASK2.

Return -1 if TASK1 is of less importance then task2, 1 if TASK1 is of more
importance than TASK2 and 0 otherwise.

<a id="scheduler.SchedulerInterface"></a>

## SchedulerInterface Objects

```python
class SchedulerInterface()
```

Scheduler publicly available interface.

<a id="scheduler.SchedulerInterface.register_task"></a>

#### register\_task

```python
@abstractmethod
def register_task(uri: str)
```

Register a runnable Task.

<a id="scheduler.SchedulerInterface.unregister_task"></a>

#### unregister\_task

```python
@abstractmethod
def unregister_task(uri: str)
```

Unregister a Task.

<a id="scheduler.SchedulerInterface.is_on_pause"></a>

#### is\_on\_pause

```python
@abstractmethod
def is_on_pause()
```

Return True if the scheduler is on pause, False otherwise.

<a id="scheduler.Scheduler"></a>

## Scheduler Objects

```python
class Scheduler(SchedulerInterface)
```

Responsible of electing starting and stopping tasks.

Tasks should register themselves using the register_task() method.  The
schedule() should be called on every cycle. A cycle length is typically one
minute.

<a id="scheduler.Scheduler.tasks"></a>

#### tasks

```python
@property
def tasks()
```

List of all the registered tasks.

<a id="scheduler.Scheduler.runnable"></a>

#### runnable

```python
@property
def runnable()
```

List of runnable tasks.

<a id="scheduler.Scheduler.running"></a>

#### running

```python
@property
def running()
```

List of running task sorted by ascending order of importance.

<a id="scheduler.Scheduler.adjustable"></a>

#### adjustable

```python
@property
def adjustable()
```

List of running and adjustable task.

<a id="scheduler.Scheduler.stopped"></a>

#### stopped

```python
@property
def stopped()
```

List of stopped task sorted by descending order of importance.

<a id="scheduler.Scheduler.sanitize"></a>

#### sanitize

```python
def sanitize()
```

Automatically remove unreachable remote tasks.

<a id="scheduler.Scheduler.schedule"></a>

#### schedule

```python
def schedule()
```

This is the main function to be called on every cycle.

This functions processes the tasks list and starts or stops tasks
depending on power availability, the tasks priority and task specific
running criteria.

<a id="scheduler.Scheduler.stop_all"></a>

#### stop\_all

```python
def stop_all()
```

Stop all the running tasks.

<a id="scheduler.Scheduler.resume"></a>

#### resume

```python
@Pyro5.api.expose
def resume()
```

Allow the scheduler to schedule tasks.

<a id="scheduler.Scheduler.pause"></a>

#### pause

```python
@Pyro5.api.expose
def pause()
```

Prevent the scheduler from scheduling task.

<a id="scheduler.SchedulerProxy"></a>

## SchedulerProxy Objects

```python
class SchedulerProxy(SchedulerInterface)
```

Helper class for scheduler service users.

This class is a wrapper with exception handler of the scheduler service. It
provides convenience for services using the scheduler by suppressing the
burden of locating the scheduler and handling the various remote object
related errors.

<a id="scheduler.my_excepthook"></a>

#### my\_excepthook

```python
def my_excepthook(etype, value=None, traceback=None)
```

On uncaught exception, log the exception and kill the process.

<a id="scheduler.main"></a>

#### main

```python
def main()
```

Register and run the scheduler service.

