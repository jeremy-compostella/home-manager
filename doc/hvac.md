<a id="hvac"></a>

# hvac

This module implements an HVAC Task based on the Ecobee thermostat.

<a id="hvac.Mode"></a>

## Mode Objects

```python
class Mode(IntEnum)
```

Define the themostat operating mode.

<a id="hvac.HVACModel"></a>

## HVACModel Objects

```python
class HVACModel()
```

Estimate the power and efficiency at a outdoor temperature.

This model is built out of statistics computed from data collected over six
months. The statistics are turned into points which are smoothed using a
Bezier curve.

<a id="hvac.HVACModel.power"></a>

#### power

```python
def power(temperature)
```

Power used by the system running at 'temperature'.

<a id="hvac.HVACModel.time"></a>

#### time

```python
def time(temperature)
```

Time necessary to change the temperature by one degree.

<a id="hvac.HomeModel"></a>

## HomeModel Objects

```python
class HomeModel()
```

Estimate the indoor temperature change in one minute.

This estimation should theoretically factor in plenty of data such as house
sun exposition, weather, indoor temperature, insulation parameters ... etc
but they are all ignored in this model.

This model is built out of statistics computed from data collected over six
months. The statistics are turned into points which are smoothed using a
Bezier curve.

<a id="hvac.HomeModel.degree_per_minute"></a>

#### degree\_per\_minute

```python
def degree_per_minute(temperature)
```

Temperature change in degree over a minute of time.

It returns the estimated temperature of the house when exposed at an
outdoor 'temperature'. The returned value can be positive or negative.

<a id="hvac.HVACTask"></a>

## HVACTask Objects

```python
class HVACTask(Task,  Sensor)
```

Ecobee controller HVAC system.

This task makes use of the Ecobee hold feature to control the HVAC
system. This task does not modify the Ecobee schedule but it expects that
at times where the production system runs (daylight for PV for instance),
the comfort setting temperatures are set to "unreachable" values. The task
is going to optimally heat or cool the home depending on power availability
and user defined target temperatures.

<a id="hvac.HVACTask.min_run_time"></a>

#### min\_run\_time

```python
@property
def min_run_time()
```

Minimal run time for the HVAC.

It is to prevent damage of the water heater by turning it on and off
too frequently.

<a id="hvac.HVACTask.hvac_mode"></a>

#### hvac\_mode

```python
@property
def hvac_mode()
```

Current HVAC mode.

<a id="hvac.HVACTask.adjust_priority"></a>

#### adjust\_priority

```python
def adjust_priority()
```

Adjust the priority based on the estimate run time.

<a id="hvac.HVACTask.adjust_power"></a>

#### adjust\_power

```python
def adjust_power()
```

Update the power necessary to run HVAC system.

<a id="hvac.HVACTask.indoor_temp"></a>

#### indoor\_temp

```python
@property
def indoor_temp()
```

Current indoor temperature.

<a id="hvac.get_ecobee"></a>

#### get\_ecobee

```python
def get_ecobee()
```

Load the ecobee service object from the storage.

<a id="hvac.register"></a>

#### register

```python
def register(name, uri, raise_exception=True)
```

Register 'task' as sensor and task.

<a id="hvac.HVACParam"></a>

## HVACParam Objects

```python
class HVACParam(threading.Thread)
```

This class provides information to the HVAC task.

This class is a thread because some of the information can take several
seconds to collect or compute. This class provide information such as the
maximum available power to expect from the energy production system, the
current outdoor temperature and the target time and temperature.

The target time is defined as the point in time when the energy production
system produces enough power for the HVAC system to run. The target
temperature is the temperature the home should be at target time so that
the temperature is going to be as close as possible to 'goal_temperature'
at goal time.

<a id="hvac.HVACParam.max_available_power"></a>

#### max\_available\_power

```python
@property
def max_available_power()
```

Maximum power that should be available to operate the HVAC.

<a id="hvac.HVACParam.outdoor_temp"></a>

#### outdoor\_temp

```python
@property
def outdoor_temp()
```

Current outdoor temperature.

<a id="hvac.HVACParam.target_time"></a>

#### target\_time

```python
@property
def target_time()
```

Last point in time when the system will produce enough power.

<a id="hvac.HVACParam.target_temp"></a>

#### target\_temp

```python
@property
def target_temp()
```

Desired temperature at 'target_time'.

<a id="hvac.HVACParam.is_ready"></a>

#### is\_ready

```python
def is_ready()
```

Return true if all this object is ready to be used.

<a id="hvac.my_excepthook"></a>

#### my\_excepthook

```python
def my_excepthook(etype, value=None, traceback=None)
```

On uncaught exception, log the exception and kill the process.

<a id="hvac.main"></a>

#### main

```python
def main()
```

Start and register an HVAC Task and Sensor.

