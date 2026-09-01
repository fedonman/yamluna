# Errors

Every failure that comes out of `load` or `dump` says where in the document it happened.
Here is a config file with an unquoted value that has a colon in it:

```python
from yamluna import YAML, ScannerError

src = '''# service configuration
name: demo
replicas: 3
image: registry.example.com: latest
'''

try:
    YAML().load(src)
except ScannerError as exc:
    print(exc)
    print('---')
    print('problem      ', exc.problem)
    print('line, column ', exc.problem_mark.line, exc.problem_mark.column)
```

```text
mapping values are not allowed in this context
  in "<unicode string>", line 4, column 28:
    image: registry.example.com: latest
                               ^ (line: 4)
---
problem       mapping values are not allowed in this context
line, column  3 27
```

The caret is placed by character, not by byte, so a line with an accent or an emoji in it
still points at the right column. `line` and `column` on the mark are 0-based, matching
ruamel; the printed message adds one to each.

## The hierarchy

`YAMLError` is the base. `except YAMLError` catches everything in the first block below.
`YAMLStreamError` deliberately sits outside it, which is where ruamel puts it too, so it
survives an `except YAMLError` that was meant for document problems.

| Exception | Base | Raised when |
| --- | --- | --- |
| `YAMLError` | `Exception` | never directly; the class to catch |
| `MarkedYAMLError` | `YAMLError` | never directly; adds `problem`, `context`, `problem_mark`, `context_mark`, `note` |
| `ScannerError` | `MarkedYAMLError` | the source is not well-formed YAML. Every parse failure the Rust core reports arrives as this, including an alias naming an anchor nothing defines |
| `ComposerError` | `MarkedYAMLError` | `load` was given a stream holding more than one document. Use `load_all` |
| `ConstructorError` | `MarkedYAMLError` | a node cannot be turned into a Python object: an ambiguous or unresolvable tag, a `!!int` / `!!float` / `!!bool` / `!!timestamp` / `!!binary` whose text does not parse, or a registered class the loader cannot build |
| `DuplicateKeyError` | `MarkedYAMLError` | a mapping repeats a key and `allow_duplicate_keys` is off, or repeats `<<` at all |
| `RepresenterError` | `MarkedYAMLError` | dumping met an object with no representation and no registration |
| `EmitterError` | `MarkedYAMLError` | the Rust emitter cannot write the model. It carries a message and no mark |
| `ParserError` | `MarkedYAMLError` | never. Kept so an `except ParserError` ported from ruamel still imports |
| `YAMLStreamError` | `Exception` | the stream cannot be read from or written to, or the context-manager form was used wrongly |

And the warnings:

| Warning | Base | Issued when |
| --- | --- | --- |
| `DuplicateKeyFutureWarning` | `YAMLFutureWarning` | a mapping repeats a key and `allow_duplicate_keys` is on |
| `YAMLWarning`, `MarkedYAMLWarning`, `ReusedAnchorWarning` | `Warning` | never. Names kept so an import or a `filterwarnings` entry written against ruamel keeps working |

!!! warning "Two classes you will not see raised"

    `ParserError` and `EmitterError` exist for compatibility and for the Rust side to
    reach, but nothing in the test suite triggers either from the public API. The core
    reports every parse failure as a `ScannerError`, and the emitter falls back to double
    quoting rather than refusing a style it cannot write. Catch `MarkedYAMLError` or
    `YAMLError` if you want both covered.

The stream name printed in a mark is always `<unicode string>`. `YAML.load` does not pass
the file path down to the parser, so a `Path` you loaded does not appear in the message.
Print the path yourself alongside the error.

## Duplicate keys

A repeated key is an error by default, and the message gives both positions:

```python
from yamluna import YAML

YAML().load('a: 1\nb: 2\na: 3\n')
```

```text
yamluna.error.DuplicateKeyError: found duplicate key 'a' first at line 1, column 1, again at line 3, column 1
  in "<unicode string>", line 3, column 1
```

Set `allow_duplicate_keys = True` and you get a `DuplicateKeyFutureWarning` naming the same
two positions instead, every time, and the **last** value wins:

```python
import warnings
from yamluna import YAML

yaml = YAML()
yaml.allow_duplicate_keys = True

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    data = yaml.load('a: 1\nb: 2\na: 3\n')

for w in caught:
    print(w.category.__name__, w.message, sep=': ')
print('data:', dict(data))
print('dump:', repr(yaml.dump(data)))
```

```text
DuplicateKeyFutureWarning: duplicate key 'a' first at line 1, column 1, again at line 3, column 1; the last value wins
data: {'a': 3, 'b': 2}
dump: 'a: 3\nb: 2\n'
```

Note the dump. A `CommentedMap` is a `dict`, so two keys that compare equal are one entry,
and the second `a:` line is gone from the output. This is the one document shape whose
round trip is lost, with or without the flag, because the dump is written from the tree.
The default of raising is there so you find out rather than silently losing a line.

`ruamel.yaml` 0.19.1 with the same flag, measured:

```python
import io, warnings
import ruamel.yaml

r = ruamel.yaml.YAML()
r.allow_duplicate_keys = True
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    data = r.load('a: 1\nb: 2\na: 3\n')

print('warnings:', [str(w.message) for w in caught])
print('data:', dict(data))
buf = io.StringIO(); r.dump(data, buf)
print('dump:', repr(buf.getvalue()))
```

```text
warnings: []
data: {'a': 1, 'b': 2}
dump: 'a: 1\nb: 2\n'
```

ruamel keeps the first value and says nothing. A config file whose author appended a
corrected `a: 3` at the bottom loads with the stale value, and the next dump deletes the
correction.

A repeated merge key raises under both settings. Loading
`'base: &b {x: 1}\nm:\n  <<: *b\n  <<: *b\n'` with `allow_duplicate_keys = True` still
gives:

```text
yamluna.error.DuplicateKeyError: found duplicate merge key "<<".  Duplicate merge keys are never allowed, not even when allow_duplicate_keys is True
  in "<unicode string>", line 4, column 3
```

## Ambiguous tags

The registry never guesses which class a tag names. Register two classes called `Circuit`
from two libraries and a bare `!Circuit` in the document matches both:

```python
from yamluna import YAML
import libx, liby            # both define a class named Circuit

yaml = YAML()
yaml.register_class(libx.Circuit)
yaml.register_class(liby.Circuit)
yaml.load('c: !Circuit\n  gates: 3\n')
```

```text
yamluna.error.ConstructorError: ambiguous tag '!Circuit': 2 registered candidates: libx.Circuit (= tag:libx/Circuit), liby.Circuit (= tag:liby/Circuit); yamluna will not guess. Add a %TAG directive naming the source (e.g. '%TAG ! tag:libx/') or re-register with an explicit source= to disambiguate.
  in "<unicode string>", line 2, column 3
```

The fix is a `%TAG` directive in the document saying which source the primary handle stands
for. The document then loads, and the directive is written back out:

```python
src = '%TAG ! tag:libx/\n---\nc: !Circuit\n  gates: 3\n'
data = yaml.load(src)
print(type(data['c']).__module__, data['c'].gates)
print(repr(yaml.dump(data)))
```

```text
libx 3
'%TAG ! tag:libx/\n---\nc: !Circuit\n  gates: 3\n'
```

The other fix is to register only the class you mean, or to register the second one under a
different `tag=`, so nothing collides in the first place.

!!! note

    `source=` on its own does not settle a bare `!Circuit`. Registering the two classes as
    `source='libx'` and `source='liby'` still leaves two candidates with the tag name
    `Circuit`, and the same error is raised. `source=` decides what the tag is *written*
    as; the `%TAG` directive is what decides what a tag *read* means.

The sibling error is a tag that resolves into a source the registry does have classes in,
but names none of them. That is treated as a typo rather than as somebody else's tag:

```text
yamluna.error.ConstructorError: unresolved tag '!Ghost' (= 'tag:libx/Ghost'): no class is registered as 'Ghost' in source 'libx'
  in "<unicode string>", line 3, column 11
```

A tag in a source the registry has never heard of raises nothing and round-trips untouched,
which is what lets a `YAML()` with an empty registry load a file full of `!Circuit`. See
[Custom classes and tags](custom-classes.md).

## No compiled extension

Importing `yamluna` needs no Rust extension. The object model, the scalar types, the error
hierarchy and the registry are pure Python, so this works with nothing built:

```python
from yamluna import CommentedMap, HexInt

print(CommentedMap({'a': 1}))
print(HexInt(31).lexeme())
```

```text
{'a': 1}
0x1f
```

`load` and `dump` are the two calls that need it, and each raises `ImportError` naming the
build command:

```text
ImportError: the yamluna Rust extension (yamluna._yamluna) is not built. Build it with `maturin develop` from the repository root (or `pip install -e .`). Everything that does not touch the parser or the emitter works without it.
```

If you installed from PyPI you should not see this: wheels ship the extension. It means an
editable checkout with nothing compiled, or a platform with no wheel and a source install
that failed quietly. See [Install](../install.md).

## Streams

`YAMLStreamError` covers everything about *where* the document is going, not what is in it.
Its messages say what was expected:

```text
YAMLStreamError: cannot read YAML from int: expected str, bytes, a path, or an object with a .read() method
YAMLStreamError: cannot write YAML to int: expected a path, an object with a .write() method, or None to get the text back
YAMLStreamError: the context-manager form needs somewhere to write: YAML(output=path or stream). Without it, use yaml.dump(data) and keep the returned text.
YAMLStreamError: pass the stream to YAML(output=...) instead: inside the context-manager form every dump goes to that one stream
```

A reminder that a `str` handed to `load` is the document text and never a path. Pass a
`Path` for a file. Two settings raise plain `ValueError` rather than a YAML error, because
they are wrong arguments rather than wrong documents: `YAML(typ=...)` with anything but
`'rt'`, and a `line_break` that is not `'\n'`, `'\r\n'` or `'\r'`.

## See also

* [Errors](../api/errors.md) for `Mark`, `make_error` and the full class docstrings.
* [Settings](settings.md) for `allow_duplicate_keys` beside the rest.
* [Behaviour differences](../migrating/differences.md) for the measurement behind the
  duplicate-key divergence.
