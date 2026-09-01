# Architecture

Four layers, and each owns one job. The seam between any two of them is narrow enough to test on
its own, which is the point. The first four directories are the layers; the rest is what holds
them to account.

```text
crates/yamluna-scanner/   forked saphyr-parser: comments, collection style, anchor names
crates/yamluna-core/      document model, loader, trivia attachment, emitter
crates/yamluna-py/        PyO3 boundary; releases the GIL around parse and emit
python/yamluna/           the API: YAML, CommentedMap/Seq, scalar types, registry, errors
tests/                    corpus, differential harness, the acceptance suite
bench/                    yamluna vs ruamel, and the parallel-load scaling
examples/                 three runnable scripts, each carrying its real output
ci/                       the wheel smoke test CI runs against a fresh venv
```

## What each layer owns

| layer | owns | does not |
|---|---|---|
| `yamluna-scanner` | tokens and parser events, including comments, whether a collection was written in block or flow style, and anchor names as spelled | build a tree, or know what a document is |
| `yamluna-core` | the `Document` model, the loader that turns events into it, comment attachment, and the emitter that writes it back | touch Python, or map a tag to a Python class |
| `yamluna-py` | translating `Document` to and from the record classes, releasing the GIL, turning a `ParseError` into an exception class | make a formatting decision, or hold a policy |
| `python/yamluna` | `YAML`, the container and scalar types, `.ca` / `.lc` / `.anchor`, the tag registry, the error hierarchy | write a single byte of YAML text |

One rule follows from that table, and it has two halves:

**Rust never walks a `CommentedMap`, and Python never formats YAML text.** Every layout
decision, down to where a comma goes, happens in `crates/yamluna-core/src/emitter/`. Every
decision about what a Python object *is*, down to whether `0x1F` comes back as a `HexInt`,
happens in `python/yamluna/`.

## The boundary

The FFI is flat and symmetric: two functions, one list of records in each direction.

```python
def parse(source: str, *, allow_duplicate_keys: bool = True,
          name: str = '<unicode string>') -> list[Doc]: ...

def emit(docs: list[Doc], opts: EmitOptions) -> str: ...
```

A `Doc` is one YAML document: an arena of `Node` records, plus the root's index and the facts
that live outside the tree. A node names its children by index into that arena, never by
reference, so the whole document is one flat list on both sides of the seam.

The record classes are defined **once, in Python**, in
[`python/yamluna/_record.py`](https://github.com/fedonman/yamluna/blob/main/python/yamluna/_record.py).
There is no `#[pyclass]` in `yamluna-py`: a second definition of `Node` in Rust would be a
second contract to keep in step. Rust imports that module once, caches a class object each for
`Node`, `Trivia` and `Doc`, calls them to build instances on load, and reads their attributes by
name on dump. You can call both halves directly:

```python
from yamluna._record import EmitOptions
from yamluna._yamluna import emit, parse

docs = parse('name: demo   # shown in the UI\nports: [80, 443]\n')
print(type(docs[0]))
print(docs[0].nodes[0])
print(docs[0].nodes[2])
print(repr(emit(docs, EmitOptions())))
```

```text
<class 'yamluna._record.Doc'>
Node(kind=MAPPING, style=BLOCK, children=[1, 2, 3, 4], tag_first=False, colon=[(0, 4), (1, 5)])
Node(value='demo', raw='demo', col=6, eol=Trivia(text='# shown in the UI', own_line=False, col=13), tag_first=False)
'name: demo   # shown in the UI\nports: [80, 443]\n'
```

Those records carry two kinds of field: what the document *means* (`value`, `tag`, `children`)
and how the source *spelled* it (`raw`, `colon`, `flow_seps`, and the rest). The Python layer
never reads the second kind. It hands them back unchanged for a node it did not change, and
drops them for one it did. [The document model](document-model.md) is about why that second kind
exists.

## A load

1. `YAML.load` reads the stream to a `str` and calls `_yamluna.parse`.
2. `yamluna-py` releases the GIL and hands the text to `yamluna_core::parse`.
3. The loader drives the forked scanner with `keep_comments(true)` and builds one `Document`
   per YAML document, hanging every comment and blank line off the node it was written against
   as it goes.
4. Still inside Rust, each `Document` becomes a `Doc` record. This part takes the GIL back: it
   is Python object creation.
5. `yamluna.constructor` walks the arena and builds the tree you get back, choosing a Python
   type per scalar, resolving registered tags, and projecting the four trivia slots into `.ca`.

## A dump

The same path in reverse. It shares no code with the load: the representer is the
constructor's inverse, not its reuse.

1. `yamluna.representer` walks your object tree and builds one `Doc` record per document,
   reading comments off the identity-keyed store and copying the untouched spelling fields
   through.
2. `YAML.dump_all` applies the instance settings that override the document (`explicit_start`,
   `explicit_end`, `version`) and calls `_yamluna.emit`.
3. `yamluna-py` converts the records back to `Document`s, releases the GIL, and calls
   `yamluna_core::emit`.
4. The emitter writes each node: `raw` verbatim when the node still carries it, a fresh layout
   from `EmitOptions` when it does not.

## Why the seam is shaped like this

Because both halves stay testable alone. The emitter has pure-Rust unit tests; the Python layer
has pure-Python tests against hand-built record lists, and importing `yamluna` does not need the
extension at all. `YAML.load` and `YAML.dump` are the only two calls that do.

The other reason is that a flat seam can be *measured*. `yaml-test-suite` is round-tripped
twice over the same 308 cases: once by `yamluna_core::{parse, emit}` inside Rust, and once by
`YAML().load_all` / `.dump_all`. Both, on this commit:

```bash
cargo test -p yamluna-core --test proptest_roundtrip \
    yaml_test_suite_round_trips_byte_for_byte -- --nocapture
.venv/bin/python -m pytest tests/test_suite_roundtrip.py -s -q
```

```text
yaml-test-suite: 308/308 parsed cases round-trip byte-identically (0 of 308 did not parse, 0 known gaps)
yaml-test-suite through the Python API: 306/308 cases round-trip byte-identically (2 known gaps)
```

The difference between the two numbers is exactly what the boundary and the object model lose,
and publishing both is what keeps it from growing quietly. It has grown quietly before, which is
why the second harness exists at all: its own docstring records that at commit `8b05b39` the same
cases scored 302 in Rust and 202 in Python, because four recorded facts had no record slot to
cross on. [Testing](testing.md) covers the harnesses; the two cases the Python side still misses
are a `dict` limitation, not unfinished work.

## Threads

`_yamluna.parse` runs the scanner, the loader and the trivia attachment inside `Python::detach`,
so parses on several threads genuinely overlap. Building the flat records, and everything
`yamluna.constructor` does on top of them, is Python object creation and holds the lock. That
split is visible in the scaling numbers on [How it compares](../comparison.md), and it is the
practical reason the layers are drawn where they are.

## Read next

| | |
|---|---|
| [The document model](document-model.md) | the rule the whole codebase turns on, and the facts it has to record |
| [The forked scanner](scanner.md) | what changed in `saphyr-parser`, and why |
| [Testing](testing.md) | the corpus, the differential harness, and the acceptance gates |
| [Measured ruamel behaviour](ruamel-behaviour.md) | the raw measurements the comparison pages cite |
