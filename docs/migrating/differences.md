# Behavior differences

These are the main changes to expect when switching from ruamel.yaml 0.19.1 to yamluna 0.1.0. Start with the [migration guide](index.md) for code changes.

## Saved files look closer to the input

yamluna keeps each loaded collection's indentation, unused anchors, document markers, and original number spelling. For example:

```python
from yamluna import YAML

source = '---\ncount: +12\nports:\n  - 80\n...\n'
yaml = YAML()
print(yaml.dump(yaml.load(source)), end='')
```

Output:

```yaml
---
count: +12
ports:
  - 80
...
```

With ruamel.yaml's default `YAML()`, this example saves without `---` and `...`, with `count: 12`, and with the list dash at column one. yamluna keeps all four details without extra settings.

## Changes that can affect your code

| Area | yamluna behavior |
| --- | --- |
| Comments | Move with reordered entries; attached comments are removed with deleted entries |
| Key renaming | `mapping.rename(old, new)` keeps position and comments |
| Class registration | Per `YAML()` instance; class sources are written using `%TAG` |
| Ambiguous tags | Raise an error instead of choosing a class by registration order |
| Quoting | Unchanged quotes survive by default; enable `preserve_quotes` for quoted-string edits and new quoted values |
| Allowed duplicate keys | Warn and keep the last value |
| Document markers | `None` keeps them; `True` adds them; `False` removes them |
| Multiple documents | `load_all()` returns a list rather than a generator |
| Source positions | An unavailable `.lc` position returns `None` |

Comment movement still has exceptions around first items and list insertions. Read [known limitations](../guide/limitations.md) and check files affected by those operations.

For side-by-side output and the corpus results, see [the comparison](../comparison.md).
