# yamluna

yamluna is a round-trip YAML library for Python. You load a document, change the parts you care
about, write it back, and everything you did not touch comes back exactly as the author wrote
it: the comments, the blank lines, the quoting, the anchors, the directives, the indentation,
the alignment of the trailing comments.

The whole pipeline is Rust. The scanner is a fork of
[`saphyr-parser`](https://github.com/saphyr-rs/saphyr) 0.0.12, extended to report the three
things a round trip needs and a parser normally throws away: comments, whether a collection was
written in block or flow style, and the names of anchors. On top of it sit a document model that
records what the source wrote rather than what it meant, and an emitter that writes those
recordings back. The Python layer is thin, and the parse and the emit run with the GIL released,
so loads across threads genuinely overlap.

It is [YAML 1.2](https://fedonman.github.io/yamluna/comparison/), with 1.1 scalar resolution for
a document that asks for it with `%YAML 1.1`. The public API is `ruamel.yaml`'s `typ='rt'`: the
same `YAML` object, the same `CommentedMap` and `CommentedSeq`, the same `.ca` and `.lc`, the
same scalar types and exception hierarchy, so porting is an import change.

**Documentation: <https://fedonman.github.io/yamluna/>**

```python
from pathlib import Path

from yamluna import YAML

yaml = YAML()                            # typ='rt' is the only mode
config = yaml.load(Path('config.yaml'))  # a dict, with the file's layout remembered

config['replicas'] = 5                   # comments and blank lines stay where they are
config['ports'].append(8080)
del config['legacy_mode']                # takes its own comment, and only its own

yaml.dump(config, Path('config.yaml'))
```

`config.yaml` before:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 3
legacy_mode: true     # remove me before 2.0

ports:
  - 80                # http
  - 443               # https
```

and after. The blank line, the indentation, the comment alignment and the two comments that
describe surviving keys are all still there; the one comment that described `legacy_mode` went
with it:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 5

ports:
  - 80                # http
  - 443               # https
  - 8080
```

Every node reproduces the layout it was loaded with, rather than having one global setting
re-applied to the whole document. An edit produces a diff containing only the edit.

## Install

Not on PyPI yet. From a clean checkout you need a Rust toolchain (1.85+, `edition = "2024"`)
and Python 3.11+:

```bash
git clone https://github.com/fedonman/yamluna
cd yamluna

uv venv                            # or: python -m venv .venv
uv pip install -e . --group dev    # or: .venv/bin/pip install -e . && ... install the dev deps

.venv/bin/python -c "import yamluna; print(yamluna.__version__)"
```

`pip install -e .` builds the extension through maturin. During development,
`.venv/bin/maturin develop --uv` rebuilds it in place after a Rust change. That is `just build`,
and `just check` is build + `cargo test --workspace` + `pytest`.

There is no runtime dependency. Importing `yamluna` does **not** need the extension either: the
object model, the scalar types, the error hierarchy and the tag registry are pure Python. Only
`load` and `dump` reach into Rust, and they say so if it is not built. Release wheels target
CPython 3.11+ through the stable ABI (`abi3-py311`), so one wheel covers every version from
3.11 up.

## What survives a round trip

The guarantee is byte-level: load a file, change nothing, dump it, and you get the same bytes.

| | |
|---|---|
| **Comments** | End-of-line and own-line, in every position: above an entry, after a value, at the top of the file, at end of file with no trailing newline, around `---` and `...`, inside flow collections, on a block-scalar header, beside an anchor. The column a trailing comment was aligned to is part of it. |
| **Blank lines** | A run of blank lines is trivia with a count, one token per line with `is_blank_line` set, so "how many were here" has an answer. Whitespace-only lines are not normalised away. |
| **Scalar spelling** | An unchanged scalar is written back as its source text, character for character: `1_000.5`, `007`, `+12`, `0X1F`, `-0x1F`, `0o755`, `0b1010`, `TRUE`, `on`, timestamps, escape sequences, `!!binary`. Long lines are never refolded, and non-ASCII stays non-ASCII rather than becoming `\uXXXX`. |
| **Quoting and block style** | All five scalar styles, plus a block scalar's indentation and chomping indicators, so `\|-2` stays `\|-2`. Quoting survives whether or not `preserve_quotes` is set. |
| **Layout** | Each node's own indentation, including a file that mixes two indentation styles; flow or block style per collection; key order; explicit `? key` indicators; empty collections. |
| **Anchors and merges** | `&name` is emitted because the file has it, referenced or not. `*name` loads as the same object, `<<` is recorded rather than expanded, and a self-aliasing node loads as a genuinely cyclic Python object. |
| **Stream bytes** | `%YAML` and `%TAG` directives per document, `---` and `...` markers, multi-document streams, a BOM, CRLF (read off a multi-line scalar's lexeme; a CRLF file without one needs `line_break = '\r\n'`), tabs, a missing final newline, and a file that is nothing but comments. |
| **Tags** | Local and global tags, including tags for classes nothing has registered: those round-trip untouched. |

`tests/corpus/` is 41 hand-written files, one YAML concern each, and every one of them is
checked exactly this way. [How it is tested](#how-it-is-tested) has the scores.

## Comments belong to nodes

A comment describes the thing written under it or beside it, and yamluna files it on that thing.
Every entry owns its trivia and the record travels with the entry: nothing is keyed by line
number, nothing is keyed by list index. `insert`, `del`, `pop`, `sort`, `reverse`, `rename`,
`move_to_end` and slice assignment all carry each comment with the element it describes, which
makes the mutating operations correct by construction, including the ones nobody remembered to
override.

```yaml
hosts:
  - web-01      # eu-west
  # internal only
  - worker-01   # eu-west
  # nightly batch
  - cron-01     # us-east
```

`doc['hosts'].reverse()` reverses the comments with it, and re-indents nothing:

```yaml
hosts:
  # nightly batch
  - cron-01     # us-east
  # internal only
  - worker-01   # eu-west
  - web-01      # eu-west
```

Reading trivia is `.ca`: `.ca.comment` is the node's own `[end-of-line token, [own-line tokens]]`,
`.ca.items` is a four-slot record per entry, `.ca.end` is what trails the last one, and
`.ca.get(entry, slot)` returns `None` rather than raising. Writing it is a handful of methods
that take plain strings and add the `#` for you:

```python
config.yaml_add_eol_comment('bumped for the new cluster', 'replicas')
config.yaml_set_comment_before_after_key('ports', before='exposed to the load balancer')
config.yaml_set_start_comment('service configuration')
config.insert(2, 'region', 'eu-west', comment='closest to the users')
```

`yaml_add_eol_comment` aligns the new comment to its neighbours when you do not name a column.
`.lc` carries the load-time position of every node: `.lc.line`, `.lc.col`, `.lc.key(k)`,
`.lc.value(k)` and `.lc.item(i)`, 0-based, `None` for a node with no recorded position.

One position is not right yet, and it is written down rather than glossed: an own-line comment
above a collection's **first** child is filed on the collection rather than on that child, so it
stays put while the child moves; and inserting, or assigning to a slice, immediately before an
item that carries an own-line comment strands its `-` on a line of its own. Twelve xfails in
`tests/test_mutation.py` pin both. Every byte still round-trips either way; what is wrong is the
ownership, for one position out of n. Until it is fixed, prefer `append` and `move_to_end` over
`insert(0, ...)` into a commented sequence.

Full detail: [Comments and blank lines](https://fedonman.github.io/yamluna/guide/comments/).

## Scalar styles and types

A value written one way must not come back written another. Every scalar you did not touch keeps
its source lexeme; every scalar you do touch keeps its formatting fields, so `0x0f += 1` is
`0x10` and `1_000 += 1` is `1_001`. To choose a spelling for a value you create, assign the type
that carries it:

| | |
|---|---|
| `LiteralScalarString`, `FoldedScalarString` | `\|` and `>` blocks (`PreservedScalarString` is the literal one under its ruamel name) |
| `SingleQuotedScalarString`, `DoubleQuotedScalarString`, `PlainScalarString` | the three inline styles; plain is a request, and a value that would not read back as itself is quoted anyway |
| `ScalarInt`, `HexInt`, `OctalInt`, `BinaryInt` | `007`, `0x1F` (`caps=`), `0o755`, `0b1010` |
| `ScalarFloat`, `ScalarBoolean`, `TimeStamp` | digit separators and exponent form, `on`/`yes`/`TRUE` spellings, timestamp separator and zone |

Every one of them answers `.lexeme()` with its source text, and every one but `TimeStamp`
takes an `anchor=` keyword.
`walk_tree(data)` rewrites every string containing a newline into a literal block, and
`preserve_literal(value)` is the single-value form. Loading returns a scalar class only when a
builtin would write something else back, so ordinary values stay ordinary `int`, `str` and
`bool`. Block style belongs to the entry rather than to the string, so assigning a bare `str`
into a slot that held a `|` block keeps the block.

Full detail: [Scalar styles and types](https://fedonman.github.io/yamluna/guide/scalars/).

## Anchors, aliases and merge keys

`&name` marks a node, `*name` refers back to it, `<<` merges one mapping into another, and
yamluna keeps all three as references: nothing is expanded and nothing is dropped.

```python
doc['web'] is doc['defaults']   # True: an alias is the object its anchor named
doc['defaults'].anchor.value    # 'defaults'
doc.non_merged_items()          # this mapping's own entries, without the merged ones
doc.yaml_set_anchor('base')     # authoring one; no registration step
```

An anchor that is in the source is in the output whether or not anything refers to it. A merge
key keeps its original sibling position and comes back as `<<: *name`, including `<<: [*base,
*extra]` and merges inside flow mappings. An object that appears twice with no anchor of its own
gets a generated `&id001`. The edges are errors rather than guesses: an undefined alias, an
anchor referenced across a document boundary, a second `<<` in one mapping.

Full detail: [Anchors, aliases and merge keys](https://fedonman.github.io/yamluna/guide/anchors/).

## The tag registry

`yaml.register_class(Circuit)` teaches one `YAML` instance to write your own classes and read
them back. The registry is keyed on the fully qualified class path, so registration cannot
overwrite, and the namespace goes into the document using YAML's own mechanism: `%TAG`
directives. One directive line per source, no pollution of user data, and any conformant YAML
parser round-trips it.

**One library.** Its source takes the primary `!` handle, so tags are bare:

```yaml
%TAG ! tag:libx/
---
main: !Circuit
  qubits: 2
```

**Two libraries.** The most-used source keeps `!`; the rest get named handles derived from the
source name (ties broken on the name, so the output does not depend on registration order):

```yaml
%TAG ! tag:libx/
%TAG !liby! tag:liby/
---
a: !Circuit
  qubits: 2
b: !liby!Circuit
  n: 3
```

**Two modules of one library.** Both classes are `Circuit`, both sources default to the root
package `libx`, so the colliding pair is automatically promoted to full module paths and the same
rule applies:

```yaml
%TAG ! tag:libx.circuits/
%TAG !libx-gates! tag:libx.gates/
---
a: !Circuit
  qubits: 2
b: !libx-gates!Circuit
  width: 1
```

Promotion is a pure function of the registry contents, recomputed on every registration; an
explicit `register_class(cls, source='qilisdk')` pins the source and is never promoted.

Reading back, a tag that resolves through a directive to `tag:{source}/{name}` is looked up by
`(source, name)`: exactly one match, or an error. A **bare** `!Circuit` in a hand-written file
with no directive in scope is looked up by name alone: one candidate constructs it, more than one
raises rather than guessing.

```
ConstructorError: ambiguous tag '!Circuit': 2 registered candidates:
libx.circuits.Circuit (= tag:libx/Circuit), liby.core.Circuit (= tag:liby/Circuit);
yamluna will not guess. Add a %TAG directive naming the source (e.g. '%TAG ! tag:libx/')
or re-register with an explicit source= to disambiguate.
  in "<unicode string>", line 2, column 3
```

A tag in a namespace this registry has never heard of is somebody else's document: it round-trips
untouched, tag and all.

The registry is **per `YAML()` instance**. `yaml.register_class(Circuit)` never touches another
instance's registry, so a library that builds its own `YAML()` cannot poison anybody else's, or
be poisoned by one. A module-level `register_class` and a shared `default_registry` exist for the
"one registry for my whole app" case, opted into with `YAML(registry=default_registry)`.

By default a class is written as a mapping of `obj.__getstate__()` or `obj.__dict__`, and read
back through `cls.__new__` and `__setstate__` without calling `__init__`. Define `to_yaml` and
`from_yaml` classmethods to write it as something else. One cost is worth knowing: comments and
blank lines *inside* a node that loads into a registered class are lost on the next dump, because
the class has nowhere to keep them.

How to use it: [Custom classes and tags](https://fedonman.github.io/yamluna/guide/custom-classes/).
Runnable: [`examples/custom_classes.py`](examples/custom_classes.py).

## Loading, dumping and settings

`load` takes a `str` (the document *text*, never a path), `bytes` decoded from its byte-order
mark, an `os.PathLike`, or anything with `.read()`; anything else raises `YAMLStreamError`. A
second `---` in the stream raises rather than silently returning the first document; that is
what `load_all` is for. `dump` writes to a path or a stream, detecting text against binary for
you, and returns the emitted text when you give it no destination. The context-manager form
collects a whole multi-document stream:

```python
with YAML(output=Path('out.yaml')) as yaml:
    yaml.dump(first)
    yaml.dump(second)      # written as one stream at exit, and not at all if the block raises
```

Settings are plain attributes, and one rule runs through all of them: **`None` means "keep what
the source had"**, not "apply the built-in default". Most of them therefore only decide how nodes
*you* built are written.

| setting | default | what it does |
|---|---|---|
| `preserve_quotes` | `None` | Emit a quoted-string object you constructed with its quotes. A scalar from a file keeps its quotes either way. |
| `default_flow_style` | `False` | `True` writes every collection in flow style. `False` honours each node's `.fa`, and block style when it asks for nothing. |
| `width` | `None` (80) | Column a scalar the emitter lays out is folded at. A scalar that remembers its source line is never re-wrapped. |
| `explicit_start`, `explicit_end` | `None` | Force `---` / `...` on, or off. `None` keeps the marker each document had. |
| `allow_duplicate_keys` | `False` | `False` raises `DuplicateKeyError` naming both positions; `True` warns and the last value wins. |
| `line_break` | `None` | `'\n'`, `'\r\n'` or `'\r'`. `None` takes it from the documents. |
| `encoding` | `'utf-8'` | Used only where text becomes bytes: a path destination, or a stream that rejects `str`. |
| `map_indent`, `sequence_indent`, `sequence_dash_offset` | `None` (2, 2, 0) | Indentation for nodes you built; `yaml.indent(mapping=, sequence=, offset=)` sets all three. |
| `version` | `None` | `(1, 2)` or `'1.2'`; writes `%YAML` and picks the resolution rules for values you create. |
| `registry` | fresh `TagRegistry` | This instance's tag registry, never shared unless you pass one in. |

`YAML` uses `__slots__`, so `yaml.preserve_qoutes = True` is an `AttributeError` at the
assignment with a "Did you mean" suggestion, rather than an option that silently does nothing.

Nearly every failure out of `load` or `dump` says where in the document it happened. `YAMLError`
is the base, and `ScannerError`, `ComposerError`, `ConstructorError`, `DuplicateKeyError` and
`RepresenterError` are the ones you will catch. A parse failure fills `.problem` and a `Mark`
with a 0-based line and column, and the caret in the printed message is placed by character
rather than by byte, so a line with an emoji in it still points at the right column. A
`RepresenterError`, and the "more than one document" error out of `load`, are about the call
rather than a position, so they carry a message and no mark. `YAMLStreamError` sits outside
`YAMLError` deliberately: it means the argument was wrong, not the document.

Full detail: [Loading and dumping](https://fedonman.github.io/yamluna/guide/load-and-dump/),
[Settings](https://fedonman.github.io/yamluna/guide/settings/),
[Errors](https://fedonman.github.io/yamluna/guide/errors/).

## How it is tested

`tests/corpus/` is 41 hand-written files, one YAML concern each: comments in every position,
anchors, merge keys, directives, document markers, block-scalar headers, a BOM, CRLF, a file with
no trailing newline, a file that is nothing but comments. The measure is the strict one: load the
file, change nothing, dump it, and compare **bytes**.

| harness | what it round-trips | score |
|---|---|---|
| `cargo test -p yamluna-core --test roundtrip` | the Rust core over the corpus | **41 / 41** byte-identical |
| `.venv/bin/python tests/differential.py` | the Python API over the corpus | **40 / 40** byte-identical |
| `cargo test -p yamluna-scanner --test yaml-test-suite` | the scanner against the conformance corpus | **402 / 402** cases |
| `cargo test -p yamluna-core --test proptest_roundtrip` | the Rust core over the suite, `parse` → `emit` | **308 / 308** byte-identical |
| `.venv/bin/python tests/suite_roundtrip.py` | the Python API over the suite, `load_all` → `dump_all` | **306 / 308** byte-identical |

Those five numbers are not interchangeable and none of them is "the" score: the 402 counts every
conformance case including the ones that must fail to parse, and the 308 is the parse-expected
subset of it. The last row is the one a user actually gets, and it is the lowest, so all of them
are quoted.

The corpus file the Python API does not manage is `key-duplicate`, and the two suite cases are
`2JQS` and `X38W`. All three are the same trade, and it is deliberate: `CommentedMap` is a
`dict`, so two entries with equal keys (including two empty keys, and an alias that *is* its own
mapping's key) are one entry, and loading raises `DuplicateKeyError` naming both source
positions. What subclassing the builtins buys in exchange is that `isinstance(x, dict)`,
`json.dumps`, `copy.deepcopy`, `pickle` and `==` all work on a loaded document with no conversion
step. `key-duplicate` is scored on behaviour instead, which is why that denominator is 40.

Both harnesses read the same 308 cases, extracted the same way, so the numbers subtract, and
`test_the_record_seam_loses_nothing_over_the_suite` asserts that all 308 survive the crossing
into Python unchanged, so the difference is the object model and nothing else. Every gap list in
the repository is a two-way gate: an unlisted failure fails the run, and a listed entry that
starts passing fails it too, so an excuse cannot go stale. Alongside them a proptest generates
documents and asserts the same property, a second pass asserts that emitting twice is a fixed
point, and a conservation test asserts that every `#` run comes back in source order even for a
file that is not byte-identical.

`tests/README.md` has the per-file table and what each layer asserts.

## Speed

`bench/bench.py` generates its four inputs rather than committing them and reports the median of
five `timeit` batches. Measured on a 12th Gen Intel Core i7-1280P (20 hw threads), Linux, CPython
3.13.12, yamluna 0.1.0, release build: a 1 KiB config file, a 249 KiB deep tree, a 150 KiB file
that is three-quarters trivia and a 37 KiB run of every scalar style all load and dump between
1.4x and 8.9x faster than `ruamel.yaml` 0.19.1, the pure-Python implementation of the same API.
The narrow end is the deep tree, and it is the number worth knowing: a document that is almost
entirely collection structure spends its time building one Python object per node, which no
amount of Rust helps with. Splitting a round trip into three layers (Rust only, then + FFI
records, then + object model) puts **22–81%** of the time in building the Python objects, which
is where the next win is.

`_yamluna.parse` runs the scanner, the loader and the trivia attachment inside `py.detach`, so
loads across threads genuinely overlap, which a pure-Python library cannot do at all. 32 loads of
the 249 KiB `nested` document, spread over N threads (each count measured twice, ascending and
descending, faster of the two, so the ordering does not decide the answer):

| workload | 1 | 2 | 4 | 8 | speedup at 8 |
|---|---:|---:|---:|---:|---:|
| `_yamluna.parse` | 11.26 s | 6.41 s | 3.98 s | 2.90 s | **3.89x** |
| `YAML.load` | 13.10 s | 8.87 s | 6.72 s | 5.34 s | 2.45x |

Sub-linear, and stated as measured. Only the *parse* is GIL-free: building the flat `Node`
records, and everything the constructor does on top of them, is Python object creation and holds
the lock, which is why `YAML.load` reaches 2.45x where `parse` reaches 3.89x. The gain per thread
falls at every step. Memory traffic and this laptop's mix of performance and efficiency cores
both bite, and neither is measured here, so read the table rather than extrapolating it. Build the
extension with `maturin develop --uv --release` before benchmarking; a debug build is not
representative. Every figure here is one run of `python bench/bench.py` on the machine above,
and a laptop under thermal management does not repeat to better than a few percent, so read the
shape, not the third digit.

## What it is not

`typ='rt'` only; no safe/base/unsafe; no `!!python/object:`; no component substitution; no
plug-ins; no `scan()`/`compose()`/`serialize()`; no legacy module-level `load()`/`dump()`.

Those are deliberate omissions, not gaps. Round-trip is the mode this library is for, and the
others are `json.load` with more spelling; `PyYAML` and `ruamel.yaml` are both good at them.
`YAML(typ='safe')` raises with a message pointing here, and
[Migrating](https://fedonman.github.io/yamluna/migrating/) lists the replacement for each one.

## Examples

Each file runs standalone (`.venv/bin/python examples/round_trip.py`) and carries its real output
at the bottom.

| | |
|---|---|
| [`examples/round_trip.py`](examples/round_trip.py) | load, edit, dump; comments, blank lines and quoting preserved |
| [`examples/custom_classes.py`](examples/custom_classes.py) | `register_class` across two modules, and the `%TAG` output that disambiguates them |
| [`examples/comments.py`](examples/comments.py) | reading and writing `.ca` programmatically |

## Documentation

The site is <https://fedonman.github.io/yamluna/>, built from `docs/` with
[zensical](https://zensical.org).

| | |
|---|---|
| [Why yamluna](https://fedonman.github.io/yamluna/why/) | the problem it was built for, and what node-owned comments buy you |
| [Install](https://fedonman.github.io/yamluna/install/) | wheels, building from source, and the `just` recipes |
| [Guide](https://fedonman.github.io/yamluna/guide/) | [loading and dumping](https://fedonman.github.io/yamluna/guide/load-and-dump/), [comments](https://fedonman.github.io/yamluna/guide/comments/), [scalars](https://fedonman.github.io/yamluna/guide/scalars/), [anchors](https://fedonman.github.io/yamluna/guide/anchors/), [custom classes](https://fedonman.github.io/yamluna/guide/custom-classes/), [settings](https://fedonman.github.io/yamluna/guide/settings/), [errors](https://fedonman.github.io/yamluna/guide/errors/) |
| [API reference](https://fedonman.github.io/yamluna/api/) | every public class and function, generated from the docstrings |
| [How it compares](https://fedonman.github.io/yamluna/comparison/) | next to the other YAML libraries, and which YAML version each reads |
| [Migrating](https://fedonman.github.io/yamluna/migrating/) | the ruamel API to this one, what is deliberately different, and what is missing |
| [Changelog](https://fedonman.github.io/yamluna/changelog/) | what is in each release, and the known gaps |

In the repository, and not on the site:

| | |
|---|---|
| [tests/README.md](tests/README.md) | what each test layer asserts, and the per-file corpus table |
| [crates/yamluna-scanner/FORK.md](crates/yamluna-scanner/FORK.md) | every change made to the vendored parser |

## Layout

```
crates/yamluna-scanner/   forked saphyr-parser: comments, collection style, anchor names
crates/yamluna-core/      document model, loader, trivia attachment, emitter
crates/yamluna-py/        PyO3 boundary; releases the GIL around parse and emit
python/yamluna/           the API: YAML, CommentedMap/Seq, scalar types, registry, errors
tests/                    corpus, differential harness, the acceptance suite
bench/                    the benchmark documents and the parallel-load scaling
examples/                 three runnable scripts, each carrying its real output
docs/                     the documentation site, built with zensical from zensical.toml
ci/                       the wheel smoke test CI runs against a fresh venv
```

The FFI boundary is flat and symmetric (`parse(str) -> list[Doc]`, `emit(list[Doc]) -> str`), and
the record types are defined once, in Python, in `python/yamluna/_record.py`. Rust never walks a
`CommentedMap`; Python never formats YAML text.

## Licence

MIT OR Apache-2.0. `crates/yamluna-scanner` is a fork of
[saphyr-parser](https://github.com/saphyr-rs/saphyr) 0.0.12 under the same terms; see
[FORK.md](crates/yamluna-scanner/FORK.md) for every change made to it.
