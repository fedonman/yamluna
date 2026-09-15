# Compare Python YAML libraries

**Choose yamluna when your Python code edits YAML that people also maintain.** It combines detailed formatting preservation, comments that follow edits, and faster read-and-write cycles than ruamel.yaml in the project's benchmarks.

## At a glance

| Your priority | Library to consider |
| --- | --- |
| Edit files while preserving comments and layout | **yamluna** |
| An established round-trip library with a broader API | [ruamel.yaml](https://yaml.dev/doc/ruamel.yaml/) |
| Load YAML values into Python with a widely used library | [PyYAML](https://pyyaml.org/wiki/PyYAMLDocumentation) |
| YAML 1.2 parsing and generation with a Rust implementation | [py-yaml12](https://github.com/posit-dev/py-yaml12) |
| Validate a restricted YAML format against a schema | [StrictYAML](https://hitchdev.com/strictyaml/) |

## See the difference

Remove a setting and the comment explaining it should leave too. This example was checked with yamluna 0.1.0, ruamel.yaml 0.19.1, and PyYAML 6.0.3.

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

With ruamel.yaml's default `YAML()`, the same edit leaves the staging comment above `replicas`:

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

PyYAML discards both comments, including the one on the surviving `image` setting:

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

The project tests 40 files containing comments, quoting, anchors, document markers, and different layouts. Each file is loaded and saved without edits, then compared byte for byte.

| Library | Files reproduced exactly |
| --- | ---: |
| **yamluna 0.1.0** | **40 / 40** |
| ruamel.yaml 0.19.1 | 3 / 40 |

Both use `YAML()` and `preserve_quotes = True`, with other settings left at their defaults. A separate duplicate-key file is excluded because a Python dictionary cannot retain both entries. These results describe this corpus, not every YAML file. Run `python tests/differential.py` in a checkout with the development dependencies installed to reproduce them.

## Speed

The project's recorded **release-build** benchmarks measure a complete load and save. The figures below compare yamluna 0.1.0 with ruamel.yaml 0.19.1:

| File | Size | yamluna speedup |
| --- | ---: | ---: |
| Typical configuration | 1 KiB | 5.0× |
| Deeply nested data | 249 KiB | 1.8× |
| Comment-heavy configuration | 150 KiB | 2.9× |
| Strings and numbers in varied formats | 37 KiB | 6.3× |

Measured on Linux, CPython 3.13.12, and an Intel Core i7-1280P. Results depend on your files and machine. The [benchmark script](https://github.com/fedonman/yamluna/blob/main/bench/bench.py) generates the inputs and reports the median of five batches.

PyYAML's C loader was faster for loading values in the same recorded benchmarks. It does not preserve comments for later editing. yamluna's advantage is the combination of speed and preservation.

## Before switching

yamluna is **alpha** and requires Python 3.11+. Read the short [limitations page](guide/limitations.md), then follow the [ruamel.yaml migration guide](migrating/index.md) or [installation guide](install.md).
