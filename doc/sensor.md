<a id="sensor"></a>

# sensor

This module provides the Sensor interface class.

<a id="sensor.Sensor"></a>

## Sensor Objects

```python
class Sensor()
```

This class is the interface a Sensor should implement.

<a id="sensor.Sensor.read"></a>

#### read

```python
@abstractmethod
def read(**kwargs: dict) -> dict
```

Return a sensor record.

<a id="sensor.Sensor.units"></a>

#### units

```python
@abstractmethod
def units(**kwargs: dict) -> dict
```

Return the sensor unit mapping

<a id="sensor.SensorReader"></a>

## SensorReader Objects

```python
class SensorReader(Sensor)
```

This class is sensor wrapper with  error management.

It discharges the caller from having to handle various exceptions. On a
sensor read() failure, the wrapper returns None. The caller can use the
time_elapsed_since_latest_record() method to know the time elapsed since it
successfully retrieved a record.

<a id="sensor.SensorReader.read"></a>

#### read

```python
def read(**kwargs) -> dict
```

Read a sensor record.

It returns an empty dictionary if the sensor read() method raises an
Pyro5.errors.CommunicationError or RuntimeError exception.

<a id="sensor.SensorReader.time_elapsed_since_latest_record"></a>

#### time\_elapsed\_since\_latest\_record

```python
def time_elapsed_since_latest_record() -> timedelta
```

Time elapsed since read() successfully retrieved a record.

