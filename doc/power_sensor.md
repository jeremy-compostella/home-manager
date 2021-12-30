<a id="power_sensor"></a>

# power\_sensor

This module implements a power usage sensor based on the Emporia Vue Gen2
device.

<a id="power_sensor.RecordScale"></a>

## RecordScale Objects

```python
class RecordScale(Enum)
```

Task priority levels.

<a id="power_sensor.CacheEntry"></a>

## CacheEntry Objects

```python
class CacheEntry()
```

Represent a cached information for a particular scale.

The cached information is considered expired when the scale time unit has
rolled over. For instance, a RecordScale.SECOND.value cache entry expires on each
second, a RecordScale.MINUTE cache entry expires on each new minute ...

<a id="power_sensor.CacheEntry.value"></a>

#### value

```python
@property
def value() -> dict
```

Return the current cache entry stored value.

<a id="power_sensor.CacheEntry.has_expired"></a>

#### has\_expired

```python
def has_expired() -> bool
```

Return True if the entry value has expired.

<a id="power_sensor.PowerSensor"></a>

## PowerSensor Objects

```python
class PowerSensor(Sensor)
```

This Sensor class implementation provides power consumption readings.

<a id="power_sensor.PowerSensor.read"></a>

#### read

```python
@Pyro5.api.expose
def read(**kwargs: dict) -> dict
```

Return an instant record from the sensor.

The optional SCALE keyword argument, limited to
RecordScale.SECOND, RecordScale.MINUTE,
RecordScale.HOUR and RecordScale.DAY, indicates which time
unit resolution can be supplied to read with a different scale
order. By default, the resolution is RecordScale.MINUTE.

