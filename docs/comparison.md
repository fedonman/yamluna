# How it compares

Three Python libraries read YAML. Two of them can write a file back. Which one you want
depends on how much of the file has to survive the trip, and on what you are willing to pay
for that.

Everything in the table below was measured in this repository's virtualenv against
`ruamel.yaml` 0.19.1 and `PyYAML` 6.0.3 on CPython 3.13.12. The runs are underneath it.

## The matrix

| | yamluna 0.1.0 | ruamel.yaml 0.19.1, `typ='rt'` | PyYAML 6.0.3 |
|---|---|---|---|
| **Comments** | kept, attached to the node they describe | kept on a plain round trip of a block collection; destroyed inside a flow collection; a file of only comments comes back as `null\n...\n` | discarded |
| **Blank lines** | kept, as a run with a count | kept between entries; a leading blank line is lost and a whitespace-only line is emptied | discarded |
| **Quoting style** | each scalar's source lexeme is re-emitted | kept with `preserve_quotes = True` | re-decided by the emitter: `"1.0"` comes back `'1.0'` |
| **Anchors and aliases** | kept by name; an alias stays an alias | kept when referenced twice or more; an anchor referenced once is dropped | aliasing survives as object identity, the names do not: `&b` comes back `&id001` |
| **Directives** | `%YAML`, `%TAG` and reserved directives re-emitted verbatim, as are `---` and `...` | `%YAML` and `%TAG` kept; a reserved directive dropped; `---` and `...` dropped when no directive forces them | none read, none written |
| **Tags, custom classes** | `register_class` keyed on the fully qualified class path, per `YAML()` instance, namespaced into the document with `%TAG` | `register_class` keyed on `'!' + cls.__name__`, in a process-global table | `add_constructor` / `add_representer`, process-global; an unregistered tag raises `ConstructorError` under `safe_load` |
| **Duplicate keys** | `DuplicateKeyError`; with `allow_duplicate_keys = True`, a warning naming both source positions and the last value wins | `DuplicateKeyError`; with `allow_duplicate_keys = True`, silence and the first value wins | accepted in silence, the last value wins |
| **Speed** | 1.4x to 8.9x ruamel on load and dump, release build | the baseline | faster than both with libyaml, and keeps none of the rows above |
| **Thread scaling** | 3.89x at 8 threads for the parse, 2.45x for a whole `load` | 0.98x | 0.77x with `CSafeLoader`, 1.06x with `SafeLoader` |
| **Implementation** | a Rust extension (`cp311-abi3`) under a pure-Python object layer | pure Python: `YAML(typ='rt').parser` is `ruamel.yaml.parser.RoundTripParser` | pure Python, with an optional libyaml binding |
| **YAML version** | 1.2, and a `%YAML 1.1` directive in the document switches scalar resolution | 1.2, and the same | 1.1 only: `yes` loads as `True`, `0777` as `511` |
| **Maintenance** | 0.1.0, alpha; the [changelog](changelog.md) names the known gaps | maintained upstream | maintained upstream |

## What survives a round trip

`tests/corpus/` is 41 hand-written files, one YAML concern each. The measure is load it,
change nothing, dump it, compare bytes. Reproduce with `.venv/bin/python tests/differential.py`:

| | byte-identical round trips |
|---|---|
| `ruamel.yaml` 0.19.1 | 3 of 40 |
| yamluna 0.1.0 | 40 of 40 |

The denominator is 40 because `key-duplicate` is scored on behaviour rather than bytes: no
`dict`-backed API can write two equal keys back.

PyYAML is not in this business at all. Over all 41 files,
`yaml.safe_dump(yaml.safe_load(text))` reproduces none: 28 come back different and 13 raise.

Here is where ruamel's 37 go. Each row is one document loaded and dumped again with
`preserve_quotes = True` and nothing else set:

```python
import io

import ruamel.yaml
import yamluna

CASES = {
    'comment in a flow collection': 'a: {x: 1,  # about x\n   y: 2}\n',
    'a file of only comments':      '# nothing but this\n',
    'an anchor referenced once':    'base: &b\n  k: 1\n',
    'sequence indentation':         'ports:\n  - 80\n',
    '--- and ...':                  '---\na: 1\n...\n',
    'a reserved directive':         '%FOO bar\n---\na: 1\n',
    'an explicit key':              '? gamma  # c\n: 3\n',
    'a leading blank line':         '\nkey: 1\n',
    'an underscored float':         'ratio: 1_000.5\n',
    'a signed integer':             'signed: +12\n',
    'a negative hex integer':       'mask: -0x1F\n',
    'a block-scalar header':        'a: |-2\n   text\n',
}


def round_trip(yaml, src):
    if isinstance(yaml, ruamel.yaml.YAML):
        buf = io.StringIO()
        yaml.dump(yaml.load(src), buf)
        return buf.getvalue()
    return yaml.dump(yaml.load(src), None)


yl, rm = yamluna.YAML(), ruamel.yaml.YAML()
yl.preserve_quotes = rm.preserve_quotes = True

print(f'{"case":<30}{"yamluna":<12}ruamel.yaml 0.19.1')
for name, src in CASES.items():
    a, b = round_trip(yl, src), round_trip(rm, src)
    print(f'{name:<30}{"identical" if a == src else repr(a):<12}'
          f'{"identical" if b == src else repr(b)}')
```

```text
case                          yamluna     ruamel.yaml 0.19.1
comment in a flow collection  identical   'a: {x: 1, y: 2}\n'
a file of only comments       identical   'null\n...\n'
an anchor referenced once     identical   'base:\n  k: 1\n'
sequence indentation          identical   'ports:\n- 80\n'
--- and ...                   identical   'a: 1\n'
a reserved directive          identical   'a: 1\n'
an explicit key               identical   'gamma    # c\n: 3\n'
a leading blank line          identical   'key: 1\n'
an underscored float          identical   'ratio: 01000.5\n'
a signed integer              identical   'signed: 12\n'
a negative hex integer        identical   "mask: !!int '0x-1F'\n"
a block-scalar header         identical   'a: |2-\n   text\n'
```

Two of those are worse than losing bytes. `? gamma  # c` comes back as YAML that ruamel
cannot re-read, and `mask: !!int '0x-1F'` is an integer literal no YAML implementation
accepts. The rest of the list, including what happens to comments once you start *mutating*
a document, is on [Behaviour differences](migrating/differences.md), with the raw runs behind
it on [Measured ruamel behaviour](internals/ruamel-behaviour.md).

The two tag registries are the other place the libraries genuinely disagree, rather than one
being less careful than the other: see [Custom classes and tags](guide/custom-classes.md).

## Speed

From `bench/bench.py`, which generates its four inputs, gives both libraries the same two
lines of configuration (`YAML()`, `preserve_quotes = True`) and reports the median of five
`timeit` batches. Measured on a 12th Gen Intel Core i7-1280P (20 hardware threads), Linux,
CPython 3.13.12, yamluna 0.1.0 against `ruamel.yaml` 0.19.1, release build. The numbers are
how many times faster yamluna is:

| document | size | load | dump | load+dump |
|---|---:|---:|---:|---:|
| `config`, a hand-written config file | 1 KiB | 8.9x | 3.5x | 5.0x |
| `nested`, a deep tree, many collections | 249 KiB | 1.4x | 3.2x | 1.8x |
| `comments`, three lines in four are trivia | 150 KiB | 3.2x | 2.3x | 2.9x |
| `scalars`, a flat run of every scalar style | 37 KiB | 8.1x | 4.8x | 6.3x |

The 1.4x is the number worth knowing. `nested` is almost entirely collection structure, so
loading it is dominated by building one Python object per node, and Rust does not help with
that. `bench.py` splits a yamluna round trip into three layers and finds 22 to 81% of the
time going into the Python objects. On a document that is mostly text rather than structure
the margin is five to nine times.

Parsing happens inside `py.detach`, so loads across threads overlap. 32 loads of the 249 KiB
`nested` document spread over N threads, each count measured twice and the faster taken:

| workload | 1 | 2 | 4 | 8 | speedup at 8 |
|---|---:|---:|---:|---:|---:|
| yamluna `_yamluna.parse` | 11.26 s | 6.41 s | 3.98 s | 2.90 s | 3.89x |
| yamluna `YAML.load` | 13.10 s | 8.87 s | 6.72 s | 5.34 s | 2.45x |
| ruamel `YAML.load` | 16.49 s | 15.79 s | 16.52 s | 16.83 s | 0.98x |

Only the parse is GIL-free. Building the records and the `CommentedMap`s on top of them is
Python object creation and holds the lock, which is why a whole `load` reaches 2.45x where
the parse reaches 3.89x. Past four threads this laptop schedules onto efficiency cores, so
the per-thread gain tails off.

!!! warning "Build with `--release` before you measure"

    A debug build of the extension is slower than ruamel. Measured on the same machine,
    loading `nested`: release 334 ms, debug 835 ms, ruamel 465 ms. That is 1.4x faster and
    1.8x slower from the same source. `maturin develop --uv --release`.

### PyYAML, doing a different job

The same two inputs, load only, measured here with the same `timeit` median of five:

| document | yamluna | ruamel.yaml | PyYAML `CSafeLoader` | PyYAML `SafeLoader` |
|---|---:|---:|---:|---:|
| `config`, 1 KiB | 0.18 ms | 1.52 ms | 0.07 ms | 0.71 ms |
| `nested`, 249 KiB | 334 ms | 465 ms | 24.7 ms | 246 ms |

PyYAML with libyaml loads the deep tree thirteen times faster than yamluna and hands back
plain `dict`s and `list`s with the comments, the blank lines, the quoting, the anchors and
the tags gone. If that is what you need from the file, that is the right answer and this
library is the wrong one. The comparison only becomes meaningful once you intend to write
the file back.

Neither PyYAML loader scales across threads: 32 loads of `nested` take 1.37 s on one thread
and 1.78 s on eight with `CSafeLoader`, 9.63 s and 9.07 s with `SafeLoader`.

## strictyaml

`strictyaml` is a pure-Python library, built on `ruamel.yaml`, that parses a deliberately
restricted subset of YAML. Its documentation lists implicit typing, flow style, node anchors
and aliases, and explicit tags among the features it removes on purpose: you declare a schema
and everything else is a string. That is a different problem from this one. yamluna's job is
to reproduce whatever the author wrote, including the parts `strictyaml` exists to forbid.

If your YAML is configuration you control and you want it validated rather than preserved,
`strictyaml` and yamluna are not competing.

## Coming from Rust

yamluna's scanner is a fork of [`saphyr-parser`](https://github.com/saphyr-rs/saphyr) 0.0.12,
a YAML 1.2 parser for Rust, with comments, block-versus-flow collection style and anchor
names added to the token and event streams. Upstream carries none of the three, which is the
reason for the fork rather than a dependency: with `keep_comments` off, the fork's streams
are byte-identical to upstream's. Every change is logged in
[FORK.md](https://github.com/qilimanjaro-tech/yamluna/blob/master/crates/yamluna-scanner/FORK.md).

`yaml-rust2` is a maintained fork of the older `yaml-rust`, and `serde_yaml` is archived
upstream as of 2024. None of the three gives you a Python object, and none of them is a
round-trip library in the sense this page means: they parse YAML into Rust values and lose
the layout on the way. If you want the Rust side of yamluna, that is
[`yamluna-core`](https://github.com/qilimanjaro-tech/yamluna/tree/master/crates/yamluna-core),
which is where the document model and the emitter live, and
[Internals](internals/index.md) describes the contract between it and Python.

## When not to use yamluna

* **You need `typ='safe'`.** `YAML(typ='safe')` raises a `ValueError` pointing here. There is
  one mode, and it is round-trip. Use PyYAML or `ruamel.yaml` for a plain load, and keep
  yamluna for the files you edit and write back.
* **You are on Python 3.10 or older.** The wheels are `cp311-abi3` and `pyproject.toml`
  requires 3.11.
* **You need a pure-Python dependency.** Loading and dumping go through a compiled extension.
  If your deployment cannot take one, `ruamel.yaml` is the round-trip library that does not
  have one.
* **You want a multi-document stream through `load` and `dump`.** They take one document and
  raise `ComposerError` on a second, exactly as ruamel's do. `load_all` and `dump_all` are
  the pair that take a stream, and the two `yaml-test-suite` cases yamluna does not
  reproduce (306 of 308) are measured through them.
* **You need to round-trip a document with duplicate keys.** `CommentedMap` is a `dict`, so
  two entries with equal keys are one entry, and the second is gone before the emitter sees
  the document. `ruamel.yaml` cannot do it either, for the same reason. Loading raises
  `DuplicateKeyError` naming both source positions; `allow_duplicate_keys = True` downgrades
  that to a warning and keeps the last value, which is a choice about which value you want,
  not a way to write both back.
