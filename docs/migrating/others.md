# Switch from py-yaml12 or StrictYAML

## From py-yaml12

py-yaml12 and yamluna both follow YAML 1.2 and both have a Rust core. py-yaml12 returns plain dictionaries and lists and writes new text, which makes it faster; yamluna keeps comments and layout and can register classes for both directions.

| py-yaml12 | yamluna |
| --- | --- |
| `parse_yaml(text)` | `yaml.load(text)` |
| `parse_yaml(text, multi=True)` | `yaml.load_all(text)` |
| `read_yaml('config.yaml')` | `yaml.load(Path('config.yaml'))` |
| `format_yaml(value)` | `yaml.dump(value)`, which ends with a line break |
| `write_yaml(value, 'config.yaml')` | `yaml.dump(value, Path('config.yaml'))`, without a leading `---` unless you set `yaml.explicit_start = True` |
| `parse_yaml(text, handlers={'!Point': make_point})` | `yaml.register_class(Point, from_yaml=...)` |
| `Yaml(value, tag='!Point')` | `TaggedScalar(value, tag='!Point')` for a scalar; `yaml_set_ctag('!Point')` on a `CommentedMap` or `CommentedSeq` |

A py-yaml12 handler receives the parsed value; a yamluna `from_yaml` receives the node, and `construct_mapping()` turns it into the same value:

```python
from dataclasses import dataclass
from yamluna import YAML


@dataclass
class Point:
    x: int
    y: int


yaml = YAML()
yaml.register_class(
    Point, from_yaml=lambda constructor, node: Point(**constructor.construct_mapping(node))
)
print(yaml.load('origin: !Point {x: 1, y: 2}\n'))
```

Output:

```text
{'origin': Point(x=1, y=2)}
```

Registering the class also covers saving: without a `to_yaml`, yamluna writes the object's fields under the tag, where py-yaml12 raises `TypeError` for an unknown type.

A few values load differently. yamluna reads `1_000` as the integer `1000` and `2026-09-28` as a timestamp; py-yaml12 leaves both as strings. Numbers written in a form Python would not reproduce, such as `1_000` or `0x1F`, load as `int` and `float` subclasses that remember it.

## From StrictYAML

StrictYAML reads a restricted YAML subset, types values through a schema you pass, and keeps comments on a round trip. yamluna reads all of YAML 1.2, types values by YAML's own rules, and has no schema validation.

| StrictYAML | yamluna |
| --- | --- |
| `load(text, schema)` | `yaml.load(text)`, then validate in your code |
| `document.data` | The loaded object itself |
| `document['key'] = value` | `config['key'] = value` |
| `document.as_yaml()` | `yaml.dump(config)` |

Values come back typed: `port: 8080` loads as the integer `8080`, and `debug: true` as `True`, where StrictYAML without a schema gives the strings `'8080'` and `'true'`. Flow style, tags, and anchors, which StrictYAML rejects, load normally.

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load('# service\nport: 8080  # public\ndebug: true\n')
assert config['port'] == 8080
assert config['debug'] is True
config['port'] += 1
print(yaml.dump(config), end='')
```

Output:

```yaml
# service
port: 8081  # public
debug: true
```

To give sections a type, as a StrictYAML `Map` schema does, tag them in the file and register a class for each tag; see [custom classes](../guide/custom-classes.md). Checks beyond types, such as ranges or required keys, belong in your code or in a validation library.

For measured round-trip and speed results, see [compare libraries](../comparison.md).
