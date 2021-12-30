<a id="weather"></a>

# weather

Weather Sensor and a weather forecast Service implementation.

<a id="weather.WeatherSensor"></a>

## WeatherSensor Objects

```python
class WeatherSensor(Sensor)
```

Provide instantaneous weather information as a Sensor.

It uses the OpenWeather API.

<a id="weather.WeatherSensor.read"></a>

#### read

```python
@Pyro5.api.expose
def read(**kwargs) -> dict
```

Return the current weather conditions.

The conditions includes the 'temperature', 'wind_speed', 'wind_degree',
'weather_code' and 'humidity' level information.

For more details on the 'weather_code' field, consult
https://openweathermap.org/weather-conditions.

<a id="weather.WeatherForecastService"></a>

## WeatherForecastService Objects

```python
class WeatherForecastService()
```

Provide weather forecast information.

It uses on the National Weather Service (NWS) API.

<a id="weather.WeatherForecastService.conditions_at"></a>

#### conditions\_at

```python
@Pyro5.api.expose
def conditions_at(target: datetime) -> dict
```

Return the condition at TARGET time.

The conditions include the 'temperature', 'wind_speed' and
'wind_degree'.

<a id="weather.WeatherProxy"></a>

## WeatherProxy Objects

```python
class WeatherProxy(Sensor)
```

Helper class for weather Sensor and Service.

This class is a wrapper of the weather sensor and service with exception
handlers. It provides convenience for services using the weather Sensor
and Service by suppressing the burden of locating them and handling the
various remote object related errors.

This class also introduces some direct attributes such as 'temperature',
'wind_speed', 'wind_degree', 'weather_code' and 'humidity' providing
simpler access to the current weather condition information. These fields
can also be retrieved all at once using the 'read' method.

It introduces the 'temperature_at(datetime)', 'wind_speed_at(datetime)' and
'wind_direction_at(datetime)' methods providing a simpler access to
targeted weather forecast information. These fields can also be retrieved
all at once using the 'conditions_at(datetime)' method.

<a id="weather.WeatherProxy.read"></a>

#### read

```python
def read(**kwargs) -> dict
```

Return the instantaneous weather condition.

<a id="weather.WeatherProxy.units"></a>

#### units

```python
def units(**kwargs) -> dict
```

Return the instantaneous weather condition.

<a id="weather.WeatherProxy.conditions_at"></a>

#### conditions\_at

```python
def conditions_at(date: datetime) -> dict
```

Return the forecast weather condition at 'date'.

<a id="weather.main"></a>

#### main

```python
def main()
```

Register and run the weather Sensor and weather forecast Service.

