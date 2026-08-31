# yamluna acceptance harness

This directory is the adjudicator. `docs/DESIGN.md` §6 defines acceptance; everything here
measures it. When a design question comes up ("should the emitter normalise this?"), the answer
is whichever choice keeps the corpus byte-identical.

```
corpus/            41 hand-written YAML files, one concern each
conftest.py        corpus discovery + the single yamluna seam
differential.py    ruamel.yaml 0.19.1 and yamluna, scored side by side
test_roundtrip.py  DESIGN 6.2 end to end: dump(load(text)) == text
```

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

# the Python suite (needs `maturin develop --uv` first for the corpus tests)
PYTHONPATH=python .venv/bin/pytest tests -q

# the Rust side
cargo test --workspace                       # core + emitter unit tests
cargo test -p yamluna-scanner                # upstream tests + yaml-test-suite
```

`differential.py` exits non-zero if the corpus itself is malformed (not UTF-8, or a file whose
first line is not a `# covers:` comment), so it doubles as the corpus lint.

## What each layer asserts

| layer | assertion | where |
|---|---|---|
| `yamluna-scanner` | the upstream unit tests and all 402 `yaml-test-suite` cases stay green after every patch; `keep_comments(false)` is byte-identical to upstream | `cargo test -p yamluna-scanner` |
| `yamluna-core` | for every corpus file, `load -> dump` with nothing mutated is **byte-identical to the input** (DESIGN 6.2) | Rust integration test over `tests/corpus` |
| `yamluna-py` | `emit(parse(text))` through the `_record` classes is byte-identical to `parse`-then-`emit` inside Rust: the records lose nothing (`KNOWN_RECORD_GAPS` is empty) | `tests/test_bindings.py` |
| `python/yamluna` | the object model, registry and error hierarchy, tested against hand-built record lists — no extension needed | `pytest tests` |
| end to end | `YAML().dump(YAML().load(text)) == text` for every corpus file, with every exception named in `KNOWN_LOSSES` | `tests/test_roundtrip.py` |
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
| **ruamel.yaml 0.19.1** | **3 of 41** |
| **yamluna** | **29 of 41** |

Point ruamel at the indentation style most of the corpus is written in
(`yaml.indent(mapping=2, sequence=4, offset=2)`, i.e. `differential.py --seq-indent`) and it
manages **7 of 41** — `blank-lines`, `comment-block-boundaries`, `comment-eol` and
`comment-own-line` join its passing set; yamluna is unaffected, because it reproduces each
node's own layout rather than applying one global indentation. That is also why no single
setting can fix `struct-seq-indent.yaml` for ruamel: the file mixes indentations within one
document.

The notes column is what **yamluna** changed; run with `--ruamel` for ruamel's, which is
reproduced further down.

<!-- generated by: PYTHONPATH=python .venv/bin/python tests/differential.py -->

| corpus file                   | ruamel | yamluna | what yamluna changed |
| ----------------------------- | ------ | ------- | -------------------- |
| `anchors-aliases`             | **no** | **no**  | lost 1 anchor(s); line 25: `tag_then_anchor: !!str &ta tagged first` -> `tag_then_anchor: &ta       tagged first` |
| `anchors-merge`               | **no** | yes     |  |
| `anchors-recursive`           | **no** | yes     |  |
| `blank-lines`                 | **no** | yes     |  |
| `block-scalar-chomping`       | yes    | yes     |  |
| `block-scalar-indent`         | **no** | **no**  | blank lines 1 -> 0; 21 lines -> 20 |
| `comment-anchors`             | **no** | yes     |  |
| `comment-block-boundaries`    | **no** | yes     |  |
| `comment-block-scalar-header` | yes    | yes     |  |
| `comment-doc-markers`         | **no** | yes     |  |
| `comment-eof-no-newline`      | **no** | yes     |  |
| `comment-eol`                 | **no** | yes     |  |
| `comment-flow`                | **no** | **no**  | 22 lines -> 19; line 9: `flow_map: {` -> `flow_map:` |
| `comment-only`                | **no** | **no**  | lost 6 own-line comment(s); blank lines 1 -> 0; 7 lines -> 1 |
| `comment-own-line`            | **no** | yes     |  |
| `comment-top-of-file`         | yes    | yes     |  |
| `directive-multiple-tags`     | **no** | yes     |  |
| `directive-per-document`      | **no** | **no**  | line 8: `%TAG ! tag:second/` -> `%YAML 1.2` |
| `directive-tag`               | **no** | yes     |  |
| `directive-yaml-version`      | **no** | yes     |  |
| `doc-multi-explicit`          | **no** | yes     |  |
| `doc-multi-implicit`          | **no** | yes     |  |
| `flow-forms`                  | **no** | **no**  | line 18: `  b: 2` -> `  b: 2,` |
| `flow-nesting`                | **no** | yes     |  |
| `key-complex`                 | **no** | yes     |  |
| `key-duplicate`               | **no** | **no**  | raises DuplicateKeyError: found duplicate key 'a' first at line 6, column 1, again at line 8, column 1 |
| `scalar-binary`               | **no** | **no**  | 11 lines -> 8; line 4: `picture: !!binary \|` -> `picture:` |
| `scalar-core-schema`          | **no** | **no**  | line 30: `null_tilde: ~` -> `null_tilde:` |
| `scalar-escapes`              | **no** | yes     |  |
| `scalar-long-lines`           | **no** | yes     |  |
| `scalar-styles`               | **no** | yes     |  |
| `scalar-timestamps`           | **no** | yes     |  |
| `struct-deep-nesting`         | **no** | yes     |  |
| `struct-empty`                | **no** | **no**  | lost 4 own-line comment(s); dropped 5 `---` document start; 21 lines -> 15 |
| `struct-seq-indent`           | **no** | yes     |  |
| `tag-local-global`            | **no** | **no**  | line 4: `std_str: !!str 123` -> `std_str:       '123'` |
| `tag-unregistered`            | **no** | yes     |  |
| `text-bom`                    | **no** | yes     |  |
| `text-crlf`                   | **no** | yes     |  |
| `text-tabs`                   | **no** | **no**  | raises ScannerError: ':' must be followed by a valid YAML whitespace |
| `text-unicode`                | **no** | yes     |  |

### The 12 yamluna still loses

Every one is in `KNOWN_LOSSES` in `test_roundtrip.py` with the same reasons, and they fall
into three groups.

**The Python object model cannot hold the fact** (7 files). These are the price of DESIGN 4.1
— the types subclass the builtins, so `isinstance(x, dict)`, `json.dumps`, `copy.deepcopy`
and `==` all work for free, and what a builtin cannot carry is lost.

- `comment-only`, `struct-empty` — a document with no root loads as `None`, and `None` carries
  nothing. A file that is *only* comments comes back as `null`. (These two are also the only
  files where a comment is lost at all; every other file keeps all of them.)
- `scalar-core-schema`, part of `struct-empty` — `None` is not subclassable, so a null cannot
  remember whether it was written `~`, `null`, `NULL` or `Null`. The empty spelling (`key:`)
  is the one that round-trips.
- `key-duplicate` — `CommentedMap` is a `dict`; two entries with equal keys collapse into one.
- `scalar-binary` — `!!binary` constructs `bytes`, which cannot hold the `|` block form the
  payload was written in.
- `tag-local-global` — a standard tag on a *scalar* (`!!str 123`, `!!int "42"`) is applied and
  then dropped: the value becomes a plain `str`/`int`, which has nowhere to keep `.tag`.
  Container tags (`!!seq`, `!!set`, `!!map`) and every unregistered tag survive.
- `comment-flow`, `block-scalar-indent` — `.ca`'s shape, not the record's: `ca.comment[1]` is
  one list holding what the records keep in two slots (`before` and `inner`), and a scalar
  value's `.ca` record has one slot for the trivia on both sides of it. So an own-line comment
  *inside* a flow collection comes back *above* it, and the blank first line of a `|+2` body
  comes back after the scalar. Both are recoverable by giving the store its own slots; both
  are a `.ca` compatibility decision, not a model gap.

**The document model does not record it** (3 files) — the pure-Rust round trip loses these
too, and they are listed in `KNOWN_FAILURES` in `crates/yamluna-core/tests/roundtrip.rs`:

- `anchors-aliases` — a node records an anchor and a tag but not which came first, so
  `!!str &ta v` and `&at !!str v` cannot both come back.
- `directive-per-document` — a document records `%YAML` and `%TAG` but not their order.
- `flow-forms` — a flow collection records where its items start, not its separators, so
  `[ 1 , 2 ]` and a trailing comma cannot come back.

**The scanner refuses the file** (1 file):

- `text-tabs` — the fork's `:`-in-flow check only accepts a space, so `{a:<TAB>b}` does not
  load. TAB is `s-white`, so the spec allows it and ruamel loads it. Tracked in
  `KNOWN_SCANNER_DEFECTS` in `crates/yamluna-core/tests/corpus.rs`.

Two of the three layers below the Python API do better than the table above, and the gap is
exactly what the object model costs:

| layer | round-trips |
|---|---|
| `yamluna-core` (Rust `parse` -> `emit`) | 37 of 41 |
| the FFI records (`emit(parse(text))` through `_record`) | 37 of 41 |
| `YAML().dump(YAML().load(text))` | 29 of 41 |

## What ruamel.yaml 0.19.1 does to this corpus

Same run, with the notes column scored against ruamel
(`PYTHONPATH=python .venv/bin/python tests/differential.py --ruamel`).

<!-- generated by: PYTHONPATH=python .venv/bin/python tests/differential.py --ruamel -->

| corpus file                   | ruamel | yamluna | what ruamel changed |
| ----------------------------- | ------ | ------- | ------------------- |
| `anchors-aliases`             | **no** | **no**  | lost 3 anchor(s); re-indented block sequences; warns ReusedAnchorWarning |
| `anchors-merge`               | **no** | yes     | moved 1 end-of-line comment(s) onto their own line; 29 lines -> 30 |
| `anchors-recursive`           | **no** | yes     | lost 4 anchor(s); lost 4 alias(s); re-indented block sequences |
| `blank-lines`                 | **no** | yes     | re-indented block sequences; line 19: `  - a` -> `- a` |
| `block-scalar-chomping`       | yes    | yes     |  |
| `block-scalar-indent`         | **no** | **no**  | line 9: `...p_then_explicit: \|-2` -> `...p_then_explicit: \|2-` |
| `comment-anchors`             | **no** | yes     | moved 4 end-of-line comment(s) onto their own line; re-indented block sequences; 14 lines -> 18 |
| `comment-block-boundaries`    | **no** | yes     | re-indented block sequences; line 6: `    - a` -> `  - a` |
| `comment-block-scalar-header` | yes    | yes     |  |
| `comment-doc-markers`         | **no** | yes     | lost 2 own-line comment(s); lost 3 end-of-line comment(s); added 1 `---` document start; dropped 1 `...` document end; 10 lines -> 5 |
| `comment-eof-no-newline`      | **no** | yes     | appended a final newline; 5 lines -> 6 |
| `comment-eol`                 | **no** | yes     | re-indented block sequences; line 13: `  - one                  # after a sequence ...` -> `- one                    # after a sequence ...` |
| `comment-flow`                | **no** | **no**  | lost 2 own-line comment(s); lost 6 end-of-line comment(s); 22 lines -> 8 |
| `comment-only`                | **no** | **no**  | lost 6 own-line comment(s); blank lines 1 -> 0; removed the final newline; 7 lines -> 0 |
| `comment-own-line`            | **no** | yes     | re-indented block sequences; line 11: `  - a` -> `- a` |
| `comment-top-of-file`         | yes    | yes     |  |
| `directive-multiple-tags`     | **no** | yes     | lost 5 own-line comment(s); dropped 2 %TAG directive(s); 14 lines -> 9 |
| `directive-per-document`      | **no** | **no**  | lost 3 own-line comment(s); dropped 2 `...` document end; dropped 1 %YAML directive(s); added 1 %TAG directive(s); 14 lines -> 9 |
| `directive-tag`               | **no** | yes     | lost 2 own-line comment(s); dropped 1 %TAG directive(s); 9 lines -> 7 |
| `directive-yaml-version`      | **no** | yes     | lost 2 own-line comment(s); dropped 1 %YAML directive(s); re-indented block sequences; 8 lines -> 6 |
| `doc-multi-explicit`          | **no** | yes     | lost 3 own-line comment(s); dropped 2 `---` document start; dropped 3 `...` document end; 18 lines -> 10 |
| `doc-multi-implicit`          | **no** | yes     | dropped 1 `---` document start; 15 lines -> 14 |
| `flow-forms`                  | **no** | **no**  | 26 lines -> 19; line 7: `..._nested: [{}, [], {a: []}]` -> `..._nested: [{}, [], a: []]` |
| `flow-nesting`                | **no** | yes     | dropped 2 `---` document start; re-indented block sequences; 28 lines -> 21 |
| `key-complex`                 | **no** | yes     | moved 1 end-of-line comment(s) onto their own line; 30 lines -> 20 |
| `key-duplicate`               | **no** | **no**  | raises DuplicateKeyError: while constructing a mapping |
| `scalar-binary`               | **no** | **no**  | 11 lines -> 12; line 5: `...eXvPz7Y6OjuDg4J+fn5` -> `...eXvPz7Y6OjuDg4J+fn5OTk6enp56enmlp` |
| `scalar-core-schema`          | **no** | **no**  | line 9: `int_positive: +7` -> `int_positive: 7` |
| `scalar-escapes`              | **no** | yes     | blank lines 1 -> 0; 23 lines -> 17 |
| `scalar-long-lines`           | **no** | yes     | 13 lines -> 23; refolded long lines |
| `scalar-styles`               | **no** | yes     | 20 lines -> 19; line 17: `...: this plain scalar` -> `...: this plain scalar runs onto a second line` |
| `scalar-timestamps`           | **no** | yes     | re-indented block sequences; line 4: `...1-12-15T02:59:43.1Z` -> `...1-12-15T02:59:43.100000Z` |
| `struct-deep-nesting`         | **no** | yes     | re-indented block sequences; line 17: `  - - - - - - - - deep leaf` -> `- - - - - - - - deep leaf` |
| `struct-empty`                | **no** | **no**  | lost 4 own-line comment(s); dropped 5 `---` document start; added 1 `...` document end; 21 lines -> 16 |
| `struct-seq-indent`           | **no** | yes     | re-indented block sequences; line 9: `  - one` -> `- one` |
| `tag-local-global`            | **no** | **no**  | re-indented block sequences; 32 lines -> 31 |
| `tag-unregistered`            | **no** | yes     | lost 4 own-line comment(s); dropped 1 %TAG directive(s); re-indented block sequences; 17 lines -> 13 |
| `text-bom`                    | **no** | yes     | dropped the BOM; moved 1 end-of-line comment(s) onto their own line; re-indented block sequences |
| `text-crlf`                   | **no** | yes     | re-indented block sequences; line 5: `key: value<CR>` -> `key: value` |
| `text-tabs`                   | **no** | **no**  | line 12: `...and a literal tab <TAB> here"` -> `...and a literal tab \t here"` |
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
exceptions, so the target is 41 of 41. Groups 1 to 4 are done — yamluna keeps every
directive, marker, BOM, CRLF and comment (bar the two empty-document files), never moves an
end-of-line comment, never re-indents or refolds an untouched node, and never re-spells a
scalar it did not touch. Group 5 is done for anchors and aliases, which stay aliases rather
than being cloned; duplicate keys are still refused, and cannot be represented at all while
`CommentedMap` is a `dict`.
