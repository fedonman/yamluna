# YAML

```python
from yamluna import YAML

yaml = YAML()
print(yaml.dump(yaml.load('ready: true\n')), end='')
```

Output:

```yaml
ready: true
```

## Constructor

`YAML(*, output=None, registry=None)`

- `output`: destination for the `with` form below.
- `registry`: a `TagRegistry` to share. By default, each instance gets its own.

There is no `typ` argument. Set formatting options after creating the instance.

## Methods

| Method | Returns |
| --- | --- |
| `load(stream)` | One document as a Python object, or `None` for an empty document |
| `load_all(stream)` | A list of document objects |
| `dump(data, stream=None)` | YAML text, or `None` when writing to a destination |
| `dump_all(documents, stream=None)` | YAML text for an iterable of documents, or `None` when writing |
| `indent(mapping=None, sequence=None, offset=None)` | `None`; sets the supplied indentation options |
| `register_class(cls, *, tag=None, source=None, to_yaml=None, from_yaml=None)` | The registered class; also available as `register` |

Input accepts YAML text, bytes, bytearrays, paths, and readable streams. Output accepts paths and writable streams. Use `Path('config.yaml')` for a filename. See [read and write YAML](../guide/load-and-dump.md).

## Write several documents with a context manager

```python
from io import StringIO
from yamluna import YAML

output = StringIO()
with YAML(output=output) as yaml:
    yaml.dump({'name': 'web'})
    yaml.dump({'name': 'worker'})
print(output.getvalue(), end='')
```

Output:

```yaml
name: web
---
name: worker
```

Inside the block, `dump()` returns `None` and collects documents. A successful exit writes them together; an exception leaves the destination untouched. Pass the destination to `YAML(output=...)`, not to the individual `dump()` calls.

See [settings](../guide/settings.md) and [errors](errors.md).
