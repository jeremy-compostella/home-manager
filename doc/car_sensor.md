<a id="car_sensor"></a>

# car\_sensor

This module implements a car Sensor providing state of charge and mileage
information based on an OBDII bluetooth adapter.

<a id="car_sensor.CarSensor"></a>

## CarSensor Objects

```python
class CarSensor(Sensor)
```

Sensor collecting information via an OBDII bluetooth adapter.

<a id="car_sensor.CarSensor.update"></a>

#### update

```python
def update()
```

Attempt to collect a record from the car.

<a id="car_sensor.CarSensorProxy"></a>

## CarSensorProxy Objects

```python
class CarSensorProxy(Sensor)
```

Helper class for Car Sensor.

This class is a wrapper of the car sensor and service with exception
handlers. It provides convenience for services using the car Sensor
and Service by suppressing the burden of locating them and handling the
various remote object related errors.

<a id="car_sensor.main"></a>

#### main

```python
def main()
```

Register and run the Car Sensor.

