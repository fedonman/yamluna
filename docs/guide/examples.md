# Examples

## Update an image version

```python
from yamluna import YAML

yaml = YAML()
yaml.preserve_quotes = True
config = yaml.load('image: "app:1.4"  # approved release\nreplicas: 3\n')
config['image'] = config['image'].replace('1.4', '1.5')
print(yaml.dump(config), end='')
```

Output:

```yaml
image: "app:1.5"  # approved release
replicas: 3
```

## Generate a commented config

```python
from yamluna import YAML, CommentedMap

yaml = YAML()
config = CommentedMap({'host': 'localhost', 'port': 8080})
config.yaml_set_start_comment('Local development')
config.yaml_add_eol_comment('HTTP port', 'port')
print(yaml.dump(config), end='')
```

Output:

```yaml
# Local development
host: localhost
port: 8080 # HTTP port
```

## More recipes

- [Edit a file](load-and-dump.md#edit-a-file)
- [Read multiple documents](load-and-dump.md#multiple-documents)
- [Remove a setting and its comment](comments.md#remove-a-setting-and-its-comment)
- [Write a multiline script](scalars.md#write-multiline-text)
- [Override a shared default](anchors.md#override-a-default)
- [Save and load a Python class](custom-classes.md#save-and-load-a-class)

The repository also includes runnable scripts: [round trip](https://github.com/fedonman/yamluna/blob/main/examples/round_trip.py), [comments](https://github.com/fedonman/yamluna/blob/main/examples/comments.py), and [custom classes](https://github.com/fedonman/yamluna/blob/main/examples/custom_classes.py).
