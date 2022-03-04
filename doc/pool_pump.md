<a id="pool_pump"></a>

# pool\_pump

This module implements a pool pump task on Migro switch.

<a id="pool_pump.Ewelink"></a>

## Ewelink Objects

```python
class Ewelink()
```

Helper class to communicate with the Ewelink server.

<a id="pool_pump.PoolPump"></a>

## PoolPump Objects

```python
class PoolPump(Task)
```

This task uses a Migro switch to control a pool pump.

<a id="pool_pump.PoolPump.update_remaining_runtime"></a>

#### update\_remaining\_runtime

```python
def update_remaining_runtime()
```

Update the remaining runtime counter.

<a id="pool_pump.PoolPump.has_been_running_for"></a>

#### has\_been\_running\_for

```python
def has_been_running_for()
```

Return the time the pool pump has been running.

<a id="pool_pump.PoolPump.adjust_priority"></a>

#### adjust\_priority

```python
def adjust_priority()
```

Update the priority according to the target time

<a id="pool_pump.already_ran_today_for"></a>

#### already\_ran\_today\_for

```python
def already_ran_today_for(min_power=.5)
```

Return how long the pool pump has been running today based.

It uses the database power table.

<a id="pool_pump.configure_cycle"></a>

#### configure\_cycle

```python
def configure_cycle(task, power_simulator, weather)
```

Compute and set the current cycle target time and runtime.

<a id="pool_pump.main"></a>

#### main

```python
def main()
```

Register and run the pool pump task.

