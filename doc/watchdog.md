<a id="watchdog"></a>

# watchdog

This module implements a watchdog service processes.

<a id="watchdog.Process"></a>

## Process Objects

```python
class Process()
```

Class representing a process (aka. a service).

<a id="watchdog.Process.reset_timer"></a>

#### reset\_timer

```python
def reset_timer() -> None
```

Reset the timer.

<a id="watchdog.Process.timer_has_expired"></a>

#### timer\_has\_expired

```python
def timer_has_expired() -> bool
```

Return true if the timer has expired

<a id="watchdog.Process.kill"></a>

#### kill

```python
def kill(signal_number) -> None
```

Send the signal_number signal to the process.

<a id="watchdog.Process.is_alive"></a>

#### is\_alive

```python
def is_alive() -> bool
```

Return True if the process is alive.

<a id="watchdog.WatchdogInterface"></a>

## WatchdogInterface Objects

```python
class WatchdogInterface()
```

Scheduler publicly available interface.

<a id="watchdog.WatchdogInterface.register"></a>

#### register

```python
@abstractmethod
def register(pid: int, name: str, timeout: timedelta = None)
```

Add a process to the list of monitored processes.

Processes are identified by a PID and a NAME.  If the TIMEOUT argument
is not set, a default timeout of 3 minutes timeout is used.

<a id="watchdog.WatchdogInterface.unregister"></a>

#### unregister

```python
@abstractmethod
def unregister(pid: int) -> None
```

Unregister a process.

<a id="watchdog.WatchdogInterface.kick"></a>

#### kick

```python
@abstractmethod
def kick(pid: int) -> None
```

Reset the watchdog timer of a particular process.

<a id="watchdog.Watchdog"></a>

## Watchdog Objects

```python
class Watchdog(WatchdogInterface)
```

Watchdog class exposed as a pyro object.

Processes register themselves using the Watchdog.register() method. Once
they have registered, if they do not call the Watchdog.kick() method for a
defined duration, the watchdog service kills them.

<a id="watchdog.Watchdog.desc"></a>

#### desc

```python
@property
@Pyro5.api.expose
def desc()
```

List processes formatted as string.

<a id="watchdog.Watchdog.monitor"></a>

#### monitor

```python
def monitor() -> None
```

Verify the monitored processes and report status to the monitor.

If any process is missing, it is automatically removed from the list of
registered processes.

<a id="watchdog.Watchdog.kill_hung_processes"></a>

#### kill\_hung\_processes

```python
def kill_hung_processes() -> None
```

Kill processes which have not reset their watchdog timer in time.

<a id="watchdog.WatchdogProxy"></a>

## WatchdogProxy Objects

```python
class WatchdogProxy(WatchdogInterface)
```

Helper class for watchdog service users.

This class is a wrapper with exception handler of the watchdog service. It
provides convenience for services using the watchdog by suppressing the
burden of locating the watchdog and handling the various remote object
related errors.

