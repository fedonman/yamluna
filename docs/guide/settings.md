# Settings

Settings are plain attributes on a `YAML` instance, set after you construct it:

```python
yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
```

They are ruamel's settings, with ruamel's names and ruamel's defaults, and one difference
that runs through all of them: **`None` means "keep what the source had"** rather than
"apply the built-in default". A node that came out of a document and that you did not
change reproduces its own layout, so most of these settings only decide how nodes *you*
made are written.

| setting | default | what it does |
|---|---|---|
| `preserve_quotes` | `None` | Emit a quoted-string object you constructed with its quotes. `None` and `False` let the emitter re-decide; a scalar from a file keeps its quotes either way. |
| `default_flow_style` | `False` | `True` writes every collection in flow style, loaded ones included. `False` gives each collection the style its `.fa` asks for, and block style when it asks for nothing. |
| `width` | `None` | Column at which a scalar the emitter lays out is folded. `None` means 80. A scalar that still remembers its source line is never re-wrapped. |
| `explicit_start` | `None` | Write `---` on every document. `None` keeps the marker each document had. |
| `explicit_end` | `None` | Write `...` on every document. `None` keeps the marker each document had. |
| `allow_duplicate_keys` | `False` | `False` raises `DuplicateKeyError` on a repeated key. `True` warns and the last value wins. |
| `line_break` | `None` | The break to write: `'\n'`, `'\r\n'` or `'\r'`. `None` takes it from the documents. Any other value raises `ValueError` on dump. |
| `encoding` | `'utf-8'` | The codec used whenever the text goes out as bytes: a path destination, or a stream that rejects `str`. |
| `map_indent` | `None` | Columns a nested mapping is indented by. `None` means 2. |
| `sequence_indent` | `None` | Columns a sequence's items are indented by, from the key holding them. `None` means 2. |
| `sequence_dash_offset` | `None` | Columns the `-` is indented by, inside `sequence_indent`. `None` means 0. |
| `version` | `None` | The `%YAML` version to write, as `(major, minor)` or `'1.2'`. `None` re-emits whatever directive the source had. |
| `registry` | a fresh `TagRegistry` | This instance's [tag registry](custom-classes.md). Never shared unless you pass one to `YAML()`. |

`YAML` uses `__slots__`, so a misspelled setting is an error at the assignment rather than
an option that silently does nothing:

```pycon
>>> yaml.preserve_qoutes = True
Traceback (most recent call last):
  ...
AttributeError: 'YAML' object has no attribute 'preserve_qoutes' and no __dict__ for setting new attributes. Did you mean: 'preserve_quotes'?
```

## Indentation

`indent()` sets the three indent attributes with ruamel's signature, and leaves out
whatever you do not pass:

```python
yaml.indent(mapping=2, sequence=4, offset=2)
```

Under ruamel that call is the standard incantation for stopping a loaded file's sequences
being re-indented to column 0. Here it is not needed for that: a loaded node reproduces its
own indentation, including in a file that mixes two styles. What it does affect is nodes
you built. For `CommentedMap({'top': CommentedMap({'items': CommentedSeq([1, 2])})})`:

=== "defaults"

    ```yaml
    top:
      items:
      - 1
      - 2
    ```

=== "`indent(mapping=4)`"

    ```yaml
    top:
        items:
        - 1
        - 2
    ```

=== "`indent(sequence=4, offset=2)`"

    ```yaml
    top:
      items:
        - 1
        - 2
    ```

## Document markers

`explicit_start` and `explicit_end` are three-valued, and `None` is not a synonym for
`False`. Loading `a: 1\n` and loading `---\na: 1\n...\n`, then dumping each:

| | `None` | `True` | `False` |
|---|---|---|---|
| source `a: 1` | `a: 1` | `---`<br>`a: 1` | `a: 1` |
| source `---` `a: 1` `...` | `---`<br>`a: 1`<br>`...` | `---`<br>`a: 1`<br>`...` | `a: 1` |

So `None` round-trips, `True` forces the marker on, and `False` forces it off. Setting
`version` also forces `explicit_start`, because a `%YAML` directive requires the `---`
after it.

## Line breaks

`None` takes the break from the documents. That works from the lexemes, so a CRLF file with
a multi-line scalar in it comes back CRLF:

```pycon
>>> yaml.dump(yaml.load('a: |\r\n  x\r\n  y\r\n'))
'a: |\r\n  x\r\n  y\r\n'
```

A break *between* two lines is not something the model records, so a CRLF file with no
multi-line scalar comes back with `'\n'`:

```pycon
>>> yaml.dump(yaml.load('a: 1\r\nb: 2\r\n'))
'a: 1\nb: 2\n'
```

Set `line_break = '\r\n'` for those. Anything outside the three legal breaks raises when
you dump, not when you assign:

```pycon
>>> yaml.line_break = '\n\n'
>>> yaml.dump({'a': 1})
Traceback (most recent call last):
  ...
ValueError: EmitOptions.line_break must be one of '\n', '\r\n', '\r', not "\n\n"
```

## Encoding

`encoding` applies only where the text is turned into bytes, which is a path destination
and a stream that refuses `str`. A text stream and the string returned by `dump(data)` are
unaffected by it. Setting `encoding = 'utf-16'` and dumping `name: café`:

| destination | result |
|---|---|
| `Path('out.yaml')` | `b'\xff\xfen\x00a\x00m\x00e\x00:\x00 \x00c\x00a\x00f\x00\xe9\x00\n\x00'` |
| `io.BytesIO()` | the same bytes |
| `io.StringIO()` | `'name: café\n'` |
| `dump(data)` | `'name: café\n'` |

Non-ASCII is written as itself, never as `\uXXXX`, unless the source escaped it.

## Duplicate keys

The default raises, naming both positions:

```text
DuplicateKeyError: found duplicate key 'a' first at line 1, column 1, again at line 3, column 1
  in "<unicode string>", line 3, column 1
```

`allow_duplicate_keys = True` warns instead, with `DuplicateKeyFutureWarning`, and the
**last** value wins, so `a: 1`, `b: 2`, `a: 3` loads as `{'a': 3, 'b': 2}`. ruamel keeps the
first and says nothing. A repeated `<<` merge key is an error under both settings.

!!! note "The duplicate itself cannot survive"

    `CommentedMap` is a `dict`, so two entries with equal keys are one entry however you
    set this. The setting decides whether the load fails or warns, not whether the second
    entry is kept.

## Version

Setting `version` writes the `%YAML` directive and the `---` that has to follow it:

```pycon
>>> yaml.version = (1, 2)          # or '1.2'
>>> yaml.dump(yaml.load('a: 1\n'))
'%YAML 1.2\n---\na: 1\n'
```

It also picks the resolution rules used when a value *you* created is spelled. Under
YAML 1.1 the plain strings `yes`, `no`, `on` and `off` are booleans, so a Python string
spelled that way has to be quoted to stay a string:

```pycon
>>> YAML().dump({'a': 'yes'})
'a: yes\n'
>>> forced = YAML(); forced.version = (1, 1)
>>> forced.dump({'a': 'yes'})
"%YAML 1.1\n---\na: 'yes'\n"
```

Leaving it `None` re-emits whatever directive the source carried, and spells new values by
YAML 1.2. An assignment that is neither a pair of integers nor `'major.minor'` raises
`ValueError` at the assignment.

## What is not here

The emitter knobs ruamel needs to re-decide the layout of nodes it could not
reproduce, `canonical`, `default_style`, `allow_unicode`,
`sort_base_mapping_type_on_output`, `block_seq_indent`, `top_level_colon_align` and the
rest, are absent. Reproduction removes the problem most of them solve, and the ones that
remain are per-node style instead: the scalar-string subclasses, and
`.fa.set_flow_style()` / `.fa.set_block_style()`. [Migrating](../migrating/index.md#absent-and-what-to-use-instead)
has the replacement for each.
