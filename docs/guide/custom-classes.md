# Custom classes

Register a class to save its instances as YAML and load them back as Python objects. Registrations belong to the `YAML()` instance you use.

## Save and load a class

```python
from dataclasses import dataclass
from yamluna import YAML

@dataclass
class Server:
    host: str
    port: int

yaml = YAML()
yaml.register_class(Server, source='myapp')
text = yaml.dump({'server': Server('localhost', 8080)})
print(text, end='')
print(yaml.load(text)['server'])
```

Output:

```text
%TAG ! tag:myapp/
---
server: !Server
  host: localhost
  port: 8080
Server(host='localhost', port=8080)
```

`!Server` names the class. The `%TAG` line identifies its source, so classes from different packages can share a name without overwriting each other. `source=` is optional; it defaults to the class's root package.

By default, yamluna saves the object's state and restores it without calling `__init__`. Use `from_yaml` when loading needs validation or construction logic. `register_class()` also works as the decorator `@yaml.register`.

## Choose how a value is written

Pass `to_yaml` and `from_yaml` functions for a class you cannot change. Here a `Decimal` is stored as a tagged number instead of a mapping:

```python
from decimal import Decimal
from yamluna import YAML

def write_decimal(representer, value):
    return representer.represent_scalar('!Decimal', str(value))

def read_decimal(constructor, node):
    return Decimal(node.value)

yaml = YAML()
yaml.register_class(Decimal, to_yaml=write_decimal, from_yaml=read_decimal)
text = yaml.dump({'price': Decimal('19.99')})
print(text, end='')
print(repr(yaml.load(text)['price']))
```

Output:

```text
%TAG ! tag:decimal/
---
price: !Decimal 19.99
Decimal('19.99')
```

You can also define these hooks on the class as classmethods, with `cls` as the first argument. Existing ruamel.yaml hooks use the same signatures.

## Share registrations when you need to

A fresh `YAML()` has an empty registry. Use the same instance to save and load, or pass a shared `TagRegistry` to several instances. See the [registry reference](../api/registry.md) for an example.

If two registered classes share a name, yamluna writes the namespace needed to tell them apart. A hand-written bare tag such as `!Server` must identify exactly one class; ambiguous tags raise `ConstructorError`.

## Preserve tagged files without constructing objects

Tags from unknown namespaces are kept as YAML, so you can edit a file without having every class installed or registered.

Registering a class changes that behavior: comments and formatting **inside** the constructed object may be lost when you save it. Leave the tag unregistered when preserving those details matters. Comments outside the object are kept.
