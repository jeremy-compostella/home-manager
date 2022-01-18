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

