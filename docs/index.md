# Edit YAML. Keep it human.

yamluna is a Python library for updating YAML files while keeping their comments, quotes, blank lines, and layout. Your script changes the values; people can still recognize the file.

## Try it

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

## Why yamluna?

**Smaller diffs.** Keep the formatting people chose, including indentation, anchors, and document markers.

**Comments that stay useful.** Comments follow the entries you reorder or delete. [See it in action](why.md), including the current edge cases.

**Fast updates.** The project's recorded benchmarks show a 1.8–6.3× faster load-and-save cycle than ruamel.yaml 0.19.1. [Compare libraries](comparison.md).

**Classes that work together.** Register classes from different packages, even when they share a name. Each `YAML()` has its own registrations.

## Get started

Install from PyPI with Python 3.11+:

```bash
python -m pip install yamluna
```

- [Install](install.md) — set up your environment.
- [Read and write YAML](guide/load-and-dump.md) — strings, files, and multiple documents.
- [Common tasks](guide/index.md) — comments, formatting, and custom classes.
- [Switch from ruamel.yaml](migrating/index.md) — the changes to make in your code.
- [Known limitations](guide/limitations.md) — what to check before adopting it.
