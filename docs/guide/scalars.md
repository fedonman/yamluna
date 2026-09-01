# Scalar styles and types

YAML has five ways to write a string, and a value written one way must not come back
written another. A load, an edit somewhere else and a dump leaves every scalar you did not
touch spelled exactly as the author spelled it, quotes, block header, digit separators and
all.

## The five styles

```yaml
plain: hello world
single: 'it''s fine'
double: "tab\there"
literal: |
  line one
  line two
folded: >
  one long
  paragraph
```

Plain is unquoted. Single quotes take no escapes except `''` for a quote of their own.
Double quotes take the full set of backslash escapes. `|` keeps the line breaks in the
block below it; `>` folds them into spaces.

Load that document and dump it back and you get the same bytes:

```python
from yamluna import YAML

src = '''plain: hello world
single: 'it''s fine'
double: "tab\\there"
literal: |
  line one
  line two
folded: >
  one long
  paragraph
'''

yaml = YAML()
data = yaml.load(src)
for key, value in data.items():
    print(f'{key:8} {type(value).__name__:22} {value!r}')
print('identical:', yaml.dump(data) == src)
```

```text
plain    str                    'hello world'
single   str                    "it's fine"
double   str                    'tab\there'
literal  LiteralScalarString    'line one\nline two\n'
folded   FoldedScalarString     'one long paragraph\n'
identical: True
```

Two things to read off that. The quoted scalars came back as a plain `str`, and the round
trip kept their quotes anyway. The block scalars kept a class, because their cooked value
(`'one long paragraph\n'`) does not describe the text the source used, so the class is
where the source text is held.

## The types

Assign one of these to force a style or a spelling on the next dump. The two quoted classes
only quote when `preserve_quotes` is on; see [below](#preserve_quotes).

| Type | Use it when | Writes |
| --- | --- | --- |
| `LiteralScalarString` | a multi-line string whose breaks matter: a script, a key, a log excerpt | a literal block |
| `FoldedScalarString` | long prose you want wrapped in the file but joined in Python | a folded block |
| `PreservedScalarString` | ruamel's old name for `LiteralScalarString`; the same class object | a literal block |
| `SingleQuotedScalarString` | you want quotes and the value has no escapes worth writing | `'it''s'` |
| `DoubleQuotedScalarString` | you want quotes and the value has tabs, newlines or control characters | `"a\tb"` |
| `PlainScalarString` | you want no quotes, and accept them anyway if the value needs them | `hello` |
| `ScalarInt` | a decimal integer with leading zeros, digit separators or an explicit `+` | `007` |
| `HexInt` | base sixteen, with `caps=True` for `0x1F` over `0x1f` | `0x1F` |
| `OctalInt` | base eight, such as a file mode | `0o755` |
| `BinaryInt` | base two, such as a bit mask you want readable | `0b1010` |
| `ScalarFloat` | any float; every float a document loads is one of these | `1.5` |
| `ScalarBoolean` | a boolean spelled `yes`, `on`, `True` or `TRUE` rather than `true` | `on` |
| `TimeStamp` | a date or a datetime, keeping the source's separator and zone spelling | `2001-12-14 21:59:43` |

All thirteen in one script:

```python
from yamluna import (
    YAML, CommentedMap,
    LiteralScalarString, FoldedScalarString, PreservedScalarString,
    SingleQuotedScalarString, DoubleQuotedScalarString, PlainScalarString,
    ScalarInt, HexInt, OctalInt, BinaryInt, ScalarFloat, ScalarBoolean, TimeStamp,
)

yaml = YAML()
yaml.preserve_quotes = True          # needed for the two quoted classes

data = CommentedMap()
data['literal'] = LiteralScalarString('one\ntwo\n')
data['folded'] = FoldedScalarString('one long paragraph\n')
data['preserved'] = PreservedScalarString('same class as literal\n')
data['single'] = SingleQuotedScalarString("it's")
data['double'] = DoubleQuotedScalarString('a\tb')
data['plain'] = PlainScalarString('hello')
data['count'] = ScalarInt(7, width=3)
data['mask'] = HexInt(31, caps=True)
data['mode'] = OctalInt(0o755)
data['flags'] = BinaryInt(0b1010)
data['ratio'] = ScalarFloat(1.5)
data['debug'] = ScalarBoolean(True, lexeme='on')
data['when'] = TimeStamp(2001, 12, 14, 21, 59, 43)

print(yaml.dump(data))
```

```yaml
literal: |
  one
  two
folded: >
  one long paragraph
preserved: |
  same class as literal
single: 'it''s'
double: "a\tb"
plain: hello
count: 007
mask: 0x1F
mode: 0o755
flags: 0b1010
ratio: 1.5
debug: on
when: 2001-12-14 21:59:43
```

`PlainScalarString` is a request, not a guarantee. A value that would not read back as
itself is quoted regardless:

```python
from yamluna import YAML, CommentedMap, PlainScalarString

data = CommentedMap()
data['greeting'] = PlainScalarString('hello')
data['mapping-ish'] = PlainScalarString('a: b')
data['number-ish'] = PlainScalarString('12')
data['empty'] = PlainScalarString('')
print(YAML().dump(data))
```

```yaml
greeting: hello
mapping-ish: 'a: b'
number-ish: '12'
empty: ''
```

`bool` cannot be subclassed, so `ScalarBoolean` subclasses `int`, as ruamel's does. It
works in an `if` and compares equal to `True`, but `x is True` is false; test with `==` or
`bool(x)`.

## preserve_quotes

`preserve_quotes` gates one thing: whether a quoted scalar you *replace* is written back
quoted. It has nothing to do with the round trip, which keeps quoting either way.

```python
from yamluna import YAML

src = "name: 'demo'\nnote: \"x\"\n"

for flag in (False, True):
    yaml = YAML()
    yaml.preserve_quotes = flag

    untouched = yaml.load(src)
    edited = yaml.load(src)
    edited['name'] = edited['name'].replace('demo', 'other')

    print(f'preserve_quotes={flag}')
    print('  untouched:', repr(yaml.dump(untouched)))
    print('  edited:   ', repr(yaml.dump(edited)), type(edited['name']).__name__)
```

```text
preserve_quotes=False
  untouched: 'name: \'demo\'\nnote: "x"\n'
  edited:    'name: other\nnote: "x"\n' str
preserve_quotes=True
  untouched: 'name: \'demo\'\nnote: "x"\n'
  edited:    'name: \'other\'\nnote: "x"\n' SingleQuotedScalarString
```

Both loads reproduce the file. The difference is what `load` hands you: with the flag off a
quoted scalar is a bare `str`, and once you have replaced its text there is nothing left
saying it was quoted. With the flag on it is a `SingleQuotedScalarString`, and
`str.replace` on one of those returns another one, so the style survives the edit.

The same flag decides whether a quoted class you construct yourself is honoured at all:

```python
from yamluna import YAML, CommentedMap, SingleQuotedScalarString, DoubleQuotedScalarString

for flag in (False, True):
    yaml = YAML()
    yaml.preserve_quotes = flag
    data = CommentedMap()
    data['who'] = SingleQuotedScalarString('demo')
    data['what'] = DoubleQuotedScalarString('demo')
    print(f'preserve_quotes={flag}: {yaml.dump(data)!r}')
```

```text
preserve_quotes=False: 'who: demo\nwhat: demo\n'
preserve_quotes=True: 'who: \'demo\'\nwhat: "demo"\n'
```

With the flag off the emitter picks the cheapest style the value survives, and `demo`
survives plain. Turn the flag on whenever you build quoted strings by hand.

!!! note

    The block styles behave differently from the quoted ones here. Assign a bare `str`
    into a slot that held a `|` or `>` block and the block style stays, because the block
    is the layout of the entry rather than a property of the string:
    `'note: |\n  one\n  two\n'` with `data['note'] = 'short'` dumps as
    `'note: |-\n  short\n'`.

## The lexeme rule

A scalar you did not change is written back as its source text, character for character.
The class it came back as carries that text and `lexeme()` returns it. So the spellings
that mean the same value but do not look the same all survive:

```python
from yamluna import YAML

src = 'a: 1_000.5\nb: +12\nc: 0X1F\nd: 007\ne: -0x1F\nf: 0o755\ng: TRUE\nh: 2002-12-14\n'
print(YAML().dump(YAML().load(src)))
```

The same document loaded and dumped through `ruamel.yaml` 0.19.1, side by side:

=== "yamluna"

    ```yaml
    a: 1_000.5
    b: +12
    c: 0X1F
    d: 007
    e: -0x1F
    f: 0o755
    g: TRUE
    h: 2002-12-14
    ```

=== "ruamel.yaml 0.19.1"

    ```yaml
    a: 01000.5
    b: 12
    c: 0X1F
    d: 007
    e: !!int '0x-1F'
    f: 0o755
    g: true
    h: 2002-12-14
    ```

ruamel re-spells four of the eight. `1_000.5` loses its separator and gains a leading zero
from a width field that was measured before the separator was dropped. `+12` loses its
sign. `-0x1F` comes out as `!!int '0x-1F'`, which is not an integer in any other YAML
implementation. `TRUE` becomes `true`. Each of these is written up with its measurement in
[Behaviour differences](../migrating/differences.md).

Loading only gives you a scalar class when a builtin would write something else back. `7`
is an `int`, `true` is a `bool`, `007` is a `ScalarInt` and `0X1F` is a `HexInt`. Floats
are the exception: every float in a document loads as a `ScalarFloat`, because no double
remembers whether it was written `1.5`, `1.50` or `15e-1`.

## When you do change one

Changing the value drops the lexeme, since the source text no longer describes what you
hold. The formatting fields survive, so the new value is written in the old shape.
In-place arithmetic keeps them; ordinary arithmetic returns a plain `int`, as it does in
ruamel:

```python
from yamluna import YAML

yaml = YAML()
src = 'mask: 0x0f\ncount: 1_000\n'

inplace = yaml.load(src)
inplace['mask'] += 1
inplace['count'] += 1
print('in place:', repr(yaml.dump(inplace)))

ordinary = yaml.load(src)
ordinary['mask'] = ordinary['mask'] + 1
print('ordinary:', repr(yaml.dump(ordinary)), type(ordinary['mask']).__name__)
```

```text
in place: 'mask: 0x10\ncount: 1_001\n'
ordinary: 'mask: 16\ncount: 1_000\n' int
```

`str.replace` on a `ScalarString` does the same for text: it returns the same class with no
lexeme, so the style holds and the layout is worked out afresh.

## Turning strings into blocks

`walk_tree` rewrites the strings in a loaded tree in place. Its default map turns every
string containing a newline into a `LiteralScalarString`, which is the usual reason to
reach for it:

```python
from yamluna import YAML, CommentedMap, walk_tree

data = CommentedMap({'note': 'one\ntwo\n', 'name': 'demo'})
yaml = YAML()
print('before:', repr(yaml.dump(data)))
walk_tree(data)
print('after: ', repr(yaml.dump(data)))
```

```text
before: 'note: "one\\ntwo\\n"\nname: demo\n'
after:  'note: |\n  one\n  two\nname: demo\n'
```

Pass `map=` for other rules, for example
`walk_tree(data, map={'\n': preserve_literal, ':': SingleQuotedScalarString})`.
`preserve_literal` is the single-value form: it normalises `\r\n` and `\r` to `\n` and
returns a `LiteralScalarString`.

## See also

* [Settings](settings.md) for `preserve_quotes` beside the other emitter settings.
* [Anchors, aliases and merge keys](anchors.md) for the `anchor=` keyword every scalar
  class takes.
* [Scalar types](../api/scalars.md) for the full signatures.
