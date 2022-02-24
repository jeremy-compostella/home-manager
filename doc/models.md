<a id="models"></a>

# models

Process the database and generate models like the HVAC performance model.

<a id="models.HVACModel"></a>

## HVACModel Objects

```python
class HVACModel()
```

Estimate the power and efficiency at an outdoor temperature.

This model is built out of statistics computed from data collected over six
months.

<a id="models.HVACModel.power"></a>

#### power

```python
def power(temperature)
```

Power used by the system running at 'temperature'.

<a id="models.HVACModel.time"></a>

#### time

```python
def time(temperature)
```

Time necessary to change the temperature by one degree.

<a id="models.HomeModel"></a>

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

<a id="models.HomeModel.degree_per_minute"></a>

#### degree\_per\_minute

```python
def degree_per_minute(indoor, outdoor)
```

Temperature change in degree over a minute of time.

It returns the estimated temperature of the house when exposed at an
outdoor 'temperature'. The returned value can be positive or negative.

