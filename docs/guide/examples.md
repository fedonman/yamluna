# Examples

Recipes to copy and adapt. Each one runs as shown and prints the output below it.

## Load settings into dataclasses

Given this `settings.yaml`:

```yaml
# Staging settings
database: !Database
  host: db.internal
  pool: !Pool
    size: 5
replicas: 2  # scale up for load tests
```

Register the classes, then load, edit, and save the file:

```python
from dataclasses import dataclass
from pathlib import Path
from yamluna import YAML

yaml = YAML()


@yaml.register_class
@dataclass
class Pool:
    size: int
    timeout: float = 30.0


@yaml.register_class
@dataclass
class Database:
    host: str
    pool: Pool


path = Path('settings.yaml')
settings = yaml.load(path)
settings['database'].pool.size = 10
settings['replicas'] = 4
yaml.dump(settings, path)
print(path.read_text(), end='')
```

Output:

```text
# Staging settings
%TAG ! tag:__main__/
---
database: !Database
  host: db.internal
  pool: !Pool
    size: 10
replicas: 4  # scale up for load tests
```

`settings['database']` is a `Database` and its `pool` is a `Pool`, so the edit is ordinary attribute access. The first save adds a `%TAG` line naming the package the classes came from.

## Keep prices exact with Decimal

`Decimal` comes from the standard library, so register it by passing two functions:

```python
from decimal import Decimal
from yamluna import YAML


def decimal_to_yaml(representer, value):
    return representer.represent_scalar(representer.plan.tags[Decimal], str(value))


def decimal_from_yaml(constructor, node):
    return Decimal(node.value)


yaml = YAML()
yaml.register_class(Decimal, to_yaml=decimal_to_yaml, from_yaml=decimal_from_yaml)

prices = yaml.load("""\
basic: !Decimal 9.99    # per month
pro: !Decimal 19.99
""")
prices['pro'] += Decimal('0.01')
print(yaml.dump(prices), end='')
print(sum(prices.values()))
```

Output:

```text
%TAG ! tag:decimal/
---
basic: !Decimal 9.99    # per month
pro: !Decimal 20.00
29.99
```

The values are `Decimal` objects from the moment they load, so the sum has no floating-point rounding.

## Store durations as timedelta

```python
from datetime import timedelta
from yamluna import YAML


def timedelta_to_yaml(representer, value):
    text = f'{value.total_seconds():g}s'
    return representer.represent_scalar(representer.plan.tags[timedelta], text)


def timedelta_from_yaml(constructor, node):
    return timedelta(seconds=float(node.value.removesuffix('s')))


yaml = YAML()
yaml.register_class(timedelta, to_yaml=timedelta_to_yaml, from_yaml=timedelta_from_yaml)

config = yaml.load('retry: !timedelta 90s  # between attempts\n')
print(repr(config['retry']))
config['retry'] *= 2
print(yaml.dump(config), end='')
```

Output:

```text
datetime.timedelta(seconds=90)
%TAG ! tag:datetime/
---
retry: !timedelta 180s # between attempts
```

The comment keeps its column, so the longer value leaves one space before it.

## Store an Enum by name

```python
from enum import Enum
from yamluna import YAML


class Level(Enum):
    DEBUG = 10
    INFO = 20


def level_to_yaml(representer, level):
    return representer.represent_scalar(representer.plan.tags[Level], level.name)


def level_from_yaml(constructor, node):
    return Level[node.value]


yaml = YAML()
yaml.register_class(Level, to_yaml=level_to_yaml, from_yaml=level_from_yaml)

config = yaml.load('log_level: !Level INFO  # DEBUG for local runs\n')
print(config['log_level'] is Level.INFO)
config['log_level'] = Level.DEBUG
print(yaml.dump(config), end='')
```

Output:

```text
True
%TAG ! tag:__main__/
---
log_level: !Level DEBUG # DEBUG for local runs
```

`from_yaml` returns the existing member, so identity checks such as `is Level.INFO` work after loading.

## Store numpy arrays

Register `numpy.ndarray` with a `to_yaml` that writes the dtype, shape, and data, and a `from_yaml` that rebuilds the array from them. [Register a class you do not own](custom-classes.md#register-a-class-you-do-not-own) has the complete code, and the repository's [`register_class.py`](https://github.com/fedonman/yamluna/blob/main/examples/register_class.py) runs it.

## Update an image version

```python
from yamluna import YAML

yaml = YAML()
yaml.preserve_quotes = True
config = yaml.load('image: "app:1.4"  # approved release\nreplicas: 3\n')
config['image'] = config['image'].replace('1.4', '1.5')
print(yaml.dump(config), end='')
```

Output:

```yaml
image: "app:1.5"  # approved release
replicas: 3
```

## Generate a commented config

```python
from yamluna import YAML, CommentedMap

yaml = YAML()
config = CommentedMap({'host': 'localhost', 'port': 8080})
config.yaml_set_start_comment('Local development')
config.yaml_add_eol_comment('HTTP port', 'port')
print(yaml.dump(config), end='')
```

Output:

```yaml
# Local development
host: localhost
port: 8080 # HTTP port
```

## More recipes

- [Choose a class's tag and namespace](custom-classes.md#choose-the-tag-and-namespace)
- [Write a class as a single scalar](custom-classes.md#choose-how-your-class-is-written)
- [Share classes between a writer and a reader](../api/registry.md#share-classes-between-a-writer-and-a-reader)
- [Edit a file](load-and-dump.md#edit-a-file)
- [Read multiple documents](load-and-dump.md#multiple-documents)
- [Remove a setting and its comment](comments.md#remove-a-setting-and-its-comment)
- [Write a multiline script](scalars.md#write-multiline-text)
- [Override a shared default](anchors.md#override-a-default)

The repository also includes runnable scripts: [register_class](https://github.com/fedonman/yamluna/blob/main/examples/register_class.py), [class namespaces](https://github.com/fedonman/yamluna/blob/main/examples/custom_classes.py), [round trip](https://github.com/fedonman/yamluna/blob/main/examples/round_trip.py), and [comments](https://github.com/fedonman/yamluna/blob/main/examples/comments.py).
