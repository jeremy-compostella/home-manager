<a id="power_simulator"></a>

# power\_simulator

This module implements a power usage simulation based on the pvlib library.

<a id="power_simulator.ModelFactory"></a>

## ModelFactory Objects

```python
class ModelFactory()
```

Factory pvlib Model Chain.

'conf' is a dictionary of parameters describing the PV system.

<a id="power_simulator.ModelFactory.get_model"></a>

#### get\_model

```python
def get_model(date, latitude, longitude) -> modelchain.ModelChain
```

ModelChain at 'date' time and latitude and longitude location.

<a id="power_simulator.PowerSimulator"></a>

## PowerSimulator Objects

```python
class PowerSimulator(Sensor)
```

This Sensor class implementation provides power consumption simulation.

Similarly to the power_sensor Sensor, the 'read' method returns a record of
power usage except that the data is rather simulated than read from an
actual sensor. The simulation is based on a PV Model, a base power
consumption and the Task status.

<a id="power_simulator.PowerSimulator.model"></a>

#### model

```python
def model(date=None)
```

Return the PV Model Chain at 'date'.

If 'date' is None, it returns the PV Model Chain as for today.

<a id="power_simulator.PowerSimulator.clear_sky_local_weather"></a>

#### clear\_sky\_local\_weather

```python
def clear_sky_local_weather(times)
```

Return a local weather DataFrame assuming clear sky for 'times'.

<a id="power_simulator.PowerSimulator.power_produced_in_the_previous_minute"></a>

#### power\_produced\_in\_the\_previous\_minute

```python
def power_produced_in_the_previous_minute()
```

Return the estimation of power produced in the previous minute.

<a id="power_simulator.PowerSimulator.read"></a>

#### read

```python
@Pyro5.api.expose
def read(**kwargs: dict) -> dict
```

Return an instant record of power usage.

The optional SCALE keyword argument, limited to RecordScale.SECOND and
RecordScale.MINUTE indicates which time unit resolution can be supplied
to read with a different scale order. By default, the resolution is
RecordScale.MINUTE.

The power usage record is an estimate based on the estimation of the
produced current, the 'base_power' and the running tasks.

<a id="power_simulator.PowerSimulator.power"></a>

#### power

```python
@property
@Pyro5.api.expose
def power() -> float
```

Estimated power produced at the time this method is called.

<a id="power_simulator.PowerSimulator.power_at"></a>

#### power\_at

```python
@Pyro5.api.expose
def power_at(date: datetime, temp_air=None, wind_speed=None) -> float
```

Estimated power produced at 'date' time.

If 'temp_air' or 'wind_speed' are not supplied, they are read from the
'weather' forecast service.

<a id="power_simulator.PowerSimulator.daytime"></a>

#### daytime

```python
@property
def daytime() -> tuple
```

Return a couple of datetime objects defining today daytime.

<a id="power_simulator.PowerSimulator.daytime_at"></a>

#### daytime\_at

```python
def daytime_at(date=None)
```

Return a couple of datetime objects defining 'date' daytime.

<a id="power_simulator.PowerSimulator.optimal_time"></a>

#### optimal\_time

```python
@property
def optimal_time()
```

Time when the system is expected to produce the most today.

<a id="power_simulator.PowerSimulator.optimal_time_at"></a>

#### optimal\_time\_at

```python
def optimal_time_at(date)
```

Time when the system is expected to produce the most on date day.

<a id="power_simulator.PowerSimulator.max_available_power"></a>

#### max\_available\_power

```python
@Pyro5.api.expose
@property
def max_available_power()
```

Maximum power available between now and the end of daytime.

<a id="power_simulator.PowerSimulator.max_available_power_at"></a>

#### max\_available\_power\_at

```python
@Pyro5.api.expose
def max_available_power_at(date)
```

Maximum power available between date and the same day dusk.

<a id="power_simulator.PowerSimulator._PowerRange"></a>

## \_PowerRange Objects

```python
class _PowerRange()
```

<a id="power_simulator.PowerSimulator._PowerRange.time"></a>

#### time

```python
def time(index)
```

Return the datetime at 'index'.

<a id="power_simulator.PowerSimulator.next_power_window"></a>

#### next\_power\_window

```python
@Pyro5.api.expose
def next_power_window(power: float) -> tuple
```

Return the next time window when 'min_power' should be available.

Under clear sky weather conditions, it estimates when the system is
likely going to product more than 'min_power'.

<a id="power_simulator.PowerSimulatorProxy"></a>

## PowerSimulatorProxy Objects

```python
class PowerSimulatorProxy()
```

Helper class for the power simulator Sensor and Service.

This class is a wrapper of the power simulator sensor and service with
exception handlers. It provides convenience for services using the weather
Sensor and Service by suppressing the burden of locating them and handling
the various remote object related errors.

<a id="power_simulator.main"></a>

#### main

```python
def main()
```

Register and run the power_simulator Sensor and service.

