# Loading and dumping

The whole workflow is four lines. You make a `YAML`, load a document, change it, and write
it back:

```python
from pathlib import Path

from yamluna import YAML

yaml = YAML()
config = yaml.load(Path('config.yaml'))

config['replicas'] = 5
config['ports'].append(8080)

yaml.dump(config, Path('config.yaml'))
```

`YAML()` takes keyword arguments only, and all three are optional: `typ`, which accepts
`'rt'` and nothing else, `output`, which is [the context-manager
destination](#the-context-manager-form), and `registry`, which is
[the tag registry](custom-classes.md). Everything else is an attribute you set afterwards,
and [Settings](settings.md) lists them.

What comes back from `load` is a `CommentedMap`, a `CommentedSeq`, a scalar, or `None`.
`CommentedMap` is a `dict` subclass and `CommentedSeq` is a `list` subclass, so
`isinstance(config, dict)`, `json.dumps`, `copy.deepcopy` and `==` all work on the result
without a conversion step.

## What you can load from

| you pass | `load` reads |
|---|---|
| `str` | the string, as the document text |
| `bytes`, `bytearray` | the bytes, decoded from their byte-order mark, or as UTF-8 when there is none |
| `os.PathLike` | the file at that path, in binary, decoded the same way |
| an object with `.read()` | whatever it returns, decoded if it is bytes |
| anything else | nothing: `YAMLStreamError` |

### A `str` is a document, not a path

This is ruamel's rule and yamluna keeps it, because too much code in the wild passes YAML
text as a string for a divergence here to be worth it. The consequence is that a path you
forgot to wrap loads as a one-scalar document instead of failing:

```pycon
>>> yaml.load('config.yaml')
'config.yaml'
```

Pass a `Path` for a file, and take the habit everywhere: the failure mode is a valid load
of the wrong thing, not an exception.

## Where a dump goes

`dump` and `dump_all` take the destination as their last argument:

| you pass | `dump` does | it returns |
|---|---|---|
| nothing, or `None` | writes nowhere | the emitted text, as a `str` |
| `os.PathLike` | writes the bytes to that path, in [`encoding`](settings.md) | `None` |
| an object with `.write()` | writes text to it, or bytes if it rejects text | `None` |
| anything else | nothing: `YAMLStreamError` | |

A binary stream is detected by trying the text write first, which is safe because
`BytesIO.write(str)` raises before writing anything. So the same call covers both:

```python
import io

buffer = io.BytesIO()
yaml.dump(document, buffer)     # bytes, in yaml.encoding
```

To keep the text instead, leave the stream out:

```pycon
>>> yaml.dump(yaml.load('a: 1  # keep me\n'))
'a: 1  # keep me\n'
```

## Multi-document streams

`load` handles one document. Give it a stream with a second `---` in it and it raises
rather than quietly returning the first:

```pycon
>>> yaml.load('---\na: 1\n---\nb: 2\n')
Traceback (most recent call last):
  ...
yamluna.error.ComposerError: expected a single document in the stream
but found another document
```

`load_all` returns a list, one root per document, in order. `dump_all` takes an iterable
back:

```python
yaml = YAML()

stream = """\
# defaults
---
name: alpha
---
name: beta   # second
...
"""

documents = yaml.load_all(stream)
print(documents)
print(yaml.dump_all(documents) == stream)
```

```text
[{'name': 'alpha'}, {'name': 'beta'}]
True
```

The list is built eagerly rather than yielded. The parser reads the whole stream in one FFI
call anyway, so a generator would only delay the errors.

## Documents with no content

A stream that is nothing but comments, or a bare `---` with nothing under it, has no root
node, and it loads as `None`:

```pycon
>>> yaml = YAML()
>>> yaml.load('# nothing but comments\n') is None
True
```

`None` is a singleton, so it cannot carry the comments, the `---` and the directives that
document had. The `YAML` instance holds them instead, keyed by position in the stream it
loaded last, and `dump_all` hands each set back to the `None` at the same position:

```pycon
>>> yaml.dump(yaml.load('# nothing but comments\n'))
'# nothing but comments\n'
```

Two things follow from the association being positional, and both are worth knowing before
you hit them.

A `None` with no record behind it is a null document, because there is nothing else it
could be:

```pycon
>>> YAML().dump(None)
'null\n'
```

And a load replaces the whole table, so reordering or resizing a stream between the load
and the dump can hand a record to the wrong empty document:

```pycon
>>> yaml = YAML()
>>> documents = yaml.load_all('---\n# first\n---\nkept: 1\n---\n# third\n')
>>> documents
[None, {'kept': 1}, None]
>>> yaml.dump_all([documents[1], documents[0], documents[2]])
'---\nkept: 1\n--- null\n---\n# third\n'
```

`# first` moved to position 1, where the table holds no record, so it was written as
`--- null` and its comment is gone. For the load, edit and dump cycle this library is for,
where the documents come back in the order they went out, the association is exact.

!!! warning "Test `is None`, not truthiness"

    An empty document and a document holding an empty mapping both look false. `load`
    returns `None` for the first and a `CommentedMap` for the second, and only the second
    can be edited.

## The context-manager form

`YAML(output=...)` plus a `with` block collects every document dumped inside it and writes
them as one multi-document stream when the block ends:

```python
from pathlib import Path

from yamluna import YAML

with YAML(output=Path('out.yaml')) as yaml:
    yaml.dump({'name': 'alpha'})
    yaml.dump({'name': 'beta'})

print(repr(Path('out.yaml').read_text()))
```

```text
'name: alpha\n---\nname: beta\n'
```

Three rules come with it:

* The block needs somewhere to write. `with YAML() as yaml:` raises `YAMLStreamError`,
  because there would be nowhere for the collected documents to go.
* `dump` inside the block takes no stream of its own. Passing one raises, rather than
  writing to two places.
* A block that ends by raising writes nothing. The exception propagates and the file is
  not touched.
