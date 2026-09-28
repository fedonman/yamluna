# YAML for Python, out of the box

yamluna reads and writes YAML without setup. A plain `YAML()` keeps everything you did not touch exactly as it was written, and a registered class goes into the file as a tagged object and loads back as itself.

## Edit a file

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

Comments, blank lines, quotes, indentation, anchors, and document markers all survive the load and save. There is no `typ=` to choose and no `indent()` call to match the file's style.

## Store Python objects

```python
from dataclasses import dataclass
from yamluna import YAML

yaml = YAML()


@yaml.register_class
@dataclass
class Server:
    host: str
    port: int = 80


text = yaml.dump({'primary': Server('web-1', 8080)})
print(text, end='')
print(yaml.load(text)['primary'])
```

Output:

```text
%TAG ! tag:__main__/
---
primary: !Server
  host: web-1
  port: 8080
Server(host='web-1', port=8080)
```

The decorator is the whole setup for your own class. For a class you cannot change, such as numpy's `ndarray` or `Decimal`, call `register_class` with a `to_yaml` and a `from_yaml` function; [custom classes](guide/custom-classes.md#register-a-class-you-do-not-own) shows both.

## What you get

**Byte-identical round trips.** In the project's 40-file corpus, yamluna reproduces every file exactly; ruamel.yaml reproduces 3, StrictYAML 2, and PyYAML and py-yaml12 none. [Compare libraries](comparison.md).

**Objects that know where they came from.** Tags are namespaced by package, so two libraries can each register a `Server`. Each `YAML()` has its own registrations.

**Comments that follow your edits.** Reorder a list or delete a setting and its comments go with it. [Why yamluna](why.md) has examples and the current edge cases.

**Fast.** A load-and-save cycle is 1.7 to 6.0 times faster than ruamel.yaml. [Compare libraries](comparison.md) has the numbers for PyYAML, py-yaml12, and StrictYAML too.

## Next steps

Install from PyPI with Python 3.11+:

```bash
python -m pip install yamluna
```

- [Install](install.md): set up your environment.
- [Read and write YAML](guide/load-and-dump.md): strings, files, and multiple documents.
- [Examples](guide/examples.md): recipes for configs, dataclasses, numpy arrays, and decimals.
- [User guide](guide/index.md): every task, from custom classes to comments and formatting.
- [Switch to yamluna](migrating/index.md): the changes to make coming from PyYAML, ruamel.yaml, py-yaml12, or StrictYAML.
