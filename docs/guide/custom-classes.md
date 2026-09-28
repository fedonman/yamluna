# Custom classes

Register a class to write its objects as tagged YAML and load them back as the same class. There are two ways to do it: decorate a class you own, or call `register_class()` with two functions for a class you cannot change. Registrations belong to the `YAML()` instance you register them on.

## Register your own class

Put `@yaml.register_class` on the class. A dataclass needs nothing more:

```python
from dataclasses import dataclass
from yamluna import YAML

yaml = YAML()


@yaml.register_class
@dataclass
class Server:
    host: str
    port: int = 80


config = yaml.load("""\
# Production servers
primary: !Server
  host: web-1
  port: 8080
timeout: 30  # seconds
""")

print(config['primary'])
config['primary'].port = 9090
config['canary'] = Server('web-3')
print(yaml.dump(config), end='')
```

Output:

```text
Server(host='web-1', port=8080)
# Production servers
%TAG ! tag:__main__/
---
primary: !Server
  host: web-1
  port: 9090
timeout: 30  # seconds
canary: !Server
  host: web-3
  port: 80
```

`!Server` names the class and the `%TAG` line names the package it came from, which defaults to the root package of the class's module (`__main__` for a script). The hand-written file had no `%TAG` line; a bare `!Server` loads as long as only one registered class is called `Server`.

By default yamluna writes the object's `__dict__`, or what `__getstate__` returns, as a mapping, and restores it without calling `__init__`. `@yaml.register` is a shorter spelling of the same decorator. Any class works, not only dataclasses.

## Choose the tag and namespace

Pass `tag` and `source` to name the class yourself:

```python
yaml = YAML()
yaml.register_class(Server, tag='Host', source='myapp')
print(yaml.dump({'primary': Server('web-1')}), end='')
```

Output:

```yaml
%TAG ! tag:myapp/
---
primary: !Host
  host: web-1
  port: 80
```

A decorated class can set the same options as class attributes, `yaml_tag = 'Host'` and `yaml_source = 'myapp'`, which is the spelling ruamel.yaml code already uses. Keyword arguments win over the attributes. Registering a class again replaces its earlier registration.

## Choose how your class is written

Define `to_yaml` and `from_yaml` classmethods to control the YAML form. This `Version` is written as one scalar instead of a mapping:

```python
from dataclasses import dataclass
from yamluna import YAML

yaml = YAML()


@yaml.register_class
@dataclass
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def to_yaml(cls, representer, obj):
        text = f'{obj.major}.{obj.minor}.{obj.patch}'
        return representer.represent_scalar(representer.plan.tags[cls], text)

    @classmethod
    def from_yaml(cls, constructor, node):
        return cls(*map(int, node.value.split('.')))


config = yaml.load('release: !Version 1.4.2  # approved\n')
print(config['release'])
config['release'] = Version(1, 5, 0)
print(yaml.dump(config), end='')
```

Output:

```text
Version(major=1, minor=4, patch=2)
%TAG ! tag:__main__/
---
release: !Version 1.5.0  # approved
```

`representer.plan.tags[cls]` is the tag the document uses for the class, with whichever handle the `%TAG` lines gave it. Existing ruamel.yaml hooks use the same signatures.

## Register a class you do not own

A class from the standard library or another package cannot take classmethods, so pass the hooks to `register_class()` as plain functions. They take the same arguments as the classmethods, without `cls`:

```python
from decimal import Decimal

import numpy as np
from yamluna import YAML, CommentedSeq


def decimal_to_yaml(representer, value):
    return representer.represent_scalar(representer.plan.tags[Decimal], str(value))


def decimal_from_yaml(constructor, node):
    return Decimal(node.value)


def flow(items):
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def array_to_yaml(representer, array):
    fields = {
        'dtype': str(array.dtype),
        'shape': flow(array.shape),
        'data': flow(array.ravel().tolist()),
    }
    return representer.represent_mapping(representer.plan.tags[np.ndarray], fields)


def array_from_yaml(constructor, node):
    fields = constructor.construct_mapping(node)
    return np.array(fields['data'], dtype=fields['dtype']).reshape(fields['shape'])


yaml = YAML()
yaml.register_class(Decimal, to_yaml=decimal_to_yaml, from_yaml=decimal_from_yaml)
yaml.register_class(np.ndarray, to_yaml=array_to_yaml, from_yaml=array_from_yaml)

model = {
    'learning_rate': Decimal('0.001'),
    'weights': np.array([[0.5, 1.5], [2.0, 3.0]], dtype=np.float32),
}
text = yaml.dump(model)
print(text, end='')
print(repr(yaml.load(text)['weights']))
```

Output:

```text
%TAG ! tag:decimal/
%TAG !numpy! tag:numpy/
---
learning_rate: !Decimal 0.001
weights: !numpy!ndarray
  dtype: float32
  shape: [2, 2]
  data: [0.5, 1.5, 2.0, 3.0]
array([[0.5, 1.5],
       [2. , 3. ]], dtype=float32)
```

With two sources in one document, one keeps the primary `!` handle and the other gets a named one, `!numpy!`. The `flow()` helper writes a list inline, as `[2, 2]`, instead of one item per line. Functions passed to `register_class()` win over classmethods the class already has.

The repository's [`register_class.py`](https://github.com/fedonman/yamluna/blob/main/examples/register_class.py) example runs all of the above.

## Hook reference

| In a hook | Use |
| --- | --- |
| `representer.plan.tags[cls]` | The tag to write for a registered class |
| `representer.represent_scalar(tag, value, style=None)` | Write one scalar; `style` is `'`, `"`, `\|`, or `>` |
| `representer.represent_mapping(tag, mapping)` | Write a mapping; its values are represented in turn |
| `representer.represent_sequence(tag, items)` | Write a sequence |
| `node.value` | Read a scalar's text in `from_yaml` |
| `constructor.construct_mapping(node)` | Read a mapping's contents as a `CommentedMap` |
| `constructor.construct_sequence(node)` | Read a sequence's contents as a `CommentedSeq` |

`to_yaml` must return what the `represent_*` call returns.

## Read mapping and sequence contents in a hook

`construct_mapping()` and `construct_sequence()` build a collection's contents without applying the collection's own tag again. Nested collections, registered classes, and aliases are resolved normally; comments, formatting, and mapping merges are preserved in the returned container. Repeated calls for the same node return the same container, so child hooks are not run again. Passing the wrong node kind raises `ConstructorError`.

This example stores a point as a mapping and a set of samples as a sequence:

```python
from dataclasses import dataclass
from yamluna import YAML

yaml = YAML()


@yaml.register_class
@dataclass
class Point:
    x: float
    y: float

    @classmethod
    def to_yaml(cls, representer, obj):
        return representer.represent_mapping(representer.plan.tags[cls], {'x': obj.x, 'y': obj.y})

    @classmethod
    def from_yaml(cls, constructor, node):
        return cls(**constructor.construct_mapping(node))


@yaml.register_class
@dataclass
class Samples:
    values: list[float]

    @classmethod
    def to_yaml(cls, representer, obj):
        return representer.represent_sequence(representer.plan.tags[cls], obj.values)

    @classmethod
    def from_yaml(cls, constructor, node):
        return cls(constructor.construct_sequence(node))


values = {'point': Point(1.0, 2.0), 'samples': Samples([0.25, 0.5])}
assert yaml.load(yaml.dump(values)) == values
```

## Classes with the same name

yamluna keys each registration on the class's full path, not its name, so `libx.Server` and `liby.Server` can both be registered on one instance. The saved file gets a `%TAG` line for each source, and the tags say which class is which: one is written `!Server` and the other `!liby!Server`.

A hand-written bare tag such as `!Server` must identify exactly one class. With two candidates, loading raises `ConstructorError` naming both, rather than picking one. Add a `%TAG` directive to the file, or register one class with an explicit `source`.

## Share registrations

A fresh `YAML()` has an empty registry. Use the same instance to save and load, or pass one `TagRegistry` to several instances. The [registry reference](../api/registry.md) has an example.

For application-wide registration, the module-level `yamluna.register_class()` writes to `yamluna.default_registry`, which an instance uses only when you pass it as `YAML(registry=yamluna.default_registry)`.

## Preserve tagged files without constructing objects

Tags from unknown namespaces are kept as YAML, so you can edit a file without having every class installed or registered.

Registering a class changes that: comments and formatting **inside** a constructed object are lost when you save it, because the object holds only its fields. Leave the tag unregistered when those details matter. Comments outside the object, such as `# seconds` above, are kept.
