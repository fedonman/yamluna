# yamluna acceptance harness

This directory is the adjudicator. `docs/DESIGN.md` §6 defines acceptance; everything here
measures it. When a design question comes up ("should the emitter normalise this?"), the answer
is whichever choice keeps the corpus byte-identical.

```
corpus/                  41 hand-written YAML files, one concern each
conftest.py              corpus discovery + the single yamluna seam
differential.py          ruamel.yaml 0.19.1 and yamluna, scored side by side
test_roundtrip.py        DESIGN 6.2 end to end: dump(load(text)) == text
test_suite_roundtrip.py  the same, over yaml-test-suite, through the Python API
suite_roundtrip.py       that score interactively, with a diff per failing case
```

**Three round-trip scores, and they are not the same number.** Each measures a different
stack, so quoting one of them as "the" score is quoting the wrong thing:

<!-- scores: regenerate with the three commands in the right-hand column -->

| what round-trips | over | score | command |
|---|---|---|---|
| the Python API — `YAML().load` → `.dump` | `tests/corpus/` | **40 / 40** (of 41 files; `key-duplicate` is scored on behaviour, below) | `python tests/differential.py` |
| the Rust core — `parse` → `emit` | `yaml-test-suite` | **302 / 308** | `cargo test -p yamluna-core --test proptest_roundtrip` |
| the Python API — `YAML().load_all` → `.dump_all` | `yaml-test-suite` | **299 / 308** | `python tests/suite_roundtrip.py` |

The corpus is what the library is *for*; the suite is what YAML *is*. The gap between the
second and third rows is **three cases**, and as of this commit all three are the Python
object model (`2JQS`, `X38W`, `6KGN` below) rather than the FFI seam: the seam carries every
recorded fact, and `test_the_record_seam_loses_nothing_over_the_suite` is the gate that keeps
it that way over all 308. When a recorded fact stops crossing, that gate fails first and names
the case — which is the failure DESIGN §2.5 is about, and the one that cost 100 cases at
`8b05b39`.

## Running things

```bash
# both libraries over the whole corpus, as the table below
PYTHONPATH=python .venv/bin/python tests/differential.py

# ... plus a unified diff for every file yamluna changes
PYTHONPATH=python .venv/bin/python tests/differential.py --diff

# the same, scoring the notes column against ruamel instead
PYTHONPATH=python .venv/bin/python tests/differential.py --ruamel --diff

# one file, with its diff
PYTHONPATH=python .venv/bin/python tests/differential.py comment-eol

# ruamel again with the indentation style most of the corpus uses
PYTHONPATH=python .venv/bin/python tests/differential.py --seq-indent

# yaml-test-suite through the Python API: the score, and every failing case
PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py

# ... with in/out for each failure, or for one case
PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py --diff
PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py 26DV

# ... alongside the Rust core's score for the same 308 cases, to see the difference
PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py --rust

# the Python suite (needs `maturin develop --uv` first for the corpus tests)
PYTHONPATH=python .venv/bin/pytest tests -q

# the Rust side
cargo test --workspace                          # everything: 720 tests
cargo test -p yamluna-scanner --test yaml-test-suite   # the fork's 402 conformance cases
cargo test -p yamluna-core --test proptest_roundtrip -- --nocapture   # the suite round trip
```

`differential.py` exits non-zero if the corpus itself is malformed (not UTF-8, or a file whose
first line is not a `# covers:` comment), so it doubles as the corpus lint.

## What each layer asserts

| layer | assertion | where |
|---|---|---|
| `yamluna-scanner` | the upstream unit tests and all 402 `yaml-test-suite` cases stay green after every patch; `keep_comments(false)` is byte-identical to upstream | `cargo test -p yamluna-scanner` (585 tests, of which `--test yaml-test-suite` is the 402) |
| `yamluna-core` | for every corpus file, `load -> dump` with nothing mutated is **byte-identical to the input** (DESIGN 6.2) | Rust integration test over `tests/corpus` |
| `yamluna-py` | `emit(parse(text))` through the `_record` classes is byte-identical to `parse`-then-`emit` inside Rust: every record gap is named in `KNOWN_RECORD_GAPS`, with the field it needs | `tests/test_bindings.py` |
| `python/yamluna` | the object model, registry and error hierarchy, tested against hand-built record lists — no extension needed | `pytest tests` |
| end to end | `YAML().dump(YAML().load(text)) == text` for every corpus file, with every exception named in `KNOWN_LOSSES` | `tests/test_roundtrip.py` |
| end to end, wide | `YAML().dump_all(YAML().load_all(text)) == text` for all 308 `yaml-test-suite` cases, with every exception named in `KNOWN_GAPS` — the same cases the Rust harness scores, extracted the same way, so the two numbers subtract | `tests/test_suite_roundtrip.py` |
| the seam, wide | `emit(parse(text))` through the `_record` classes is byte-identical to `parse`-then-`emit` inside Rust for all 308 suite cases, so the difference between the two round-trip scores is the object model and nothing else | `tests/test_suite_roundtrip.py::test_the_record_seam_loses_nothing_over_the_suite` |
| differential | every divergence from ruamel is either a deliberate fix recorded in `docs/DIVERGENCES.md` or a defect in yamluna (DESIGN 6.3) | `differential.py` |
| mutation | comments stay attached to the right node across `insert`, `del`, `pop`, `move_to_end` and key rename (DESIGN 6.4) | `pytest tests` |

## The corpus

One concern per file; each file's first line says what it covers, so `head -3 corpus/*.yaml` is
the index. Files are read as bytes and decoded UTF-8 with nothing normalised — `text-bom.yaml`
keeps its BOM, `text-crlf.yaml` keeps its CRLF, `comment-eof-no-newline.yaml` has no final
newline — because those are exactly the bytes that go missing in a round trip.

Two things the corpus deliberately does **not** contain:

- **Tabs as block-context separation.** `key:<TAB>value`, `-<TAB>item`, `a<TAB>b` inside a plain
  scalar and a tab before an end-of-line comment are all legal per YAML 1.2 (`s-white` includes
  TAB) and every libyaml-derived parser rejects them, ruamel 0.19.1 included. Measured, not
  assumed; the finding is written up in `corpus/text-tabs.yaml`, which keeps only the tab
  positions that actually parse. Matching libyaml here is a compatibility decision yamluna
  should make on purpose.
- **An alias before its anchor.** There is no legal ordering to test — an alias may only refer to
  an anchor earlier in the stream — so `corpus/anchors-aliases.yaml` covers every *other*
  ordering instead, including anchor-then-tag and tag-then-anchor.

### Writing yamluna tests against it

Take `corpus_path`, `corpus_bytes` or `corpus_text` and the test is parametrised over all 41
files automatically, with the file stem as the test id (`pytest -k comment-eol`). The yamluna
side is one fixture, `yamluna_roundtrip`, which skips until the extension is built; filling it in
turns the corpus into the DESIGN 6.2 acceptance run without touching a single test.

```python
def test_roundtrip_is_byte_identical(corpus_text, yamluna_roundtrip):
    assert yamluna_roundtrip(corpus_text) == corpus_text
```

That is `test_roundtrip.py`, which also asserts the *other* direction for every file it
cannot round-trip: a file listed in `KNOWN_LOSSES` that starts passing fails the suite, so a
fix can never leave a stale excuse behind.

## Measured: yamluna vs ruamel.yaml 0.19.1 over this corpus

Both libraries get the ordinary round-trip recipe — `YAML()` (`typ='rt'`),
`preserve_quotes = True`, everything else default, including `width = 80` and
`allow_duplicate_keys = False`. Regenerate with
`PYTHONPATH=python .venv/bin/python tests/differential.py`.

| | round-trips byte-identically |
|---|---|
| **ruamel.yaml 0.19.1** | **3 of 40** |
| **yamluna** | **40 of 40** |

**40, not 41 — `key-duplicate.yaml` is scored on behaviour instead.** That file deliberately
holds `a: 1 ... a: 3`, and a mapping keeps one of two equal keys, so *no* dict-backed API can
write those bytes back: "does it round-trip" is a question neither library has a yes available
for, and the harness used to record that as a yamluna round-trip failure — a real result about
`dict`, dressed up as a defect in the emitter. What the file actually specifies is behaviour:
refuse duplicates by default, and when told to allow them, say so rather than lose data
silently, and keep the last of each pair (DESIGN 2.3). `differential.py` measures those three
(`check_duplicate_keys`, `BEHAVIOUR_ONLY`) and prints them in their own table:

<!-- generated by: PYTHONPATH=python .venv/bin/python tests/differential.py -->

| corpus file       | library | as specified | what it does |
| ----------------- | ------- | ------------ | ------------ |
| `key-duplicate`   | ruamel  | **no**       | raises DuplicateKeyError by default; when allowed, warns nothing and the first key wins |
| `key-duplicate`   | yamluna | yes          | raises DuplicateKeyError by default; when allowed, warns DuplicateKeyFutureWarning and the last key wins |

So the file is not a tie: ruamel refuses it correctly, then — once told to allow duplicates —
drops four values with no warning at all and resolves each pair to the *first* key, which is
neither what a `dict` literal does nor what any other YAML implementation does.

Point ruamel at the indentation style most of the corpus is written in
(`yaml.indent(mapping=2, sequence=4, offset=2)`, i.e. `differential.py --seq-indent`) and it
manages **7 of 40** — `blank-lines`, `comment-block-boundaries`, `comment-eol` and
`comment-own-line` join its passing set; yamluna is unaffected, because it reproduces each
node's own layout rather than applying one global indentation. That is also why no single
setting can fix `struct-seq-indent.yaml` for ruamel: the file mixes indentations within one
document.

The notes column is what **yamluna** changed; run with `--ruamel` for ruamel's, which is
reproduced further down.

<!-- generated by: PYTHONPATH=python .venv/bin/python tests/differential.py -->

| corpus file                   | ruamel | yamluna | what yamluna changed |
| ----------------------------- | ------ | ------- | -------------------- |
| `anchors-aliases`             | **no** | yes     |  |
| `anchors-merge`               | **no** | yes     |  |
| `anchors-recursive`           | **no** | yes     |  |
| `blank-lines`                 | **no** | yes     |  |
| `block-scalar-chomping`       | yes    | yes     |  |
| `block-scalar-indent`         | **no** | yes     |  |
| `comment-anchors`             | **no** | yes     |  |
| `comment-block-boundaries`    | **no** | yes     |  |
| `comment-block-scalar-header` | yes    | yes     |  |
| `comment-doc-markers`         | **no** | yes     |  |
| `comment-eof-no-newline`      | **no** | yes     |  |
| `comment-eol`                 | **no** | yes     |  |
| `comment-flow`                | **no** | yes     |  |
| `comment-only`                | **no** | yes     |  |
| `comment-own-line`            | **no** | yes     |  |
| `comment-top-of-file`         | yes    | yes     |  |
| `directive-multiple-tags`     | **no** | yes     |  |
| `directive-per-document`      | **no** | yes     |  |
| `directive-tag`               | **no** | yes     |  |
| `directive-yaml-version`      | **no** | yes     |  |
| `doc-multi-explicit`          | **no** | yes     |  |
| `doc-multi-implicit`          | **no** | yes     |  |
| `flow-forms`                  | **no** | yes     |  |
| `flow-nesting`                | **no** | yes     |  |
| `key-complex`                 | **no** | yes     |  |
| `scalar-binary`               | **no** | yes     |  |
| `scalar-core-schema`          | **no** | yes     |  |
| `scalar-escapes`              | **no** | yes     |  |
| `scalar-long-lines`           | **no** | yes     |  |
| `scalar-styles`               | **no** | yes     |  |
| `scalar-timestamps`           | **no** | yes     |  |
| `struct-deep-nesting`         | **no** | yes     |  |
| `struct-empty`                | **no** | yes     |  |
| `struct-seq-indent`           | **no** | yes     |  |
| `tag-local-global`            | **no** | yes     |  |
| `tag-unregistered`            | **no** | yes     |  |
| `text-bom`                    | **no** | yes     |  |
| `text-crlf`                   | **no** | yes     |  |
| `text-tabs`                   | **no** | yes     |  |
| `text-unicode`                | **no** | yes     |  |

### Known gaps

Everything that does not pass, with its cause and the guard that will notice when it stops
failing. Nothing on this list is silent: each entry fails the suite if it starts passing.

#### The corpus — 1 of 41

| file | cause | pinned by |
|---|---|---|
| `key-duplicate` | `CommentedMap` is a `dict`, so two entries with equal keys collapse into one; the bytes cannot come back. Not an emitter defect, and not on the Rust lists — `yamluna-core` and the FFI records both reproduce the file. | `KNOWN_LOSSES` (`tests/test_roundtrip.py`) |

`text-tabs` and `flow-forms` were the last two to close: both needed the separation a flow
collection's source wrote *between* its lexemes, which is one field — `Node.flow_seps`, carried
across the FFI by the record slot of the same name (DESIGN §2.5). `KNOWN_FAILURES`
(`crates/yamluna-core/tests/roundtrip.rs`) and `KNOWN_RECORD_GAPS` (`tests/test_bindings.py`)
are both empty as a result.

#### `yaml-test-suite` through the Rust core — 6 of 308

`cargo test -p yamluna-core --test proptest_roundtrip` runs `parse → emit` over every suite
case; these six do not come back byte-identical. Each is a real defect with a minimal repro,
pinned by `KNOWN_GAPS` in that file, which fails if one starts passing.

| case | cause |
|---|---|
| `6HB6` | an end-of-line comment inside a flow collection is written from a trivia slot, so the separation run around it cannot be echoed and the comment lands a line low |
| `7TMG` | a `,` the source wrote *after* an own-line comment inside a flow collection is re-emitted before it: the run holding the comma is split by trivia written from a slot |
| `CN3R` | an anchored single-pair mapping inside a flow sequence (`&c c: d`) is re-emitted with braces the source did not write |
| `CT4Q` | an explicit `? key` inside a flow collection loses its `?`: `Entry::explicit` is recorded but the flow emitter never writes the indicator |
| `M5C3` | a block-scalar header the source put on a line of its own below the node's tag is pulled up onto the tag's line; the header has no recorded position of its own |
| `M7A3` | a `...` that ends a document with no content at all is not a parser event and has no document of its own to hang on, so it is dropped |

The first four are one cluster: everything a flow collection wrote between its lexemes is one
`String` per gap with the comments taken out of it, so a run that a comment splits cannot be put
back around that comment (DESIGN §2.5, consequence 2). The other two each need one recorded
position the model does not carry.

#### `yaml-test-suite` through the Python API — 9 of 308

`python tests/suite_roundtrip.py` runs `YAML().load_all → .dump_all` over the same 308 cases;
`tests/test_suite_roundtrip.py` is the gate over the same list, with the same excuses in its own
`KNOWN_GAPS`. **Six of the nine are the six above** — the core loses them first, so the API
cannot get them back. The other three are the Python side's own, and all three are the same
trade: `CommentedMap` is a `dict` and `CommentedSeq` is a `list` (DESIGN §4.1), so two objects
that compare equal are one key, and `None` is a singleton with no identity to hang an anchor on.

| case | cause |
|---|---|
| `6HB6`, `7TMG`, `CN3R`, `CT4Q`, `M5C3`, `M7A3` | the six core gaps above, unchanged — each is marked `(core)` in `KNOWN_GAPS` |
| `2JQS` — `': a\n: b\n'` | two entries with an empty key are two entries with the *same* key, so the constructor raises `DuplicateKeyError` where the core keeps both. The `key-duplicate` wall, reached through `null` |
| `X38W` — `'{ &a [a, &b b]: *b, *a : [c, *b, d]}'` | an alias used as a key of the mapping its own anchor is defined in is the *same object* as that key, so the mapping has a duplicate key and raises. The same wall, reached through an alias |
| `6KGN` — `'a: &anchor\nb: *anchor\n'` | an anchor on a null survives on the parent, but an alias to it constructs to the one `None` singleton, which carries no identity to alias on, so `b: *anchor` comes back `b:` |

Buying these three back means not subclassing `dict` and `list`, which costs `isinstance(x,
dict)`, `json.dumps`, `deepcopy`, `pickle` and `==` — the trade DESIGN §4.1 makes on purpose.
The core keeps all three; only the object model cannot represent them.

#### Mutation — 12 xfails, all in `tests/test_mutation.py`

Two causes, and both are model defects rather than test debt:

| xfails | cause |
|---|---|
| 8 — `test_a2_insert_at_the_front`, `test_a3_deleting_the_first_item`, `test_a4_deleting_the_first_key`, `test_a5_move_to_end`, `test_a5_move_to_front`, `test_a6_reverse`, `test_map_clear`, `test_seq_clear` | `loader.rs::take_before` files a first child's own-line comment on the enclosing collection's `inner` slot rather than on the child's `before`, so an insertion at the front labels the new element with the old one's comment. Every byte still round-trips; only the ownership is wrong, and only for the first child. DESIGN §2.2 rule 2 requires `before` and says so; DIVERGENCES A2–A6 carry the same caveat, each with its measured output. |
| 4 — `test_a2_insert_in_the_middle`, `test_no_mutation_strands_a_dash_from_its_value[seq-insert-front / -middle / -slice-set]` | after an insertion the emitter strands the `-` of the item preceding an own-line comment: `-\n  value` where the source wrote `- value` |

#### Skips — 45, and none of them is a gap

`pytest tests -q` skips 45, every one a guard declining a case that does not apply:

| count | skip |
|---|---|
| 40 | `test_roundtrip.py:72` "not a known loss" — the "a known loss still fails" test declining the 40 files that are not known losses |
| 1 | `test_roundtrip.py:63` "known loss" — `key-duplicate`, the one file on `KNOWN_LOSSES` |
| 2 | `test_roundtrip.py:94`, `:110` "does not load" — `key-duplicate` again, which refuses to load at all under the default `allow_duplicate_keys=False` |
| 2 | `test_api.py:308`, `:316` "the extension is built" — the two branches that test the *unbuilt* extension's error messages |


## What ruamel.yaml 0.19.1 does to this corpus

Same run, with the notes column scored against ruamel
(`PYTHONPATH=python .venv/bin/python tests/differential.py --ruamel`).

<!-- generated by: PYTHONPATH=python .venv/bin/python tests/differential.py --ruamel -->

| corpus file                   | ruamel | yamluna | what ruamel changed |
| ----------------------------- | ------ | ------- | ------------------- |
| `anchors-aliases`             | **no** | yes     | lost 3 anchor(s); re-indented block sequences; warns ReusedAnchorWarning |
| `anchors-merge`               | **no** | yes     | moved 1 end-of-line comment(s) onto their own line; 29 lines -> 30 |
| `anchors-recursive`           | **no** | yes     | lost 4 anchor(s); lost 4 alias(s); re-indented block sequences |
| `blank-lines`                 | **no** | yes     | re-indented block sequences; line 19: `  - a` -> `- a` |
| `block-scalar-chomping`       | yes    | yes     |  |
| `block-scalar-indent`         | **no** | yes     | line 9: `...p_then_explicit: \|-2` -> `...p_then_explicit: \|2-` |
| `comment-anchors`             | **no** | yes     | moved 4 end-of-line comment(s) onto their own line; re-indented block sequences; 14 lines -> 18 |
| `comment-block-boundaries`    | **no** | yes     | re-indented block sequences; line 6: `    - a` -> `  - a` |
| `comment-block-scalar-header` | yes    | yes     |  |
| `comment-doc-markers`         | **no** | yes     | lost 2 own-line comment(s); lost 3 end-of-line comment(s); added 1 `---` document start; dropped 1 `...` document end; 10 lines -> 5 |
| `comment-eof-no-newline`      | **no** | yes     | appended a final newline; 5 lines -> 6 |
| `comment-eol`                 | **no** | yes     | re-indented block sequences; line 13: `  - one                  # after a sequence ...` -> `- one                    # after a sequence ...` |
| `comment-flow`                | **no** | yes     | lost 2 own-line comment(s); lost 6 end-of-line comment(s); 22 lines -> 8 |
| `comment-only`                | **no** | yes     | lost 6 own-line comment(s); blank lines 1 -> 0; removed the final newline; 7 lines -> 0 |
| `comment-own-line`            | **no** | yes     | re-indented block sequences; line 11: `  - a` -> `- a` |
| `comment-top-of-file`         | yes    | yes     |  |
| `directive-multiple-tags`     | **no** | yes     | lost 5 own-line comment(s); dropped 2 %TAG directive(s); 14 lines -> 9 |
| `directive-per-document`      | **no** | yes     | lost 3 own-line comment(s); dropped 2 `...` document end; dropped 1 %YAML directive(s); added 1 %TAG directive(s); 14 lines -> 9 |
| `directive-tag`               | **no** | yes     | lost 2 own-line comment(s); dropped 1 %TAG directive(s); 9 lines -> 7 |
| `directive-yaml-version`      | **no** | yes     | lost 2 own-line comment(s); dropped 1 %YAML directive(s); re-indented block sequences; 8 lines -> 6 |
| `doc-multi-explicit`          | **no** | yes     | lost 3 own-line comment(s); dropped 2 `---` document start; dropped 3 `...` document end; 18 lines -> 10 |
| `doc-multi-implicit`          | **no** | yes     | dropped 1 `---` document start; 15 lines -> 14 |
| `flow-forms`                  | **no** | yes     | 26 lines -> 19; line 7: `..._nested: [{}, [], {a: []}]` -> `..._nested: [{}, [], a: []]` |
| `flow-nesting`                | **no** | yes     | dropped 2 `---` document start; re-indented block sequences; 28 lines -> 21 |
| `key-complex`                 | **no** | yes     | moved 1 end-of-line comment(s) onto their own line; 30 lines -> 20 |
| `scalar-binary`               | **no** | yes     | 11 lines -> 12; line 5: `...XvPz7Y6OjuDg4J+fn5` -> `...XvPz7Y6OjuDg4J+fn5OTk6enp56enmlp` |
| `scalar-core-schema`          | **no** | yes     | line 9: `int_positive: +7` -> `int_positive: 7` |
| `scalar-escapes`              | **no** | yes     | blank lines 1 -> 0; 23 lines -> 17 |
| `scalar-long-lines`           | **no** | yes     | 13 lines -> 23; refolded long lines |
| `scalar-styles`               | **no** | yes     | 20 lines -> 19; line 17: `... this plain scalar` -> `... this plain scalar runs onto a second line` |
| `scalar-timestamps`           | **no** | yes     | re-indented block sequences; line 4: `...1-12-15T02:59:43.1Z` -> `...1-12-15T02:59:43.100000Z` |
| `struct-deep-nesting`         | **no** | yes     | re-indented block sequences; line 17: `  - - - - - - - - deep leaf` -> `- - - - - - - - deep leaf` |
| `struct-empty`                | **no** | yes     | lost 4 own-line comment(s); dropped 5 `---` document start; added 1 `...` document end; 21 lines -> 16 |
| `struct-seq-indent`           | **no** | yes     | re-indented block sequences; line 9: `  - one` -> `- one` |
| `tag-local-global`            | **no** | yes     | re-indented block sequences; 32 lines -> 31 |
| `tag-unregistered`            | **no** | yes     | lost 4 own-line comment(s); dropped 1 %TAG directive(s); re-indented block sequences; 17 lines -> 13 |
| `text-bom`                    | **no** | yes     | dropped the BOM; moved 1 end-of-line comment(s) onto their own line; re-indented block sequences |
| `text-crlf`                   | **no** | yes     | re-indented block sequences; line 5: `key: value<CR>` -> `key: value` |
| `text-tabs`                   | **no** | yes     | line 12: `...and a literal tab <TAB> here"` -> `...and a literal tab \t here"` |
| `text-unicode`                | **no** | yes     | re-indented block sequences; line 18: `quoted_escape: "\u00e9 \U0001F600"` -> `quoted_escape: "é 😀"` |

### Reading the table

`<CR>`, `<TAB>`, `<BOM>` and `<NBSP>` are markers for literal characters, so a literal tab does
not look like the two-character escape `\t`.

The "no" rows are the list of places where being bug-compatible with ruamel is the wrong goal.
Grouped by what the fix costs:

1. **Trivia that is simply dropped.** Directives (`%YAML`, `%TAG` — and the multi-`%TAG` file
   loses two of three), document markers (`---`, `...`, and `struct-empty` loses five), the BOM,
   CRLF, the missing final newline, and comments inside flow collections and around document
   markers. `comment-only.yaml` — a file that is nothing but comments — comes back as zero
   bytes. These are the DESIGN 2.1 / 2.2 / 2.3 slots existing precisely because ruamel has
   nowhere to put them.
2. **Trivia that is kept but moved.** End-of-line comments demoted onto their own line
   (`anchors-merge`, `comment-anchors`, `key-complex`, `text-bom`), which is the DESIGN 2.1
   `Trivia::Comment { own_line, col }` distinction.
3. **Layout re-decided rather than reproduced.** Sequence re-indentation, flow collections
   collapsed onto one line, multi-line plain and quoted scalars joined, long lines refolded at
   `width`, `?`-form keys rewritten as simple keys. DESIGN 2.4's invariant — reproduce `raw`
   verbatim for any node the user did not touch — is the whole answer to this group.
4. **Scalars re-spelled from the parsed value.** `+7` -> `7`, `\x41\x42` -> `AB`,
   `2001-12-15T02:59:43.1Z` -> `...43.100000Z`, `!!int "42"` -> `42`, `"é \U0001F600"` ->
   the literal characters, `|-2` -> `|2-`, base64 rewrapped at a different width, a literal tab
   re-escaped. This is why `Node` carries both `value` and `raw` (DESIGN 2).
5. **Structural loss.** `anchors-aliases` and `anchors-recursive` lose anchors and aliases
   outright — the recursive file loses all four of each, because ruamel resolves an alias by
   cloning the target (DESIGN 2.3). `key-duplicate` raises `DuplicateKeyError` rather than
   reporting the duplicates and letting the caller decide (DESIGN 2.3, 4.2).

Every one of these is a yamluna requirement, not a nice-to-have: DESIGN 6.2 admits no
exceptions, so the target is 40 of 40, plus a `key-duplicate` row that behaves. Groups 1 to 4
are done — yamluna keeps every directive, marker, BOM, CRLF and comment, including in the two
files that are nothing but comments and empty documents, never moves an end-of-line comment,
never re-indents or refolds an untouched node, and never re-spells a scalar it did not touch.
Group 5 is done for anchors and aliases, which stay aliases rather than being cloned;
duplicate keys are reported rather than raised on by default, but still cannot be *represented* while
`CommentedMap` is a `dict` — the one remaining wall, and the reason `key-duplicate` is
scored on behaviour rather than on bytes.
