<a id="water_heater"></a>

# water\_heater

This module implements a water heater Task based on the Aquanta device.

<a id="water_heater.WaterHeaterState"></a>

## WaterHeaterState Objects

```python
class WaterHeaterState()
```

Water heater state: temperature and tank level.

The Aquanta sensors are unreliable and sometimes give the false impression
that the tank is full and the temperature good while actually the water
heater ran for a limited time a obviously isn't full nor has been able to
reach this temperature for the water of the entire tank.

This WaterHeaterState class acts as a proxy by updating the state
representation

<a id="water_heater.WaterHeater"></a>

## WaterHeater Objects

```python
class WaterHeater(Task,  Sensor)
```

Aquanta controlled water heater Task and Sensor.

This task makes use of the Aquanta away and boost features to control the
water heater.

The implementation assumes that the Aquanta device is configured in timer
mode. As a result, the code is a little bit complexity to handle the
schedules but the benefit is that if the task/scheduler stops running, or
if the Aquanta server is inaccessible or, if the API changed unexpectedly,
the Aquanta device should fallback automatically on its schedule.

The Aquanta temperature sensor and available water are per design partially
driven by some software algorithms. Indeed the temperature sensor sit
outside the tank and the water level cannot be detected accurately.
Therefore, if the water heater is not using any power for a little while,
the WaterHeater task stops itself, sets the priority to LOW and waits for
the temperature or available values to change before making any decision.

<a id="water_heater.WaterHeater.min_run_time"></a>

#### min\_run\_time

```python
@property
def min_run_time()
```

Minimal run time for the water heater.

It is to prevent damage of the water heater by turning it on and off
too frequently.

<a id="water_heater.WaterHeater.desired_temperature"></a>

#### desired\_temperature

```python
@property
def desired_temperature()
```

The desired water temperature.

<a id="water_heater.WaterHeater.temperature"></a>

#### temperature

```python
@property
def temperature()
```

Current water temperature.

<a id="water_heater.WaterHeater.available"></a>

#### available

```python
@property
def available()
```

Current water tank level expressed as percent.

<a id="water_heater.WaterHeater.estimate_run_time"></a>

#### estimate\_run\_time

```python
def estimate_run_time()
```

Estimate the required time to reach the target temperature.

<a id="water_heater.WaterHeater.mode"></a>

#### mode

```python
@property
def mode()
```

Return the Aquanta device active mode.

Usually one of 'away', 'boost' or 'timer'.

<a id="water_heater.WaterHeater.start"></a>

#### start

```python
@Pyro5.api.expose
@Pyro5.api.oneway
def start()
```

Turn on the water heater.

<a id="water_heater.WaterHeater.stop"></a>

#### stop

```python
@Pyro5.api.expose
@Pyro5.api.oneway
def stop()
```

Turn off the water heater.

If the water heater is running but has not been running for
MIN_RUN_TIME, this function does not do anything.

<a id="water_heater.WaterHeater.is_runnable"></a>

#### is\_runnable

```python
@Pyro5.api.expose
def is_runnable()
```

True if the Task can be schedule.

<a id="water_heater.WaterHeater.has_been_running_for"></a>

#### has\_been\_running\_for

```python
def has_been_running_for()
```

Return the time the water heater has been running.

<a id="water_heater.WaterHeater.is_stoppable"></a>

#### is\_stoppable

```python
@Pyro5.api.expose
def is_stoppable()
```

Return True if it has been running for MIN_RUN_TIME.

<a id="water_heater.WaterHeater.meet_running_criteria"></a>

#### meet\_running\_criteria

```python
@Pyro5.api.expose
def meet_running_criteria(ratio, power=0)
```

True if the water heater can be turned on or should keep running.

The water heater may not use any power while it is filling the tank and
may stop using power or not starting using any power when the tank is
full tank. This function attempt to detect the best it can when the
water heater should be started or stopped.

- If the water heater tank is full we expect that if started it would
  use power right away. If it does not we make the task not runnable
  for 'no_power_delay'.

- If the water heater has been running for a little while and suddenly
  stop using power, we consider it the tank is full, the water fully
  heated and make the task not runnable for four times
  'no_power_delay'.

<a id="water_heater.WaterHeater.desc"></a>

#### desc

```python
@Pyro5.api.expose
@property
def desc()
```

String representation of the water heater task and status.

<a id="water_heater.WaterHeater.adjust_priority"></a>

#### adjust\_priority

```python
def adjust_priority()
```

Adjust the priority according to the status and target time.

If the temperature and the water availability has not changed since the
last priority adjustment, the function aborts.

The priority is adjusted based on temperature and water availability
thresholds.

If the priority is not the highest and we have less time than estimated
to reach the target, the priority is artificially increased by one
level.

<a id="water_heater.WaterHeater.prevent_auto_start"></a>

#### prevent\_auto\_start

```python
def prevent_auto_start()
```

Prevent automatic turn on.

This function puts the Aquanta in away mode if the schedule is about to
turn the water heater on. The away mode is set for the duration of the
programmed ON schedule.

<a id="water_heater.WaterHeater.today_schedule"></a>

#### today\_schedule

```python
def today_schedule()
```

Return today's schedule as list of [start, stop] datetime.

<a id="water_heater.register"></a>

#### register

```python
def register(name, uri, raise_exception=True)
```

Register 'task' as sensor and task.

<a id="water_heater.device_exist_assert"></a>

#### device\_exist\_assert

```python
def device_exist_assert(device_id, aquanta)
```

Verify that 'device_id' exist for this aquanta account.

It exits with exit code data error if the device is not found or the device
list could not be read.

<a id="water_heater.main"></a>

#### main

```python
def main()
```

Start and register a water heater Task and water heater Sensor.

