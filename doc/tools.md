<a id="tools"></a>

# tools

This module provides tool functions and classes for the entire project.

<a id="tools.debug"></a>

#### debug

```python
def debug(text)
```

Record text to the log file.

<a id="tools.log_exception"></a>

#### log\_exception

```python
def log_exception(msg, exc_type, exc_value, exc_traceback)
```

Record the msg and the exception to the log file.

<a id="tools.Settings"></a>

## Settings Objects

```python
class Settings()
```

Represent key/value pair settings.

Settings can be loaded from the configuration file. All the key/value pair
under the 'settings' are loaded as attributes of the Settings object.

<a id="tools.Settings.load"></a>

#### load

```python
def load()
```

Load the settings from filename supplied at construction.

<a id="tools.get_storage"></a>

#### get\_storage

```python
def get_storage()
```

Return a shelve object for dynamic data storage.

<a id="tools.get_database"></a>

#### get\_database

```python
def get_database()
```

Return a SQLite object for persistent data storage.

<a id="tools.db_field_type"></a>

#### db\_field\_type

```python
def db_field_type(value)
```

Return the SQL type of "value"

<a id="tools.db_dict_to_fields"></a>

#### db\_dict\_to\_fields

```python
def db_dict_to_fields(data)
```

Turn "data" dictionary into a SQL table fields description

<a id="tools.my_excepthook"></a>

#### my\_excepthook

```python
def my_excepthook(etype, value=None, traceback=None)
```

On uncaught exception, log the exception and kill the process.

