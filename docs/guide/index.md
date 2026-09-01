# Guide

`YAML()` is the whole entry point. One instance carries the emitter settings, the tag
registry and the records of the stream it loaded last, so a load and the dump that follows
it belong together:

```python
from pathlib import Path

from yamluna import YAML

yaml = YAML()
document = yaml.load(Path('config.yaml'))
```

| page | what it covers |
|---|---|
| [Loading and dumping](load-and-dump.md) | `load`, `load_all`, `dump`, `dump_all`, where a dump can go, and why a `str` argument is a document rather than a path |
| [Comments and blank lines](comments.md) | reading and writing `.ca`, and how trivia stays with the node it describes across an insert, a delete or a reorder |
| [Scalar styles and types](scalars.md) | literal, folded and quoted strings, the `int` and `float` subclasses that remember their spelling, and `TimeStamp` |
| [Anchors, aliases and merge keys](anchors.md) | `&name`, `*name` and `<<`, what survives a round trip, and what `Anchor.always_dump` is for |
| [Custom classes and tags](custom-classes.md) | `register_class`, the `to_yaml` and `from_yaml` hooks, and the `%TAG` directives that keep two libraries' classes apart |
| [Settings](settings.md) | every setting on the `YAML` object, its default, and what `None` means |
| [Errors](errors.md) | the exception hierarchy, what each error means, and reading a `Mark` |
| [Examples](examples.md) | the three runnable scripts in `examples/`, each with its real output |

Coming from `ruamel.yaml`, read [The port](../migrating/index.md) first: the API is the
same one, and that page is the shorter route to the handful of places where it is not.
