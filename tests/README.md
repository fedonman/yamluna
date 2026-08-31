# yamluna acceptance harness

This directory is the adjudicator. `docs/DESIGN.md` §6 defines acceptance; everything here
measures it. When a design question comes up ("should the emitter normalise this?"), the answer
is whichever choice keeps the corpus byte-identical.

```
corpus/            41 hand-written YAML files, one concern each
conftest.py        corpus discovery + the single yamluna seam
differential.py    the ruamel.yaml 0.19.1 oracle, importable and runnable
```

## Running things

```bash
# the ruamel baseline: the whole corpus, as the table below
.venv/bin/python tests/differential.py

# ... plus a unified diff for every file ruamel changes
.venv/bin/python tests/differential.py --diff

# one file, with its diff
.venv/bin/python tests/differential.py comment-eol

# ruamel again with the indentation style most of the corpus uses
.venv/bin/python tests/differential.py --seq-indent

# the Python suite (every corpus test skips until the extension is built)
.venv/bin/pytest tests -q

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
| `yamluna-py` | flat `Node` records survive the FFI boundary unchanged in both directions | `cargo test -p yamluna-py` |
| `python/yamluna` | the object model, registry and error hierarchy, tested against hand-built record lists — no extension needed | `pytest tests` |
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

## Measured: what ruamel.yaml 0.19.1 does to this corpus

Run with the ordinary round-trip recipe — `YAML()` (`typ='rt'`), `preserve_quotes = True`,
everything else default, including `width = 80` and `allow_duplicate_keys = False`.

**3 of 41 corpus files round-trip byte-identically.**

Point ruamel at the indentation style most of the corpus is written in
(`yaml.indent(mapping=2, sequence=4, offset=2)`, i.e. `differential.py --seq-indent`) and it
manages **7 of 41** — `blank-lines`, `comment-block-boundaries`, `comment-eol` and
`comment-own-line` join the passing set. So four of the failures below are ruamel being pointed
at the wrong layout, and 34 are ruamel being unable to reproduce the file at all. Note that no
single setting can fix `struct-seq-indent.yaml`, which mixes indentations within one document:
ruamel emits one global indentation, which is why DESIGN 2.4 reproduces `raw` per node instead of
re-deciding layout.

<!-- generated by: .venv/bin/python tests/differential.py -->

| corpus file                 | round-trips | what ruamel changed |
| --------------------------- | ----------- | ------------------- |
| `anchors-aliases`             | **no**      | lost 3 anchor(s); re-indented block sequences; warns ReusedAnchorWarning |
| `anchors-merge`               | **no**      | moved 1 end-of-line comment(s) onto their own line; 29 lines -> 30 |
| `anchors-recursive`           | **no**      | lost 4 anchor(s); lost 4 alias(s); re-indented block sequences |
| `blank-lines`                 | **no**      | re-indented block sequences; line 19: `  - a` -> `- a` |
| `block-scalar-chomping`       | yes         |  |
| `block-scalar-indent`         | **no**      | line 9: `...p_then_explicit: \|-2` -> `...p_then_explicit: \|2-` |
| `comment-anchors`             | **no**      | moved 4 end-of-line comment(s) onto their own line; re-indented block sequences; 14 lines -> 18 |
| `comment-block-boundaries`    | **no**      | re-indented block sequences; line 6: `    - a` -> `  - a` |
| `comment-block-scalar-header` | yes         |  |
| `comment-doc-markers`         | **no**      | lost 2 own-line comment(s); lost 3 end-of-line comment(s); added 1 `---` document start; dropped 1 `...` document end; 10 lines -> 5 |
| `comment-eof-no-newline`      | **no**      | appended a final newline; 5 lines -> 6 |
| `comment-eol`                 | **no**      | re-indented block sequences; line 13: `  - one                  # after a sequence ...` -> `- one                    # after a sequence ...` |
| `comment-flow`                | **no**      | lost 2 own-line comment(s); lost 6 end-of-line comment(s); 22 lines -> 8 |
| `comment-only`                | **no**      | lost 6 own-line comment(s); blank lines 1 -> 0; removed the final newline; 7 lines -> 0 |
| `comment-own-line`            | **no**      | re-indented block sequences; line 11: `  - a` -> `- a` |
| `comment-top-of-file`         | yes         |  |
| `directive-multiple-tags`     | **no**      | lost 5 own-line comment(s); dropped 2 %TAG directive(s); 14 lines -> 9 |
| `directive-per-document`      | **no**      | lost 3 own-line comment(s); dropped 2 `...` document end; dropped 1 %YAML directive(s); added 1 %TAG directive(s); 14 lines -> 9 |
| `directive-tag`               | **no**      | lost 2 own-line comment(s); dropped 1 %TAG directive(s); 9 lines -> 7 |
| `directive-yaml-version`      | **no**      | lost 2 own-line comment(s); dropped 1 %YAML directive(s); re-indented block sequences; 8 lines -> 6 |
| `doc-multi-explicit`          | **no**      | lost 3 own-line comment(s); dropped 2 `---` document start; dropped 3 `...` document end; 18 lines -> 10 |
| `doc-multi-implicit`          | **no**      | dropped 1 `---` document start; 15 lines -> 14 |
| `flow-forms`                  | **no**      | 26 lines -> 19; line 7: `..._nested: [{}, [], {a: []}]` -> `..._nested: [{}, [], a: []]` |
| `flow-nesting`                | **no**      | dropped 2 `---` document start; re-indented block sequences; 28 lines -> 21 |
| `key-complex`                 | **no**      | moved 1 end-of-line comment(s) onto their own line; 30 lines -> 20 |
| `key-duplicate`               | **no**      | raises DuplicateKeyError: while constructing a mapping |
| `scalar-binary`               | **no**      | 11 lines -> 12; line 5: `...eXvPz7Y6OjuDg4J+fn5` -> `...eXvPz7Y6OjuDg4J+fn5OTk6enp56enmlp` |
| `scalar-core-schema`          | **no**      | line 9: `int_positive: +7` -> `int_positive: 7` |
| `scalar-escapes`              | **no**      | blank lines 1 -> 0; 23 lines -> 17 |
| `scalar-long-lines`           | **no**      | 13 lines -> 23; refolded long lines |
| `scalar-styles`               | **no**      | 20 lines -> 19; line 17: `...: this plain scalar` -> `...: this plain scalar runs onto a second line` |
| `scalar-timestamps`           | **no**      | re-indented block sequences; line 4: `...1-12-15T02:59:43.1Z` -> `...1-12-15T02:59:43.100000Z` |
| `struct-deep-nesting`         | **no**      | re-indented block sequences; line 17: `  - - - - - - - - deep leaf` -> `- - - - - - - - deep leaf` |
| `struct-empty`                | **no**      | lost 4 own-line comment(s); dropped 5 `---` document start; added 1 `...` document end; 21 lines -> 16 |
| `struct-seq-indent`           | **no**      | re-indented block sequences; line 9: `  - one` -> `- one` |
| `tag-local-global`            | **no**      | re-indented block sequences; 32 lines -> 31 |
| `tag-unregistered`            | **no**      | lost 4 own-line comment(s); dropped 1 %TAG directive(s); re-indented block sequences; 17 lines -> 13 |
| `text-bom`                    | **no**      | dropped the BOM; moved 1 end-of-line comment(s) onto their own line; re-indented block sequences |
| `text-crlf`                   | **no**      | re-indented block sequences; line 5: `key: value<CR>` -> `key: value` |
| `text-tabs`                   | **no**      | line 12: `...and a literal tab <TAB> here"` -> `...and a literal tab \t here"` |
| `text-unicode`                | **no**      | re-indented block sequences; line 18: `quoted_escape: "\u00e9 \U0001F600"` -> `quoted_escape: "é 😀"` |

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
exceptions, so the target for this table is 41 of 41.
