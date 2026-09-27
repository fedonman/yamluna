# Anchors and merges

YAML uses `&name` to name a value and `*name` to reuse it. yamluna keeps those names and references when you save.

## Edit a shared value

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load("""defaults: &defaults
  timeout: 30
web: *defaults
""")

config['defaults']['timeout'] = 60
print(config['web']['timeout'])
print(yaml.dump(config), end='')
```

Output:

```text
60
defaults: &defaults
  timeout: 60
web: *defaults
```

`config['web']` and `config['defaults']` are the same Python object. Editing either changes the shared value. An anchor in the source is kept even if nothing references it.

## Override a default

The merge key `<<` supplies defaults to a mapping. Assign a value on that mapping to give it an explicit override:

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load("""defaults: &defaults
  timeout: 30
web:
  <<: *defaults
  workers: 2
""")

config['web']['timeout'] = 60
print(yaml.dump(config), end='')
```

Output:

```yaml
defaults: &defaults
  timeout: 30
web:
  <<: *defaults
  workers: 2
  timeout: 60
```

The merge stays a merge; the defaults are not expanded into the saved mapping. `config['web'].non_merged_items()` gives only the explicitly stored entries.

## Name a new anchor

Call `node.yaml_set_anchor('defaults', always_dump=True)` on a `CommentedMap` or `CommentedSeq`. Reuse the same object elsewhere to emit an alias to it.

An alias must refer to an anchor in the same document. An undefined alias raises an error. [Handle errors](errors.md).
