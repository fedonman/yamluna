# yamluna

Round-trip YAML for Python. Load a document, change what you need, write it back — with the
comments, blank lines, quoting, anchors, directives and indentation the author put there still
in place. The scanner, document model and emitter are Rust; the API is `ruamel.yaml`'s
`typ='rt'`, minus its bugs.

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

and after — the blank line, the indentation, the comment alignment and the two comments that
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

The same script through `ruamel.yaml` 0.19.1 — only the import changes — produces the right
*data* and loses the blank line, re-indents the sequence to column 0, and shifts both
end-of-line comments to follow:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 5
ports:
- 80                  # http
- 443                 # https
- 8080
```

That diff is why this library exists. yamluna reproduces each node's own layout instead of
re-deciding it globally, so an edit produces a diff containing only the edit.

## Install

Not on PyPI yet. From a clean checkout you need a Rust toolchain (1.85+, `edition = "2024"`)
and Python 3.11+:

```bash
git clone https://github.com/qilimanjaro-tech/yamluna
cd yamluna

uv venv                            # or: python -m venv .venv
uv pip install -e . --group dev    # or: .venv/bin/pip install -e . && ... install the dev deps

.venv/bin/python -c "import yamluna; print(yamluna.__version__)"
```

`pip install -e .` builds the extension through maturin. During development,
`.venv/bin/maturin develop --uv` rebuilds it in place after a Rust change — that is `just build`,
and `just check` is build + `cargo test --workspace` + `pytest`.

Importing `yamluna` does **not** need the extension: the object model, the scalar types, the
error hierarchy and the tag registry are pure Python. Only `load` and `dump` reach into Rust, and
they say so if it is not built.

## Round trips that actually round trip

`tests/corpus/` is 41 hand-written files, one YAML concern each — comments in every position,
anchors, merge keys, directives, document markers, block-scalar headers, a BOM, CRLF, a file with
no trailing newline, a file that is nothing but comments. The measure is the strict one: load the
file, change nothing, dump it, and compare **bytes**.

Both libraries get the ordinary recipe — `YAML()`, `preserve_quotes = True`, everything else
default. Reproduce with `.venv/bin/python tests/differential.py`:

| | round-trips byte-identically |
|---|---|
| `ruamel.yaml` 0.19.1 | **3 of 40** |
| yamluna | **40 of 40** |

Point ruamel at the indentation style most of the corpus uses
(`yaml.indent(mapping=2, sequence=4, offset=2)`, i.e. `differential.py --seq-indent`) and it
manages 7 of 40. No single setting does better, because `struct-seq-indent.yaml` mixes two
indentations inside one document — which is what files written by humans do.

The one yamluna does not manage, and why:

| file | why |
|---|---|
| `key-duplicate` | `CommentedMap` is a `dict`, so two entries with equal keys collapse into one. The price of subclassing the builtins (`isinstance(x, dict)`, `json.dumps`, `deepcopy`, `==` all work for free). It is scored on behaviour rather than bytes, which is why the denominator is 40. |

It is pinned by a guard list that fails the suite if it starts passing, so a fix cannot leave a
stale excuse behind. `tests/README.md` has the per-file table and what ruamel does to each one.

The wider net is the **`yaml-test-suite`**, the cross-implementation conformance corpus.
Three harnesses run against it, and they measure three different things — the numbers are not
interchangeable and this table is the honest way to read them:

| harness | what it round-trips | score |
|---|---|---|
| `cargo test -p yamluna-scanner --test yaml-test-suite` | the fork parses what upstream parses | **402 / 402** conformance cases |
| `cargo test -p yamluna-core --test proptest_roundtrip` | the **Rust core**, `parse` → `emit` | **308 / 308** byte-identical |
| `python tests/suite_roundtrip.py` | the **Python API**, `YAML().load_all` → `.dump_all` | **306 / 308** byte-identical |

The middle row is every case the suite has: the Rust core reproduces all 308 byte for byte.
The last row is the one a user actually gets, and it is the lower of the two, so both are
quoted. Both harnesses read the same 308 cases, extracted the same way, so they subtract: the
**two** cases the Rust core round-trips and the Python API does not are `2JQS` and `X38W`, and
both are the object model rather than the seam. `CommentedMap` is a `dict`, so two entries with
an empty key (`2JQS`) or an alias that *is* its own mapping's key (`X38W`) are one entry, and
loading raises `DuplicateKeyError`. Not subclassing `dict` and `list` buys them back, and costs
`isinstance(x, dict)`, `json.dumps`, `deepcopy`, `pickle` and `==` — the trade
[DESIGN §4.1](docs/DESIGN.md) makes on purpose.

That the difference is *only* the object model is a gate, not a measurement:
`test_the_record_seam_loses_nothing_over_the_suite` asserts that all 308 cases come out of
`emit(parse(x))` **through the `_record` classes** byte-identical to `parse`-then-`emit`
inside Rust, so a fact that stops crossing the FFI fails there and names the case. It has not
always held — at `8b05b39` the colon column, the two property positions and the source's own
white space were recorded and emitted correctly by the core while having no record slot, and
the same 308 cases scored 302 in Rust against 202 here. `tests/test_suite_roundtrip.py` is
the gate that fails when either number regresses, and `suite_roundtrip.py --rust` prints them
side by side.

The Rust core's `KNOWN_GAPS` list is now **empty**, and the Python one holds exactly the two
cases above, each marked permanent with the reason. Both are documents YAML's own uniqueness
rule rejects — two entries with the null key, and an alias used as a key of the mapping its
anchor is defined in — so `DuplicateKeyError` naming both source positions is the answer rather
than a workaround to find; ruamel raises on both, and PyYAML never reaches the question. They
are named with their causes in [tests/README.md](tests/README.md#known-gaps) and pinned by a
list that fails the suite if one starts passing, so an excuse cannot go stale. Both harnesses
print every failing case with its input and its output, so a new gap is a worklist rather than
an estimate. Alongside them, a proptest generates documents and asserts the same property, and
a second-pass check asserts that emitting twice is a fixed point.

## Speed

`bench/bench.py` generates its four inputs rather than committing them, gives both libraries the
same two lines of configuration (`YAML()`, `preserve_quotes = True`), and reports the **median**
of five `timeit` batches. Measured on a 12th Gen Intel Core i7-1280P (20 hw threads), Linux,
CPython 3.13.12, yamluna 0.1.0 vs `ruamel.yaml` 0.19.1, release build:

| document | size | load | dump | load+dump |
|---|---:|---:|---:|---:|
| `config` — a hand-written config file | 1 KiB | 8.9x | 3.5x | 5.0x |
| `nested` — a deep tree, many collections | 249 KiB | 1.4x | 3.2x | 1.8x |
| `comments` — three lines in four are trivia | 150 KiB | 3.2x | 2.3x | 2.9x |
| `scalars` — a flat run of every scalar style | 37 KiB | 8.1x | 4.8x | 6.3x |

Faster everywhere, between 1.4x and 8.9x. The margin narrows to **1.4x** on loading the deep
`nested` tree, and that is the number worth knowing: a document that is almost entirely
collection structure spends its time building one Python object per node, which no amount of
Rust helps with. `bench.py` splits a yamluna round trip into three layers — Rust only / + FFI
records / + object model — and finds **22–81%** of the time going into building the Python
objects, which is where the next win is, not in the Rust. (Build the extension with
`maturin develop --uv --release` before benchmarking; a debug build is slower than ruamel on
`nested` — measured, 1.8x slower.)

`_yamluna.parse` runs the scanner, the loader and the trivia attachment inside `py.detach`, so
loads across threads genuinely overlap — something a pure-Python library cannot do at all. 32
loads of the 249 KiB `nested` document, spread over N threads (each count measured twice,
ascending and descending, faster of the two, so the ordering does not decide the answer):

| workload | 1 | 2 | 4 | 8 | speedup at 8 |
|---|---:|---:|---:|---:|---:|
| yamluna `_yamluna.parse` | 11.26 s | 6.41 s | 3.98 s | 2.90 s | **3.89x** |
| yamluna `YAML.load` | 13.10 s | 8.87 s | 6.72 s | 5.34 s | 2.45x |
| ruamel `YAML.load` | 16.49 s | 15.79 s | 16.52 s | 16.83 s | 0.98x |

Sub-linear, and stated as measured. Only the *parse* is GIL-free: building the flat `Node`
records, and everything `Constructor` does on top of them, is Python object creation and holds
the lock, which is why `YAML.load` reaches 2.45x where `parse` reaches 3.89x. ruamel is flat, as
a pure-Python library has to be. Past four threads this laptop schedules onto efficiency cores,
so the per-thread gain tails off. Every figure in this section is one run of
`python bench/bench.py` against a `--release` build on the machine named above; a laptop under
thermal management does not repeat to better than a few percent, so read the shape, not the
third digit.

## The tag registry

This is the part that is deliberately not ruamel-compatible.

`ruamel.yaml`'s `register_class` keys its registry on `'!' + cls.__name__`, in a process-global
table. Two libraries that both define a `Circuit` register the same tag; the second overwrites the
first; loading gives you whichever class was imported last, holding the other one's attributes, with
no warning ([DIVERGENCES C1](docs/DIVERGENCES.md#c1-register_class-keys-the-constructor-registry-on-the-class-name)).

yamluna keys the registry on the fully qualified class path, so registration cannot overwrite, and
writes the namespace into the document using YAML's own mechanism: `%TAG` directives. One
directive line per source, no pollution of user data, and any conformant YAML parser round-trips
it.

**One library** — its source takes the primary `!` handle, so tags are bare:

```yaml
%TAG ! tag:libx/
---
main: !Circuit
  qubits: 2
```

**Two libraries** — the most-used source keeps `!`; the rest get named handles derived from the
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

**Two modules of one library** — both classes are `Circuit`, both sources default to the root
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
`(source, name)` — exactly one match, or an error. A **bare** `!Circuit` in a hand-written file
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
instance's registry, so a library that builds its own `YAML()` cannot poison — or be poisoned by —
anybody else's ([DIVERGENCES C2](docs/DIVERGENCES.md#c2-register_class-is-process-global-not-per-yaml)).
A module-level `register_class` and a shared `default_registry` exist for the
"one registry for my whole app" case, opted into with `YAML(registry=default_registry)`.

Full contract: [DESIGN.md §5](docs/DESIGN.md). Runnable: [`examples/custom_classes.py`](examples/custom_classes.py).

## What it fixes

Every entry in [docs/DIVERGENCES.md](docs/DIVERGENCES.md) is a defect measured against
`ruamel.yaml==0.19.1`, with a repro, and a regression test so it cannot come back. The headlines:

**Comments belong to nodes, not to indices** (A1–A7). ruamel stores an own-line comment glued into
the *previous* sibling's end-of-line token, so `seq.insert(0, x)` labels the new element with the
old first element's comment; `del seq[0]` destroys its neighbour's comment; `CommentedMap.__delitem__`
never touches `.ca` at all, so a deleted key's comment survives and re-attaches itself to an
unrelated value if you later re-add the key; `move_to_end` sends a comment to the far end of the
document; and `CommentedSeq.reverse()` moves no comments whatsoever. yamluna attaches trivia to
the node it describes, so a mutating list or dict operation is correct by construction — including
the ones nobody remembered to override.

One position is not there yet, and it is written down rather than glossed: an own-line comment
above a collection's **first** child is filed on the collection rather than on that child, so it
stays put while the child moves. Twelve xfails in `tests/test_mutation.py` pin it and the four
`docs/DIVERGENCES.md` entries it touches each carry their measured output. Every byte still
round-trips either way; what is wrong is the ownership, for one position out of n.

**Blank lines are counted, not smuggled** (A7, B9). ruamel encodes them as bare `\n`s inside
another node's comment text, which loses a leading blank line and normalises whitespace-only lines.
yamluna models a run of blank lines as a first-class trivium with a count.

**Nothing is silently dropped** (B1–B6, B10). ruamel drops anchors referenced fewer than twice,
`---` and `...` markers, comments after `...`, explicit `? key` indicators, `%YAML` and `%TAG`
directives, the BOM, and every comment inside a flow collection. A file that is only comments
comes back as zero bytes. `? gamma  # c` comes back as YAML that ruamel itself cannot re-read.

**Scalars are reproduced, not re-spelled** (B7, B8, B11, D1, D2). `1_000.5` → `01000.5`,
`+12` → `12`, `-0x1F` → `!!int '0x-1F'` (an integer literal no other implementation accepts),
`0X1F` not recognised as an integer at all, `|-2` re-spelled `|2-`, long lines refolded. yamluna
keeps each scalar's source lexeme and re-emits it verbatim unless you changed the value.

**Serialisation is a read** (A8). ruamel's representer appends to `ca.comment` on every dump, so
`post, pre = obj.ca.comment` raises after the first one. yamluna's emitter takes an immutable
document.

Also fixed: `.ca.end` actually round-trips (A9), `copy()` no longer shares its `Comment` object
with the original (D6), `.lc.key(k)` returns `None` for a node with no recorded position instead of
raising `KeyError` (D7), and `allow_duplicate_keys=True` warns naming both source positions and
lets the last value win, where ruamel silently keeps the first (D5). (Representing both
entries is still the `key-duplicate` gap above — a `dict` holds one.)

## What it is not

`typ='rt'` only; no safe/base/unsafe; no `!!python/object:`; no component substitution; no
plug-ins; no `scan()`/`compose()`/`serialize()`; no legacy module-level `load()`/`dump()`.

Those are deliberate omissions, not gaps. `typ='rt'` is the mode with the interesting problem and
the broken implementation; the others are `json.load` with more spelling. `YAML(typ='safe')` raises
with a message pointing here. See [docs/MIGRATING.md](docs/MIGRATING.md) for the workaround for
each one.

## Examples

Each file runs standalone (`.venv/bin/python examples/round_trip.py`) and carries its real output
at the bottom.

| | |
|---|---|
| [`examples/round_trip.py`](examples/round_trip.py) | load, edit, dump; comments, blank lines and quoting preserved |
| [`examples/custom_classes.py`](examples/custom_classes.py) | `register_class` across two modules, and the `%TAG` output that disambiguates them |
| [`examples/comments.py`](examples/comments.py) | reading and writing `.ca` programmatically |

## Documentation

| | |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | the normative contract between the layers |
| [docs/DIVERGENCES.md](docs/DIVERGENCES.md) | every ruamel defect this library refuses to reproduce, measured |
| [docs/MIGRATING.md](docs/MIGRATING.md) | ruamel API → yamluna API, and what is missing |
| [docs/RUAMEL-BEHAVIOR.md](docs/RUAMEL-BEHAVIOR.md) | the raw measurements the other two cite |
| [tests/README.md](tests/README.md) | what each test layer asserts, and the per-file corpus table |
| [crates/yamluna-scanner/FORK.md](crates/yamluna-scanner/FORK.md) | every change made to the vendored parser |
| [CHANGELOG.md](CHANGELOG.md) | what is in each release, and the known gaps |

## Layout

```
crates/yamluna-scanner/   forked saphyr-parser: comments, collection style, anchor names
crates/yamluna-core/      document model, loader, trivia attachment, emitter
crates/yamluna-py/        PyO3 boundary; releases the GIL around parse and emit
python/yamluna/           the API: YAML, CommentedMap/Seq, scalar types, registry, errors
tests/                    corpus, differential harness, the acceptance suite
bench/                    yamluna vs ruamel, and the parallel-load scaling
examples/                 three runnable scripts, each carrying its real output
ci/                       the wheel smoke test CI runs against a fresh venv
```

The FFI boundary is flat and symmetric — `parse(str) -> list[Doc]`, `emit(list[Doc]) -> str` —
and the record types are defined once, in Python, in `python/yamluna/_record.py`. Rust never walks
a `CommentedMap`; Python never formats YAML text.

## Licence

MIT OR Apache-2.0. `crates/yamluna-scanner` is a fork of
[saphyr-parser](https://github.com/saphyr-rs/saphyr) 0.0.12 under the same terms; see
[FORK.md](crates/yamluna-scanner/FORK.md) for every change made to it.
