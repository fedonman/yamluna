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
| `ruamel.yaml` 0.19.1 | **3 of 41** |
| yamluna | **38 of 41** |

Point ruamel at the indentation style most of the corpus uses
(`yaml.indent(mapping=2, sequence=4, offset=2)`, i.e. `differential.py --seq-indent`) and it
manages 7 of 41. No single setting does better, because `struct-seq-indent.yaml` mixes two
indentations inside one document — which is what files written by humans do.

The three yamluna does not yet manage, and why:

| file | why |
|---|---|
| `flow-forms` | the FFI record classes have no slot for `flow_comma` / `flow_end` / `flow_bare_key`, so `[ 1 , 2 ]` and a trailing comma lose their punctuation crossing into Python. The Rust core keeps them; the pure-Rust round trip reproduces the file. |
| `key-duplicate` | `CommentedMap` is a `dict`, so two entries with equal keys collapse into one. The price of subclassing the builtins (`isinstance(x, dict)`, `json.dumps`, `deepcopy`, `==` all work for free). |
| `text-tabs` | the model records lexemes and positions but not the white space *between* two lexemes, so `[a<TAB>, b]` comes back `[a,  b]`. |

Each is pinned by a guard list that fails the suite if it starts passing, so a fix cannot leave a
stale excuse behind. `tests/README.md` has the per-file table and what ruamel does to each one.

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

## Layout

```
crates/yamluna-scanner/   forked saphyr-parser: comments, collection style, anchor names
crates/yamluna-core/      document model, loader, trivia attachment, emitter
crates/yamluna-py/        PyO3 boundary; releases the GIL around parse and emit
python/yamluna/           the API: YAML, CommentedMap/Seq, scalar types, registry, errors
tests/                    corpus, differential harness, the acceptance suite
```

The FFI boundary is flat and symmetric — `parse(str) -> list[Doc]`, `emit(list[Doc]) -> str` —
and the record types are defined once, in Python, in `python/yamluna/_record.py`. Rust never walks
a `CommentedMap`; Python never formats YAML text.

## Licence

MIT OR Apache-2.0. `crates/yamluna-scanner` is a fork of
[saphyr-parser](https://github.com/saphyr-rs/saphyr) 0.0.12 under the same terms; see
[FORK.md](crates/yamluna-scanner/FORK.md) for every change made to it.
