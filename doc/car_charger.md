<a id="car_charger"></a>

# car\_charger

This module implements a car charger task based on the Wallbox EV charger.

<a id="car_charger.CarCharger"></a>

## CarCharger Objects

```python
class CarCharger(Task)
```

Wallbox car charger Task.

This task handles a Wallbox car charger and automatically adjusts the
charge rate based on produced power availability.

<a id="car_charger.CarCharger.status"></a>

#### status

```python
@property
def status()
```

JSON representation of the charger status.

<a id="car_charger.CarCharger.status_description"></a>

#### status\_description

```python
@property
def status_description()
```

String describing the charger status.

<a id="car_charger.CarCharger.min_available_current"></a>

#### min\_available\_current

```python
@property
def min_available_current()
```

Minimum current supported by the charger in Ampere.

<a id="car_charger.CarCharger.max_available_current"></a>

#### max\_available\_current

```python
@property
def max_available_current()
```

Maximal current supported by the charger in Ampere.

<a id="car_charger.CarCharger.is_runnable"></a>

#### is\_runnable

```python
@Pyro5.api.expose
def is_runnable()
```

True if calling the 'start' function would initiate charging.

<a id="car_charger.CarCharger.adjust_priority"></a>

#### adjust\_priority

```python
def adjust_priority(state_of_charge)
```

Update the priority according to the current state of charge

<a id="car_charger.CarCharger.current_rate_for"></a>

#### current\_rate\_for

```python
def current_rate_for(power)
```

Return the appropriate current in Ampere for POWER in KWh.

<a id="car_charger.CarCharger.adjust_charge_rate"></a>

#### adjust\_charge\_rate

```python
def adjust_charge_rate(record)
```

Adjust the charging rate according to the instant POWER record.

<a id="car_charger.main"></a>

#### main

```python
def main()
```

Register and run the car charger task.

