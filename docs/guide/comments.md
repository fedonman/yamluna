# Comments

Comments and blank lines are kept automatically when you load and save a file. Use the methods below when you want to change them.

## Add a comment

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load('replicas: 3\nports: [80, 443]\n')
config.yaml_set_start_comment('Production service')
config.yaml_add_eol_comment('extra capacity', 'replicas')
config.yaml_set_comment_before_after_key('ports', before='Public ports')
print(yaml.dump(config), end='')
```

Output:

```yaml
# Production service
replicas: 3 # extra capacity
# Public ports
ports: [80, 443]
```

Pass the comment text without `#`; yamluna adds it. To comment on a list item, pass its index instead of a mapping key.

## Remove a setting and its comment

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load("""image: app:1.4
# staging only
debug: true
# production capacity
replicas: 3
""")

del config['debug']
print(yaml.dump(config), end='')
```

Output:

```yaml
image: app:1.4
# production capacity
replicas: 3
```

## Rename a key

Use `rename()` to keep the key's position and comments:

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load('name: demo\nreplicas: 3  # production capacity\n')
config.rename('replicas', 'workers')
print(yaml.dump(config), end='')
```

Output:

```yaml
name: demo
workers: 3   # production capacity
```

`move_to_end(key)` reorders a mapping entry with its comments. List `reverse()` and `sort()` also carry comments with their items. [See a list example](../why.md).

## Useful methods

| Method | Use |
| --- | --- |
| `yaml_add_eol_comment(text, key, column=None)` | Add or replace a trailing comment |
| `yaml_set_comment_before_after_key(key, before=...)` | Add a comment above an entry |
| `yaml_set_comment_before_after_key(key, before='\n')` | Add a blank line above an entry |
| `yaml_set_start_comment(text)` | Set a heading for the document or collection |
| `insert(position, key, value, comment=...)` | Insert a mapping entry with a comment |

With no `column`, a new trailing comment aligns with nearby comments when possible.

Comments above the **first item** and insertions into commented lists have known edge cases. See [limitations](limitations.md#comments-and-list-insertions) before relying on those operations.
