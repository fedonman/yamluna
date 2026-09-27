# Containers

`load()` returns these types for YAML collections. They support normal Python dictionary and list operations while carrying comments and formatting.

| Type | Use |
| --- | --- |
| `CommentedMap` | A mapping; subclass of `dict` |
| `CommentedSeq` | A sequence; subclass of `list` |
| `CommentedSet` | A YAML `!!set` |
| `CommentedKeySeq` / `CommentedKeyMap` | Hashable collections used as mapping keys |
| `TaggedScalar` | A value with an unregistered tag |

## Create a mapping with a comment

```python
from yamluna import YAML, CommentedMap

config = CommentedMap({'port': 8080})
config.yaml_add_eol_comment('HTTP', 'port')
print(YAML().dump(config), end='')
```

Output:

```yaml
port: 8080 # HTTP
```

Use plain `dict` and `list` objects when you don't need to add comments or choose formatting before saving.

## Mapping methods

| Method | Effect |
| --- | --- |
| `insert(pos, key, value, comment=None)` | Insert an entry at a position |
| `rename(old, new)` | Rename a key, keeping its position and comments |
| `move_to_end(key, last=True)` | Move an entry to the end, or the start with `last=False` |
| `non_merged_items()` | Iterate over explicitly stored entries, excluding inherited defaults |

## Comments and formatting

| Method or attribute | Use |
| --- | --- |
| `yaml_add_eol_comment(text, key, column=None)` | Add a comment after a value |
| `yaml_set_comment_before_after_key(key, before=..., after=...)` | Add comments around an entry |
| `yaml_set_start_comment(text)` | Set a collection heading |
| `yaml_set_anchor(name, always_dump=False)` | Name an anchor; set `always_dump=True` to emit an unreferenced new anchor |
| `.anchor.value` | Read the anchor name |
| `.tag.value` | Read the resolved tag |
| `.fa.set_flow_style()` / `.fa.set_block_style()` | Choose inline or block layout |
| `.lc.line` / `.lc.col` | Read the original position, starting at zero |
| `.lc.key(key)` / `.lc.value(key)` / `.lc.item(index)` | Read an entry's original position, or `None` if unavailable |
| `.ca` | Access comment metadata; prefer the methods above for edits |

Positions describe the loaded file, not the document after edits. See [comments](../guide/comments.md), [anchors](../guide/anchors.md), and [known limitations](../guide/limitations.md).
