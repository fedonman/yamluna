# Switch from ruamel.yaml

For code that loads, edits, and saves YAML, start with the import:

```diff
-from ruamel.yaml import YAML
+from yamluna import YAML
```

Remove `typ=` from the constructor:

```diff
-yaml = YAML(typ='rt')
+yaml = YAML()
```

## A complete example

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

`load()`, `dump()`, comment methods, and the main container and scalar types keep familiar names. Import types such as `CommentedMap` and `LiteralScalarString` directly from `yamluna`. Exceptions also come from `yamluna`.

## Check these differences

| Your existing code | Change for yamluna |
| --- | --- |
| `YAML(typ='rt')` | Use `YAML()`; there is no `typ` argument |
| `yaml.dump(data, stream)` | Still works; omit `stream` to get a string back |
| `next(yaml.load_all(text))` | Use `iter()` first, or index the returned list |
| `indent(...)` to preserve loaded indentation | Remove it; existing layout is kept automatically |
| New quoted-string objects | Set `yaml.preserve_quotes = True` |
| Register on one instance, load on another | Register on both or pass a shared `TagRegistry` |
| `allow_duplicate_keys = True` | Expect a warning and the **last** value, rather than the first |
| `yaml.version = (1, 1)` before loading | Input needs a `%YAML 1.1` directive; `version` controls output |
| Manual repairs to `.ca` comment metadata | Review them; comment ownership differs |

Keep `indent()` for generating new documents in your chosen style. Applying it to existing lists can alter their layout; see [limitations](../guide/limitations.md).

## Custom classes

`register_class()` and `to_yaml` / `from_yaml` hooks are supported. Registrations are per instance, and output gains `%TAG` directives to distinguish class sources. Check that other consumers of your YAML accept those directives.

[Custom classes](../guide/custom-classes.md) covers registration and hooks.

## APIs to replace

| ruamel.yaml API | Approach |
| --- | --- |
| Module-level `load()` / `dump()` | Use a `YAML()` instance |
| `add_constructor()` / `add_representer()` | Use `register_class()` with hooks |
| `YAMLObject` | Register the class explicitly or use `@yaml.register` |
| `HexCapsInt` | Use `HexInt(value, caps=True)` |
| `DecimalInt` | Use `int` or `ScalarInt` |

yamluna focuses on round-trip editing. Other loader modes, arbitrary Python object tags, replaceable parser components, and low-level `scan()` / `compose()` / `serialize()` APIs are outside its API. Keep an existing loader for code that depends on those features.

## Verify the switch

Run your tests against representative files and review the saved YAML. Tests that expect ruamel.yaml's formatting may need new expected output; check the intended result before updating them. Pay particular attention to custom tags, comments around moved entries, and [known limitations](../guide/limitations.md).

[Behavior differences](differences.md) gives a short summary of output changes.
