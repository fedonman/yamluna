# Tag registry

Each `YAML()` starts with its own registrations. Use `yaml.register_class()` for most tasks; create a `TagRegistry` when several instances should share them.

## Share classes between a writer and a reader

```python
from dataclasses import dataclass
from yamluna import YAML, TagRegistry

@dataclass
class Server:
    port: int

registry = TagRegistry()
writer = YAML(registry=registry)
reader = YAML(registry=registry)
writer.register_class(Server, source='myapp')

text = writer.dump(Server(8080))
print(reader.load(text))
```

Output:

```text
Server(port=8080)
```

## Registration options

`yaml.register_class(cls, *, tag=None, source=None, to_yaml=None, from_yaml=None)`

| Option | Default | Use |
| --- | --- | --- |
| `tag` | `cls.yaml_tag` or the class name | Choose the tag name |
| `source` | `cls.yaml_source` or the root package | Choose its namespace |
| `to_yaml` | The class hook, or its state | Write a custom representation |
| `from_yaml` | The class hook, or restore its state | Construct a custom value |

Registering the same class again replaces its registration, including hooks. Two classes with the same explicit `source` and `tag` are ambiguous; give one a different source or tag.

For application-wide registration, `yamluna.register_class()` writes to `yamluna.default_registry`. Opt into it with `YAML(registry=yamluna.default_registry)`; a plain `YAML()` stays independent.

See [custom classes](../guide/custom-classes.md) for complete save-and-load examples.
