# Implementing Custom Data Sources

This guide explains how to implement a custom data source for your provider (e.g., DTEK, CEK) using the `ShutdownDataSource` interface. This architecture allows you to easily switch between different data retrieval methods (Web Parser, Database, API, etc.) without changing the bot's core logic.

## Overview

The system uses a decentralized architecture where each provider is responsible for:
1.  Defining its own data source implementation(s).
2.  Providing a factory function `get_data_source()` to instantiate the correct source based on configuration.

The `common` library provides the abstract base class `ShutdownDataSource` and the `ScheduleData` type definition, ensuring a consistent interface across all providers.

## Step 1: Inherit from `ShutdownDataSource`

Create a new class in your provider's package (e.g., `myprovider/data_source.py`) that inherits from `common.data_source.ShutdownDataSource`.

You must implement the `get_schedule` method.

```python
from typing import Optional
from common.data_source import ShutdownDataSource, ScheduleData

class MyProviderDBDataSource(ShutdownDataSource):
    """
    Example implementation fetching data from a database.
    """
    async def get_schedule(self, city: str, street: str, house: str, **kwargs) -> ScheduleData:
        # 1. Fetch data from your source (e.g., DB query)
        # db_result = await my_db.query(...)
        
        # 2. Map the result to the ScheduleData structure
        return ScheduleData(
            city=city,
            street=street,
            house_num=house,
            group="Group 1",
            schedule={
                "24.11.24": [{"shutdown": "10:00-14:00"}, {"shutdown": "18:00-22:00"}]
            }
        )
```

### `ScheduleData` Structure

```python
class ScheduleData(TypedDict):
    city: str
    street: str
    house_num: str
    group: str
    schedule: Dict[str, List[Dict[str, str]]] 
    # schedule format: {"dd.mm.yy": [{"shutdown": "HH:MM-HH:MM"}, ...]}
```

## Step 2: Implement the Factory Function

In the same file (or wherever appropriate in your package), implement a `get_data_source()` function. This function should read the `DATA_SOURCE_TYPE` environment variable and return an instance of your data source class.

```python
import os

def get_data_source() -> ShutdownDataSource:
    """
    Factory to get the configured data source for MyProvider.
    """
    source_type = os.getenv("DATA_SOURCE_TYPE", "PARSER").upper()
    
    if source_type == "DB":
        return MyProviderDBDataSource()
    elif source_type == "API":
        # return MyProviderAPIDataSource()
        pass
    else:
        # Default to Parser
        from myprovider.parser_source import MyProviderParserDataSource
        return MyProviderParserDataSource()
```

## Step 3: Use in Bot

In your bot's code (e.g., `myprovider/bot/bot.py`), simply import and use the factory function.

```python
from myprovider.data_source import get_data_source

async def get_shutdowns_data(city, street, house):
    source = get_data_source()
    return await source.get_schedule(city, street, house)
```

## Configuration

To switch data sources, simply set the environment variable in your deployment:

```bash
export DATA_SOURCE_TYPE=DB
```

No code changes are required in the bot to switch sources once the implementation is in place.

## Docker Deployment

**IMPORTANT:** When you create a new file for your data source (e.g., `myprovider/data_source.py`), you **MUST** update your `Dockerfile` to copy this file into the container image.

Example `Dockerfile` update:

```dockerfile
# ...
COPY myprovider/__init__.py ./myprovider/
COPY myprovider/data_source.py ./myprovider/  # <--- Add this line
# ...
```

If you fail to do this, the bot will crash with a `ModuleNotFoundError` when running in Docker, even if it works locally.
