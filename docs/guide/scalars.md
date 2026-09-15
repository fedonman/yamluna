# Strings and numbers

Unchanged values keep their spelling: quotes, `1_000`, `+12`, and `0xFF` survive a load and save. These examples show how to control values you edit or create.

## Keep quotes when editing

Set `preserve_quotes = True` before loading, then use `replace()` to edit a quoted string while keeping its style:

```python
from yamluna import YAML

yaml = YAML()
yaml.preserve_quotes = True
config = yaml.load('image: "app:1.4"\n')
config['image'] = config['image'].replace('1.4', '1.5')
print(yaml.dump(config), end='')
```

Output:

```yaml
image: "app:1.5"
```

For a new value, use `DoubleQuotedScalarString('text')` or `SingleQuotedScalarString('text')` with the same setting enabled.

## Write multiline text

`LiteralScalarString` writes a `|` block that keeps line breaks:

```python
from yamluna import YAML, LiteralScalarString

yaml = YAML()
config = {'script': LiteralScalarString('echo hello\necho done\n')}
print(yaml.dump(config), end='')
```

Output:

```yaml
script: |
  echo hello
  echo done
```

Use `FoldedScalarString` for a `>` block whose lines read as a paragraph. `walk_tree(config)` converts all strings containing newlines into literal blocks.

## Keep number formatting

Use in-place arithmetic, such as `+=`, to keep a loaded number's format:

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load('mask: 0x0f\ncount: 1_000\n')
config['mask'] += 1
config['count'] += 1
print(yaml.dump(config), end='')
```

Output:

```yaml
mask: 0x10
count: 1_001
```

Ordinary arithmetic, such as `config['mask'] = config['mask'] + 1`, returns a plain integer and writes `16`.

## YAML versions

yamluna uses YAML 1.2 by default: `yes`, `no`, `on`, and `off` are strings; `true` and `false` are booleans. A document beginning with `%YAML 1.1` and `---` uses the older rules, where `yes` and `on` are also booleans.

[Scalar type reference](../api/scalars.md) lists the available formatting classes.
