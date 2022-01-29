**Solar Power usage optimization in a residential home**

This project aims to optimize the use of solar produced energy in a residential home. In the implementation described below it optimizes three appliances: an Electric Vehicle charger, a water heater and an HVAC system. The project has focused so far on these three appliances because they add up to approximately `72.2%` of a my home energy usage and they are a form of energy storage.

My home is located in Phoenix, Arizona and my solar system is not equipped with battery system. Even though at this location 296 days per year are either sunny or partly sunny, photovoltaic production is still highly unpredictable.

There are a couple of ways to measure the performance of a solar panels system in a residential home depending on expectations.

1.  Return On Investment (ROI): measure from a financial point of point when does the system paid for itself and how much value it can create in it life time.
2.  Usage vs. production ratio at the time of need: measure the "waste" of the production. This metric can be used to design systems with the best ROI. In Phoenix, Arizona utility companies buy the over production from residential home for a small fraction of the price of the energy they sell. Minimizing the over-production is critical to generate the best ROI.
3.  Solar produced energy usage performance as measured by the ratio of energy used from the solar panel over the total energy used. To me this is the most environment oriented metric and this is the one I track.

With a few experiments, I quickly confirmed the obvious: a time based schedule is not going to be enough to meet to the ambitious goal of running 100% of the car and &asymp; 60 to 70% of my entire home consumption coming from the solar panels production.

The illustration below shows the distribution of the energy consumption of my home since I started this project.

![img](doc/images/energy_consumption_distribution.svg)

I started the project during summer and made considerable progress since then but we also have to factor in that during summer the HVAC has to run at night and is necessarily going to have a significant impact on this metric. Nevertheless, it allows to see that between `June 2021` and `January 2022`, `62.5%` of the electricity used by my home came from the solar panels.

The implementation relies on various modules providing services such as weather forecast, instant power consumption records, solar power prediction, home thermal model, appliances controls and a scheduler. **Note**: This project implementation is not generic enough to be plugged-in as-is to control any system. Some modules rely on specific hardware devices or specific software interfaces. For instance, the `car_charger` task module relies on the Pulsar II Wallbox&reg; charger   and have dependencies on their cloud service. Nevertheless, I believe that most of this project can be re-use in various forms and this is why I made it available publicly.

When I started this project I discarded existent Home Automation "framework" such as Home Assistant as I didn't want my design to be tighten to a framework or have to carry the burden of software architecture which could have made my work more difficult. Also, I originally needed a prompt solution to charge my car smartly and I did not have time to dig in the various frameworks. However, this project is modular enough that I have been able to quickly integrate it to Home Assistant as you can see in the screen capture below.

![img](./doc/images/scheduler_at_work.png)

The software architecture is based on well separated python modules each running in their dedicate process. Each module implements known interfaces such as [Sensor](doc/sensor.md#sensor-objects) or [Task](doc/scheduler.md#task-objects). Alternatively or in addition, a module can expose its a original service interface. The inter-processes communication is guaranteed by the [pyro5](https://pypi.org/project/Pyro5/) remote objects communication library. Each service, sensor or task is registered to a pyro5 nameserver under the `home-manager` namespace.  The services are under the `home-manager.service` sub-namespace, the sensors under the `home-manager.sensor` sub-namespace and the tasks are under the `home-manager.task` sub-namespace. For instance, the task responsible of the HVAC system is implemented by the [hvac.py](./src/hvac.py) program and registered as `home-manager.task.hvac` to the nameserver.

The diagram below represents the principal communications between the main modules.

![img](doc/images/programs-communication.svg)

Now let's have go a short presentation or the modules:

<span class="underline">The main sensors are</span>:

1.  The [power\_sensor](./doc/power_sensor.md) (`home_manager.sensor.power`) module provides instantaneous power consumption and power production readings. It also provides power reading over a certain period of time such as one minute, one hour or one day. This sensor is used by the [scheduler](./doc/scheduler.md) to build power consumption statistics leading to task scheduling decisions. Some task such as [car\_charger](./doc/car_charger.md) also uses this sensor.
2.  The [power\_simulator](./doc/power_simulator.md) (`home_manager.sensor.power_simulator` and `home_manager.sensor.power_simulator`) module implements the solar panel model of my installation using the python [pvlib](https://pvlib-python.readthedocs.io/en/stable/) library.
    -   Similarly to [power\_sensor](./doc/power_sensor.md) the sensor part of this module provides instantaneous power consumption and production readings except that the production reading are based on a solar panel model and the consumption reading are based on current the tasks status. This [power\_simulator](./doc/power_simulator.md) is used as an alternative if [power\_sensor](./doc/power_sensor.md) is failing by the [scheduler](./doc/scheduler.md) and the [car\_charger](./doc/car_charger.md).
    -   The service part of this module provides properties and functions such as:
        -   the [max\_available\_power](./doc/power_simulator.md#max_available_power) property which is the maximum instantaneous power in kW the solar panels are expected to deliver from now to the end of daytime
        -   the [next\_power\_window(power)](./doc/power_simulator.md#next_power_window) function which returns the next time frame when `power` kW would be available on a perfectly sunny day. This information is useful to tasks which need to know until when they can expect to get enough power to run.
3.  The [weather](./doc/weather.md) (`home_manager.sensor.weather` and `home_manager.service.weather`) module provides instantaneous weather information such as temperature or wind speed as a [Sensor](doc/sensor.md#sensor-objects) object. It also provides weather forecast service with methods to get data such as the temperature at the certain point in time. The forecast service is important to multiple modules. For instance, a solar panel production performance depends on multiple factors and in particular the temperature thus the [power\_simulator](./doc/power_simulator.md) uses the weather forecast service to compute an accurate PV panels productions estimation.
4.  The [car\_sensor](./doc/car_sensor.md) (`home_manager.sensor.car`) module provides information such as the car current state of charge and mileage.

The central piece of the system is the [scheduler](./doc/scheduler.md). The [scheduler](./doc/scheduler.md) is responsible of optimally schedule registered tasks depending on their priority level, their power needs, some task specific running criteria and of course, power availability. The scheduler module evaluates the situation and makes new decision every minutes. It computes power consumption statistics with a sliding window of power records and uses this data to determine the ratio of the energy a particular appliance has been consuming compared to what the photovoltaic system has been producing. This ratio represents how much of the energy used by a particular appliance has been covered by the photovoltaic production. This ratio is provided to each tasks which are responsible to let the scheduler know if this ratio would be good enough to start the task or keep it running if it is already started.

The project provides multiple tasks:

1.  The [car\_charger](./doc/car_charger.md) task is responsible of charging the Electric Vehicle. It uses a simple strategy: the priority is set depending on the car current state of charge, the lower the state of charge the higher the priority. When this task is started, it automatically adjusts the charging rate depending on the power availability and it does so multiple times a minute.
2.  The [water\_heater](./doc/water_heater.md) task is responsible of heating the water tank. In opposition to the car which has a large enough capacity to be able to skip a couple of days of charge the water heater has to run every single day regardless of the photovoltaic production. Therefor the strategy is a little bit more complex: the task priority is set based on the water tank level and temperature but also on how close we are of the target time. The target time is defined as the last point in time of the day when the photovoltaic system theoretically produces enough power to cover 100% of the water heater needs. In addition to that, if the priority is the highest possible and we are close to the target time, the water heater reports that it meets its running criteria regardless of the current consumption/production ratio. That way the [water\_heater](./doc/water_heater.md) task is guaranteed to be scheduled and meet its daily goal.
3.  The [hvac](./doc/hvac.md) task is responsible of heating and cooling the home during daylight. At night, the regular thermostat schedule resumes. In my home the HVAC system clearly is the appliance consuming the most energy and this is why the HVAC optimization is critical.
    Similarly to the [water\_heater](./doc/water_heater.md) task a target time is determined thanks to the [power\_simulator](./doc/power_simulator.md). However, the algorithm determining the target time is slightly more complex because the HVAC system power consumption varies when the outdoor temperature changes and under high temperature or low temperature, the HVAC system needs more power than what the photovoltaic system can produce. In order to calculate the target time, the hvac task uses a performance model of the HVAC system built out of data recorded over several month of use. The following diagram is a representation of the HVAC performance model. For a certain range of outdoor temperatures, the blue line represents the power used by the HVAC system and the orange line the number of minutes needed to change the temperature by one degree Fahrenheit.
    
    ![img](./doc/images/hvac_model.svg)
    
    The HVAC system needs a target time but also a target temperature. The target temperature is defined as the temperature to be at target time so that at a later specified time the home would be at a desired temperature. For instance, if the desire is to have a temperature of 73°F at 11pm, the [hvac](./doc/hvac.md) task computes what the temperature should be at target time taking into account the expected temperature change of the home between the target time and 11PM. This computation relies on a home thermal model built out of data captured over several months.

