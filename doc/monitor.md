<a id="monitor"></a>

# monitor

<a id="monitor.Monitor"></a>

## Monitor Objects

```python
class Monitor(Sensor)
```

<a id="monitor.Monitor.track"></a>

#### track

```python
@Pyro5.api.expose
def track(name, state)
```

Update or start tracking "name" with current value "state"

<a id="monitor.MonitorProxy"></a>

## MonitorProxy Objects

```python
class MonitorProxy()
```

Helper class for monitor service users.

This class is a wrapper with exception handler of the monitor service. It
provides convenience for modules using the monitor by suppressing the
burden of locating the monitor and handling the various remote object
related errors.

