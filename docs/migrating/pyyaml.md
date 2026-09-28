# Switch from PyYAML

This page also covers [oyaml](https://github.com/wimglenn/oyaml), which is PyYAML with ordered mappings: yamluna keeps key order too.

## Replace the calls

Create one `YAML()` and use its methods in place of PyYAML's module functions:

```diff
-import yaml
+from yamluna import YAML
+
+yaml = YAML()

-config = yaml.safe_load(text)
+config = yaml.load(text)
 config['replicas'] = 5
-text = yaml.safe_dump(config, sort_keys=False)
+text = yaml.dump(config)
```

A complete example:

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load('replicas: 3  # production\nimage: app:1.4\n')
config['replicas'] = 5
print(yaml.dump(config), end='')
```

Output:

```yaml
replicas: 5  # production
image: app:1.4
```

The comment and the key order survive, which PyYAML's `safe_dump` would not keep.

| PyYAML | yamluna |
| --- | --- |
| `yaml.safe_load(stream)` | `yaml.load(stream)` |
| `yaml.safe_load_all(stream)` | `yaml.load_all(stream)`, which returns a list |
| `yaml.safe_dump(data)` | `yaml.dump(data)` |
| `yaml.safe_dump(data, stream)` | `yaml.dump(data, stream)` |
| `yaml.safe_dump_all(docs, stream)` | `yaml.dump_all(docs, stream)` |
| `yaml.load(stream, Loader=yaml.CSafeLoader)` | `yaml.load(stream)`; there is no loader to choose |
| `sort_keys=True`, PyYAML's default | Keys stay in their order; sort the dict first if you want them sorted |
| `default_flow_style=True` | `yaml.default_flow_style = True` |
| `indent=4` | `yaml.indent(mapping=4)` |
| `width=100` | `yaml.width = 100` |
| `explicit_start=True` | `yaml.explicit_start = True` |
| `allow_unicode=True` | The default; non-ASCII text is written as is |
| `except yaml.YAMLError` | `except yamluna.YAMLError` |

`load()` takes YAML text, bytes, a `Path`, or an open file. A plain string is always YAML text, so pass `Path('config.yaml')` to read a file by name.

There is no unsafe mode. yamluna constructs only the classes you register, so `safe_load` and `load` become the same call.

## Values that load differently

PyYAML follows YAML 1.1. yamluna follows YAML 1.2 unless a document starts with `%YAML 1.1`, and a few spellings change meaning:

| In the file | PyYAML | yamluna |
| --- | --- | --- |
| `yes`, `no`, `on`, `off` | `True` / `False` | The strings `'yes'`, `'no'`, `'on'`, `'off'` |
| `0755` | `493`, an octal number | `755`, or `493` under `%YAML 1.1` |
| `0o755` | The string `'0o755'` | `493` |
| `1e3` | The string `'1e3'` | `1000.0` |

`true`, `false`, `null`, and `~` load the same way in both. Dates and timestamps load as `datetime` values in both, except that a date alone is a `datetime.date` in PyYAML and a `TimeStamp`, a `datetime` subclass, in yamluna. For files that rely on YAML 1.1 rules, add `%YAML 1.1` and `---` at the top: yamluna then reads `yes` and `on` as booleans and `0755` as an octal number, and writes them back with the same spelling.

Loaded values are subclasses of the built-in types: mappings are `dict`, lists are `list`, and numbers written in a form Python would not reproduce, such as `0x1F`, keep that spelling in `int` and `float` subclasses. `isinstance` checks, arithmetic, and `json.dumps` work unchanged; `type(value) is dict` does not. One exception: `true` and `false` load as plain `bool`, but other spellings such as `True` load as `ScalarBoolean`, an `int` subclass, because `bool` cannot be subclassed. `json.dumps` writes it as `1`, so convert with `bool(value)` first.

## Files that PyYAML also reads

yamluna writes the string `yes` unquoted, because in YAML 1.2 it is a string. A YAML 1.1 reader such as PyYAML then reads it as `True`. If PyYAML or another YAML 1.1 tool reads your output, set `yaml.version = (1, 1)`:

```python
from yamluna import YAML

yaml = YAML()
yaml.version = (1, 1)
print(yaml.dump({'confirm': 'yes', 'mode': 'on'}), end='')
```

Output:

```yaml
%YAML 1.1
---
confirm: 'yes'
mode: 'on'
```

## Custom classes

`add_representer()` and `add_constructor()` become one `register_class()` call. The functions you already have work unchanged, because the representer and constructor methods they call have the same names:

```python
from dataclasses import dataclass
from yamluna import YAML


@dataclass
class Point:
    x: float
    y: float


def point_representer(dumper, point):
    return dumper.represent_mapping('!Point', {'x': point.x, 'y': point.y})


def point_constructor(loader, node):
    return Point(**loader.construct_mapping(node))


yaml = YAML()
yaml.register_class(Point, to_yaml=point_representer, from_yaml=point_constructor)
print(yaml.load('origin: !Point {x: 1.5, y: 2.5}\n'))
```

Output:

```text
{'origin': Point(x=ScalarFloat(1.5), y=ScalarFloat(2.5))}
```

For a class you own, `@yaml.register_class` alone is enough and replaces both functions; [custom classes](../guide/custom-classes.md) shows it. It also replaces `yaml.YAMLObject`: drop that base class, keep the `yaml_tag` attribute, and decorate the class.

When yamluna saves a registered class, a `%TAG` line names the package it came from, so `!Point` in the file means `tag:myapp/Point`. PyYAML reads that directive too, so register its constructor under the full tag: `yaml.add_constructor('tag:myapp/Point', ...)`.

Files written by `yaml.dump()` with Python object tags such as `!!python/object:myapp.Point` load as ordinary mappings; yamluna never constructs arbitrary objects from tags. Register the class and replace those tags with `!Point`.

## Verify the switch

Load your representative files and check the values your code reads, especially booleans, octal numbers, and anything the table above lists. Then save one and review the diff: unchanged parts should be byte-identical, and changed parts should keep the file's style.

For the other differences in behavior and speed, see [compare libraries](../comparison.md).
