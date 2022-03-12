<a id="sensor_logger"></a>

# sensor\_logger

This module logs the sensors records every minute into the database.

<a id="sensor_logger.field_name"></a>

#### field\_name

```python
def field_name(name)
```

Turn name into SQL field name compatible string.

<a id="sensor_logger.field_type"></a>

#### field\_type

```python
def field_type(value)
```

Return the SQL type of "value"

<a id="sensor_logger.dict_to_table_fields"></a>

#### dict\_to\_table\_fields

```python
def dict_to_table_fields(data)
```

Turn "data" dictionary into a SQL table fields description

<a id="sensor_logger.execute"></a>

#### execute

```python
def execute(cursor, *args)
```

Execute an SQL request and handle database concurrency

<a id="sensor_logger.create_table"></a>

#### create\_table

```python
def create_table(table_name, cursor, data)
```

Create "table_name" table if it does not exist

<a id="sensor_logger.main"></a>

#### main

```python
def main()
```

Start and register a the sensor logger service.

