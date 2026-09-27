# String and number types

Use these types when you want to choose how a new value appears in YAML. Loaded values keep their original spelling automatically.

| Type or helper | Example | YAML style |
| --- | --- | --- |
| `LiteralScalarString` | `LiteralScalarString('one\ntwo\n')` | A `\|` block that keeps line breaks |
| `FoldedScalarString` | `FoldedScalarString('one paragraph\n')` | A `>` block |
| `SingleQuotedScalarString` | `SingleQuotedScalarString('hello')` | `'hello'` |
| `DoubleQuotedScalarString` | `DoubleQuotedScalarString('hello')` | `"hello"` |
| `PlainScalarString` | `PlainScalarString('hello')` | Unquoted, when safe for the value |
| `ScalarInt` | `ScalarInt(7, width=3)` | `007` |
| `HexInt` | `HexInt(31, caps=True)` | `0x1F` |
| `OctalInt` | `OctalInt(493)` | `0o755` |
| `BinaryInt` | `BinaryInt(10)` | `0b1010` |
| `ScalarFloat` | `ScalarFloat(1.5)` | A formatted floating-point number |
| `ScalarBoolean` | Loaded from a non-default boolean spelling | Preserves spellings such as `TRUE` |
| `TimeStamp` | Loaded from a timestamp | Preserves timestamp formatting |
| `preserve_literal(text)` | `preserve_literal('one\ntwo\n')` | Normalize line endings and use a literal block |
| `walk_tree(data)` | `walk_tree(config)` | Convert multiline strings to literal blocks in place |

`PreservedScalarString` is an alias for `LiteralScalarString`.

Set `yaml.preserve_quotes = True` when using quoted-string classes:

```python
from yamluna import YAML, DoubleQuotedScalarString

yaml = YAML()
yaml.preserve_quotes = True
print(yaml.dump({'version': DoubleQuotedScalarString('1.0')}), end='')
```

Output:

```yaml
version: "1.0"
```

`ScalarBoolean` behaves like a boolean in conditions, but `value is True` is false for these wrapper objects. Use `bool(value)` or an equality comparison.

See [strings and numbers](../guide/scalars.md) for editing examples.
