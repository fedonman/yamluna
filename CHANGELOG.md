# Changelog

Notable changes to `yamluna`. Format follows [Keep a Changelog](https://keepachangelog.com/1.1.0/);
versions follow [semver](https://semver.org/).

## [0.1.0] - 2026-09-01

First release.

### Changed

- **Tooling: `ruff` for linting and formatting, `ty` for type checking.** `mypy` is gone; it
  was configured but reported 109 errors and gated nothing. `ruff` now runs with
  `select = ["ALL"]` and both `ruff check` and `ruff format --check` are CI gates, as is
  `ty check` over the whole tree including the tests, the examples and the benchmark.
  Getting there took 6473 lint violations and 143 type diagnostics to zero. The visible
  half of that is the docstrings: Python summary lines are now imperative (`Return the
  byte offset`, not `Returns the byte offset`), every previously undocumented magic method,
  `__init__` and public method has a docstring, and `D417` holds every documented function
  to documenting all of its parameters. No pydocstyle `convention` is set, because both of
  the ones ruff ships would silently disable one of those two rules.

- **The scalar types carry real types.** `_Attr`, the descriptor behind `lexeme`, `anchor`,
  `caps`, `width` and the rest, is generic with typed `__get__` overloads, and the classes
  declare the private fields it reads. `HexInt(0x1F).caps` resolves as `bool` and
  `LiteralScalarString('x').lexeme()` as `str | None`, where before a checker saw nothing.

### Fixed

- **The recorded facts now cross the FFI.** `Entry::colon`, `Node::anchor_at`, `Node::tag_at`
  and `Document::line_space` were recorded by the loader and reproduced by the Rust emitter,
  but had no record slot, so a *Python* round trip threw away what the core had kept: at
  `8b05b39` the `yaml-test-suite` round trip scored 302/308 through
  `yamluna_core::{parse, emit}` and 202/308 through `YAML().load_all` / `.dump_all` (191/308
  through the single-document `load` / `dump`). They are now `Node.colon`, `Node.anchor_at`,
  `Node.tag_at`
  and `Doc.line_space` in `python/yamluna/_record.py`, opaque to the Python layer and handed
  back unchanged for any node it did not change. `tests/test_suite_roundtrip.py` is the new
  gate over the Python-side score, and `tests/suite_roundtrip.py` prints it with a diff per
  failing case, so the two numbers can never silently drift apart again.

- **Directive lines round-trip as written.** The whole region above `---`, meaning reserved
  directives (`%FOO`), the spacing inside `%YAML  1.1`, and any comment on or between those
  lines, is kept verbatim on `Document::directives_raw` and echoed, instead of being reconstructed from
  `version` / `tag_directives`. Nine `yaml-test-suite` cases.
- **White space at the end of a stream** that no line break closes is recorded on
  `Document::stream_tail` and written back, instead of being dropped (`4RWC`, `L24T-01`).
- **A flow collection's separation run may now cross a line** when nothing was taken out of it,
  so `{ "a" : b\n , c : 'd' }` keeps its leading-comma layout instead of being re-laid-out
  (`DFF7`, `FRK4`, `LP6E`, `WZ62`, and the last two corpus files).
- **A stored comment is never silently discarded.** A scalar-valued entry's `C_VALUE_POST`
  comment reached the document model and was then dropped by the emitter: the exact
  store-then-discard path D4 on the behaviour-differences list says this library does not have.
- Recorded flow runs are no longer echoed once the emitter has stopped landing where the model
  says: a document rebuilt from user-made nodes was getting punctuation for lexemes that were
  no longer being written, and could emit YAML that did not re-parse.
- A block-scalar header written onto a line a comment already owned (`# c` / `|`) swallowed the
  header and made the output unparseable.
- An empty node's `&anchor` and tag no longer swallow the `,` that follows them inside a flow
  collection (`{foo: !!str, bar: 1}` lost the comma).
- An `&anchor` or tag the source put on a line of its own keeps its own column (`BU8L`).
- An explicit `? key` whose `:` line the source wrote keeps it, even with an empty value
  (`KK5P`).

## [0.1.0] - unreleased

First release. A round-trip YAML library: Rust scanner, document model and emitter behind a
Python API that replaces `ruamel.yaml`'s `typ='rt'`.

### Added

- **`YAML(typ='rt')`** with `load`, `load_all`, `dump`, `dump_all`, the context-manager dump
  form, and the round-trip settings: `indent(mapping=, sequence=, offset=)`, `preserve_quotes`,
  `default_flow_style`, `width`, `explicit_start`, `explicit_end`, `allow_duplicate_keys`,
  `version`.
- **The ruamel object model**, subclassing the builtins: `CommentedMap(dict)`,
  `CommentedSeq(list)`, `CommentedSet`, `CommentedKeySeq`/`CommentedKeyMap`, `TaggedScalar`,
  the `str`/`int`/`float`/`bool` scalar subclasses, and per-node `.ca` / `.lc` / `.fa` /
  `.anchor` / `.tag` / `.merge`. `isinstance(x, dict)`, `json.dumps`, `copy.deepcopy` and
  `pickle` work unchanged.
- **A trivia model keyed by node identity, not by index.** A comment or a run of blank lines
  belongs to the node it describes, so `insert`, `del`, `pop`, `rename`, `move_to_end`,
  `reverse` and `sort` move comments with the entries they describe. `.ca.items` is a
  projection over that store, so ported code that reads `.ca` still works
  ([behaviour differences](https://qilimanjaro-tech.github.io/yamluna/migrating/differences/) A1–A7).
- **Blank lines as a first-class trivium with a count**, rather than bare newlines smuggled
  inside another node's comment text (A7, B9).
- **A tag registry keyed on the fully qualified class path**, so two libraries, or two
  modules of one library, that both define a `Circuit` cannot overwrite each other. The
  namespace is written into the document with `%TAG` directives; a colliding `(source, name)`
  pair promotes both sources to their full module paths. A bare `!Name` with two registered
  candidates raises rather than guessing
  ([the design contract](https://qilimanjaro-tech.github.io/yamluna/internals/) §5, C1–C2).
- **A per-`YAML()` registry.** `yaml.register_class(...)` never touches another instance's
  registry. A module-level `register_class` and a shared `default_registry` remain for the
  one-registry-per-app case.
- **`yamluna-scanner`**, a fork of `saphyr-parser` 0.0.12 that carries comments, block-vs-flow
  collection style, anchor *names*, and `%YAML`/`%TAG` directives through the event stream, plus
  four upstream bug fixes. Every change is logged in `crates/yamluna-scanner/FORK.md`; the
  402-case `yaml-test-suite` and the upstream unit tests stay green.
- **GIL-free parsing.** `_yamluna.parse` runs the scanner, loader and trivia attachment inside
  `py.detach`, so loads across threads overlap: measured 2.83x at 4 threads and 3.89x at 8 on
  the 249 KiB `nested` input, where `ruamel.yaml` is flat at 0.98x (`bench/bench.py --threads`).
  Only the parse is GIL-free; building the `Node` records and the `CommentedMap`s on top of
  them is Python object creation, so `YAML.load` itself scales to 2.45x at 8 threads, not
  3.89x.
- **abi3 wheels** (`cp311-abi3`), Python 3.11+.

### Fixed, relative to `ruamel.yaml` 0.19.1

Every entry is measured, with a repro and a regression test, in
[Behaviour differences](https://qilimanjaro-tech.github.io/yamluna/migrating/differences/).

- Comments no longer drift or resurrect across mutation (A1–A6), `.ca` is no longer mutated by
  dumping (A8), and `.ca.end` / `yaml_end_comment_extend` round-trip (A9).
- Nothing is silently dropped: anchors referenced fewer than twice (B1), `---`/`...` (B2),
  comments after `...` (B3), explicit `? key` (B4), sequence indentation (B5), `%YAML`/`%TAG`
  directives, the BOM, and comments inside flow collections (B10) all survive. A file that is
  only comments no longer comes back as zero bytes.
- Scalars are reproduced from their source lexeme rather than re-spelled: `1_000.5` (B7),
  `+12` (B8), `-0x1F` (D1), `0X1F` (D2), `|-2` (B11), and long lines are not refolded.
- `allow_duplicate_keys=True` warns naming both positions and lets the last value win (D5);
  `CommentedMap.copy()` no longer shares its `Comment` object with the original (D6);
  `.lc.key(k)` returns `None` instead of raising for a node with no recorded position (D7).

### Measured

On this commit, reproduce with the commands given:

| | |
| --- | --- |
| `tests/corpus/`, byte-identical round trip (`python tests/differential.py`) | yamluna 40/40, `ruamel.yaml` 0.19.1 3/40 (7/40 with `--seq-indent`; `key-duplicate` is scored on behaviour, not bytes) |
| `yaml-test-suite` round trip through the **Rust core** (`cargo test -p yamluna-core --test proptest_roundtrip`) | 308/308 |
| `yaml-test-suite` round trip through the **Python API** (`python tests/suite_roundtrip.py`) | 306/308 |
| `yaml-test-suite` conformance in the scanner fork (`cargo test -p yamluna-scanner --test yaml-test-suite`) | 402/402 |
| `cargo test --workspace` | 722 passed, 0 failed |
| `pytest tests` | 1097 passed, 45 skipped, 12 xfailed |
| load / dump throughput vs `ruamel.yaml` (`python bench/bench.py`, release build) | 1.4x–8.9x faster |

The two round-trip scores are separate on purpose. 308 is what the Rust core reproduces, every
case the suite has; 306 is what a user of `YAML().load_all` / `.dump_all` gets, and it is the
lower of the two. Quoting only the first would be quoting the core rather than the library. The
two cases between them are the object model, not the boundary: `emit(parse(x))` through the
`_record` classes is asserted byte-identical to `parse`-then-`emit` inside Rust for all 308
(`test_the_record_seam_loses_nothing_over_the_suite`).

### Known gaps

Each is pinned by a guard list or an xfail that fails if it starts passing. The full list, with
causes, is in [tests/README.md](tests/README.md#known-gaps).

- None through the Rust core: all 308 `yaml-test-suite` cases round-trip byte-for-byte and
  `KNOWN_GAPS` in `crates/yamluna-core/tests/proptest_roundtrip.rs` is empty. The last six
  closed by recording one more fact each: a `#` mark where a comment stood inside a flow
  collection's separation run and a run count that says a single pair wrote no brackets
  (`6HB6`, `7TMG`, `CN3R`, `CT4Q`), `Node::header_at` for a block scalar's header (`M5C3`),
  and `Document::directives_raw` widened to cover a `...` that ends no document (`M7A3`).
- 2 of the 308 do not round-trip through the Python API (`KNOWN_GAPS` in
  `tests/test_suite_roundtrip.py`), and both are permanent object-model limits rather than
  unfinished work: `2JQS` (`: a` over `: b`) and `X38W` (an alias used as a key of the mapping
  its anchor is defined in). `CommentedMap` is a `dict`, so two keys that compare equal, or
  one key reached twice, are one entry; YAML requires a mapping's keys to be unique, so both
  documents are ill-formed and `DuplicateKeyError` naming both positions is the answer. ruamel
  raises on both; PyYAML never reaches the question.
- One corpus file does not round-trip through the Python layer: `key-duplicate`
  (`CommentedMap` is a `dict`, so equal keys collapse). The Rust core and the FFI records both
  reproduce all 41 files, so `KNOWN_FAILURES` and `KNOWN_RECORD_GAPS` are empty.
- 12 xfails in `tests/test_mutation.py`: eight because a first child's own-line comment is filed
  on the enclosing collection's `inner` slot rather than the child's `before`, so an insertion
  at the front mislabels it; four because an insertion strands the `-` of the item before an
  own-line comment. The behaviour-differences entries A2–A6 carry the same caveat.
- Nothing is waived by rule for a whole directory that a file could have fixed. The
  `ruff` ignore list is six entries long and every one carries its reason, and the 120
  per-line `# noqa` waivers are dominated by the ruamel-compatible signatures that cannot
  change (`FBT001`, `FBT002`, `FBT003`, `PLR0913`, `PLR0917`).

### Not included, by design

`typ='rt'` only; no safe/base/unsafe; no `!!python/object:`; no component substitution; no
plug-ins; no `scan()`/`compose()`/`serialize()`; no legacy module-level `load()`/`dump()`.
See [Migrating from ruamel.yaml](https://qilimanjaro-tech.github.io/yamluna/migrating/)
for the workaround for each.

[0.1.0]: https://github.com/qilimanjaro-tech/yamluna/releases/tag/v0.1.0
