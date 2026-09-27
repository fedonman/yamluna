# Read and write YAML

## Start with text

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load('replicas: 3  # production\n')
config['replicas'] = 5
print(yaml.dump(config), end='')
```

Output:

```yaml
replicas: 5  # production
```

`load()` returns a dictionary, list, or value. The mapping and list types also remember the YAML formatting. An empty document returns `None`.

## Edit a file

Given this `config.yaml`:

```yaml
replicas: 3  # production
```

```python
from pathlib import Path
from yamluna import YAML

yaml = YAML()
path = Path('config.yaml')
config = yaml.load(path)
config['replicas'] = 5
yaml.dump(config, path)
```

The file now contains:

```yaml
replicas: 5  # production
```

**Pass a `Path` for a filename.** `yaml.load('config.yaml')` reads the literal text `config.yaml`, so it returns a string instead of opening the file.

## Choose an input or output

| Call | Result |
| --- | --- |
| `yaml.load(text)` | Read YAML text |
| `yaml.load(Path('config.yaml'))` | Read a file |
| `yaml.load(file)` | Read an open text or binary stream |
| `yaml.dump(config)` | Return YAML as a string |
| `yaml.dump(config, Path('config.yaml'))` | Write a file; return `None` |
| `yaml.dump(config, file)` | Write to an open stream; return `None` |

Bytes are accepted too: UTF-8 is the default, and a byte-order mark identifies UTF-16 or UTF-32 input. File output defaults to UTF-8.

## Multiple documents

Use `load_all()` and `dump_all()` for files containing documents separated by `---`:

```python
from yamluna import YAML

yaml = YAML()
documents = yaml.load_all('---\nname: web\n---\nname: worker\n')
documents[1]['name'] = 'jobs'
print(yaml.dump_all(documents), end='')
```

Output:

```yaml
---
name: web
---
name: jobs
```

`load_all()` returns a list. `load()` raises `ComposerError` if it finds more than one document.

Use the same `YAML()` instance for a load-edit-save cycle. For empty or comment-only documents, save before loading another stream on that instance so their comments and markers stay associated with them.

Next: [add comments](comments.md), [keep quotes when editing](scalars.md), or [handle errors](errors.md).
