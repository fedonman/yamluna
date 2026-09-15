# yamluna

**Edit YAML. Keep it human.**

yamluna lets you update YAML from Python while keeping the comments, quotes, blank lines, and layout that make the file readable. Use it for configuration files, deployment manifests, and any YAML that people edit too.

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load("""# Production service
image: app:1.4   # approved release
replicas: 3

ports:
  - 80          # HTTP
  - 443         # HTTPS
""")

config['replicas'] = 5
config['ports'].append(8080)
print(yaml.dump(config), end='')
```

Output:

```yaml
# Production service
image: app:1.4   # approved release
replicas: 5

ports:
  - 80          # HTTP
  - 443         # HTTPS
  - 8080
```

Two edits. The rest of the file stays familiar.

## Why choose yamluna?

- **Readable diffs.** Keep comments, spacing, key order, anchors, and document markers when updating a file.
- **Comments that follow your edits.** Reorder a list or remove a setting and its comments go with it. [See an example and current limits.][why]
- **Faster editing.** The project's recorded release-build benchmarks show a **1.8–6.3× faster load-and-save cycle** than ruamel.yaml 0.19.1. [See the measurements.][comparison]
- **Custom classes without name clashes.** Classes from different packages can share a name. Each `YAML()` keeps its own registrations.
- **A familiar Python API.** Work with dictionaries and lists. Coming from ruamel.yaml? Start by changing the import and removing `typ=`. [Migration guide.][migration]

PyYAML is useful for reading values. yamluna is built for editing the file itself. Compared with ruamel.yaml, it preserves more of the original text: **40/40** files unchanged in the project's round-trip corpus, versus **3/40** with ruamel.yaml 0.19.1 under the tested settings. [Comparison and test method.][comparison]

## Install

Install from PyPI with **Python 3.11+**:

```bash
python -m pip install yamluna
```

[Installation guide][install] · [Known limitations][limitations]

## Edit a file

```python
from pathlib import Path
from yamluna import YAML

yaml = YAML()
config = yaml.load(Path('config.yaml'))
config['replicas'] = 5
yaml.dump(config, Path('config.yaml'))
```

Pass a `Path` to read or write a file. A string passed to `load()` is YAML text; `dump(config)` returns YAML text.

## Learn more

[Documentation][docs] · [Common tasks][guide] · [API reference][api] · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/fedonman/yamluna/issues)

Python 3.11+ · YAML 1.2, with support for documents declaring YAML 1.1 · MIT or Apache-2.0

[docs]: https://fedonman.github.io/yamluna/
[why]: https://fedonman.github.io/yamluna/why/
[comparison]: https://fedonman.github.io/yamluna/comparison/
[migration]: https://fedonman.github.io/yamluna/migrating/
[install]: https://fedonman.github.io/yamluna/install/
[limitations]: https://fedonman.github.io/yamluna/guide/limitations/
[guide]: https://fedonman.github.io/yamluna/guide/
[api]: https://fedonman.github.io/yamluna/api/
