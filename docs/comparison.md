# Compare Python YAML libraries

**Choose yamluna when your Python code reads and writes YAML and you want the file, and your objects, back as they were.** If you only read values and never write the file back, a library that discards formatting will be faster.

## At a glance

| Your priority | Library to consider |
| --- | --- |
| Edit files while keeping comments and layout | **yamluna** |
| Save and load your own classes, or types such as `ndarray`, with namespaced tags | **yamluna** |
| Load YAML values as fast as possible, formatting not needed | [py-yaml12](https://github.com/posit-dev/py-yaml12), or [PyYAML](https://pyyaml.org/wiki/PyYAMLDocumentation) with libyaml |
| The most widely used library, YAML 1.1 | [PyYAML](https://pyyaml.org/wiki/PyYAMLDocumentation) |
| An established round-trip library with a broader API | [ruamel.yaml](https://yaml.dev/doc/ruamel.yaml/) |
| Validate a restricted YAML subset against a schema | [StrictYAML](https://hitchdev.com/strictyaml/) |

## Features

Checked with yamluna 0.1.0, ruamel.yaml 0.19.1, PyYAML 6.0.3, py-yaml12 0.2.0, and StrictYAML 1.7.3.

| | yamluna | ruamel.yaml | PyYAML | py-yaml12 | StrictYAML |
| --- | --- | --- | --- | --- | --- |
| YAML version | 1.2, and 1.1 when declared | 1.2 | 1.1 | 1.2 | A restricted subset |
| Comments kept on save | Yes, and they move with edited entries | Yes; they stay at their position | No | No | Yes; they stay at their position |
| Quotes, indentation, number spelling kept | Yes | Partly | No | No | Partly |
| Unchanged corpus files written back exactly | 40 / 40 | 3 / 40 | 0 / 40 | 0 / 40 | 2 / 40 |
| Your own classes | `@yaml.register_class` | `register_class` | `add_constructor` and `add_representer`, or `YAMLObject` | Load handlers only; write with `Yaml(value, tag)` | No; schemas type the values |
| Types you do not own | `register_class` with `to_yaml` and `from_yaml` | `add_representer` and `add_constructor` | `add_representer` and `add_constructor` | Load handlers only | No |
| Where registrations live | Each `YAML()` instance | Shared by every `YAML()` in the process | The `Loader` and `Dumper` classes | Each call | Not applicable |
| Classes with the same name from two packages | Kept apart with `%TAG` | The later registration wins | Kept apart only if you choose distinct tags | Not applicable | Not applicable |
| Constructs arbitrary Python objects from tags | Never | Only with `typ='unsafe'`, which is deprecated | With `UnsafeLoader` or `yaml.load(..., Loader=yaml.Loader)` | Never | Never |
| Anchors and aliases | Kept, with their names | Kept, with their names | Loaded; saved as `&id001` | Loaded; expanded on save | Rejected |
| Implementation | Rust core, Python API | Python, optional C for the non-round-trip loaders | Python, optional libyaml C | Rust | Python, on a vendored ruamel.yaml |

## See the difference

Remove a setting, and the comment explaining it should leave too:

```python
from yamluna import YAML

source = """image: app:1.4  # approved release
# staging only
debug: true
replicas: 3
"""

yaml = YAML()
config = yaml.load(source)
del config['debug']
print(yaml.dump(config), end='')
```

**yamluna output:**

```yaml
image: app:1.4  # approved release
replicas: 3
```

ruamel.yaml's default `YAML()` and StrictYAML both leave the staging comment behind, above `replicas`:

```python
import io
from ruamel.yaml import YAML

yaml = YAML()
config = yaml.load(source)
del config['debug']
output = io.StringIO()
yaml.dump(config, output)
print(output.getvalue(), end='')
```

```yaml
image: app:1.4  # approved release
# staging only
replicas: 3
```

PyYAML and py-yaml12 discard both comments, including the one on the surviving `image` setting:

```python
import yaml

config = yaml.safe_load(source)
del config['debug']
print(yaml.safe_dump(config, sort_keys=False), end='')
```

```yaml
image: app:1.4
replicas: 3
```

## How much of the file survives?

The project's round-trip corpus has 40 files covering comments, quoting, anchors, document markers, and different layouts. Each file is loaded and saved without edits, then compared byte for byte.

| Library | Files reproduced exactly |
| --- | ---: |
| **yamluna 0.1.0** | **40 / 40** |
| ruamel.yaml 0.19.1 | 3 / 40 |
| StrictYAML 1.7.3 | 2 / 40 |
| PyYAML 6.0.3 | 0 / 40 |
| py-yaml12 0.2.0 | 0 / 40 |

yamluna and ruamel.yaml use `YAML()` with `preserve_quotes = True`. PyYAML uses `safe_load_all` and `safe_dump_all` with `sort_keys=False`, and StrictYAML its round-trip `dirty_load(...).as_yaml()`, which rejects the files that use tags, anchors, or several documents. A separate duplicate-key file is excluded because a Python dictionary cannot hold both entries. These results describe this corpus, not every YAML file.

## Speed

Each figure is one complete load and save of a generated input, in milliseconds; lower is faster.

| Library | Config, 1 KiB | Nested data, 249 KiB | Comment-heavy, 150 KiB | Varied scalars, 37 KiB |
| --- | ---: | ---: | ---: | ---: |
| **yamluna 0.1.0** | **0.61** | **428** | **36** | **12** |
| ruamel.yaml 0.19.1 | 2.73 | 729 | 109 | 74 |
| StrictYAML 1.7.3 | 4.03 | 1585 | 344 | 1197 |
| PyYAML 6.0.3, pure Python | 1.19 | 348 | 65 | 38 |
| PyYAML 6.0.3, libyaml C | 0.17 | 51 | 5.5 | 5.1 |
| py-yaml12 0.2.0 | 0.02 | 9 | 1.0 | 0.8 |

Among the libraries that keep comments, yamluna is 1.7 to 6.0 times faster than ruamel.yaml and faster than StrictYAML on every input. It is also faster than pure-Python PyYAML except on the deeply nested document. PyYAML with libyaml and py-yaml12 are faster still, because they build plain dictionaries and lists and write new text rather than keeping the original's. If you never save the file back, or don't mind it being reformatted, one of those is the quicker choice.

Measured on Linux (WSL2), CPython 3.13.11, and an Intel Core i5-13400F, with the settings described above. Results depend on your files and machine. [`bench/libraries.py`](https://github.com/fedonman/yamluna/blob/main/bench/libraries.py) reproduces both tables and reports the median of five batches.

## Before switching

yamluna is **alpha** and requires Python 3.11+. Read the short [limitations page](guide/limitations.md), then follow the [migration guide](migrating/index.md) for the library you use now, or the [installation guide](install.md).
