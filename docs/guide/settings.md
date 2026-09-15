# Settings

Set options on a `YAML()` instance. Defaults preserve the layout of loaded documents; use formatting options when you create new content.

## Indent a new document

```python
from yamluna import YAML

yaml = YAML()
yaml.indent(mapping=2, sequence=4, offset=2)
print(yaml.dump({'ports': [80, 443]}), end='')
```

Output:

```yaml
ports:
  - 80
  - 443
```

For existing files, leave `indent()` unset to keep their indentation. Applying it to some loaded lists can change their layout or produce invalid output; see [limitations](limitations.md#indentation).

## Add document markers

```python
from yamluna import YAML

yaml = YAML()
yaml.explicit_start = True
yaml.explicit_end = True
print(yaml.dump({'ready': True}), end='')
```

Output:

```yaml
---
ready: true
...
```

For these two settings, `None` keeps the original markers, `True` adds them, and `False` removes them.

## Options you may need

| Setting | Default | Effect |
| --- | --- | --- |
| `preserve_quotes` | `None` | Set `True` for [editing or creating quoted strings](scalars.md) |
| `default_flow_style` | `False` | Set `True` to use inline collections such as `{ready: true}`, including loaded collections |
| `width` | `None` (80 columns) | Preferred wrapping width for newly formatted text; unchanged text keeps its wrapping |
| `explicit_start` / `explicit_end` | `None` | Control `---` / `...` markers |
| `line_break` | `None` | Set `'\n'`, `'\r\n'`, or `'\r'` explicitly |
| `encoding` | `'utf-8'` | Encoding for files and binary output streams |
| `allow_duplicate_keys` | `False` | Set `True` to warn and keep the last value of a repeated key |
| `version` | `None` | Set `(1, 2)` or `(1, 1)` to write a YAML version directive |

`version` controls output. Input declaring `%YAML 1.1` uses YAML 1.1 rules; input without a directive uses YAML 1.2 rules.

For Windows-style line endings, set `yaml.line_break = '\r\n'`. Automatic detection does not preserve them in every file.

For per-value formatting, use [string types](scalars.md) or `node.fa.set_flow_style()` / `node.fa.set_block_style()` on a collection.
