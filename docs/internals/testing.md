# Testing

The acceptance criterion is one line: load a document, change nothing, dump it, and get the
input back byte for byte. Everything in `tests/` and in the Rust `tests/` directories measures
some part of that, and a design argument is settled by whichever answer keeps the bytes
identical.

## The three round-trip scores

They are not the same number, because they measure different stacks. Quoting one of them as
"the" score is quoting the wrong thing.

| what round-trips | over | score | command |
|---|---|---|---|
| the Rust core, `parse` → `emit` | `yaml-test-suite` | **308 / 308** | `cargo test -p yamluna-core --test proptest_roundtrip` |
| the Python API, `YAML().load_all` → `.dump_all` | `yaml-test-suite` | **306 / 308** | `PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py` |
| the Python API, `YAML().load` → `.dump` | `tests/corpus/` | **40 / 40** | `PYTHONPATH=python .venv/bin/python tests/differential.py` |

The suite is what YAML *is*: 308 of its cases are expected to parse, and the Rust core
reproduces every one. The corpus is what the library is *for*: 41 hand-written files, one
concern each, chosen for the bytes that go missing in a round trip. The 41st, `key-duplicate`,
is scored on behaviour rather than bytes, because no `dict`-backed API can write two equal keys
back.

The gap between the first two rows is two cases, and both are the Python object model rather
than the FFI seam. `test_the_record_seam_loses_nothing_over_the_suite` asserts that
`emit(parse(text))` through the record classes is byte-identical to `parse`-then-`emit` inside
Rust for all 308, so when a recorded fact stops crossing the boundary that gate fails first and
names the case. It was written because the failure had already happened once: at commit
`8b05b39` the same 308 cases scored 302 in Rust and 202 through the Python API, and nothing
measured the difference.

Against `ruamel.yaml` 0.19.1 over the same corpus, with the ordinary round-trip recipe
(`typ='rt'`, `preserve_quotes=True`, everything else default):

```text
ruamel :  3 of 40 round-trippable files round-trip byte-identically
yamluna: 40 of 40 round-trippable files round-trip byte-identically
```

Point ruamel at the indentation style most of the corpus is written in
(`differential.py --seq-indent`) and it manages 7 of 40. yamluna is unaffected either way,
because it reproduces each node's own layout instead of applying one global indentation.
[Behaviour differences](../migrating/differences.md) has the per-file causes.

## What each layer asserts

Each layer is separately runnable, and each one fails for a different reason. That is the
point: a failure should tell you which half is wrong before you open a file.

| layer | what it asserts | what a failure means |
|---|---|---|
| `yamluna-scanner` | the upstream unit tests and all 402 `yaml-test-suite` conformance cases stay green after every patch, and `keep_comments(false)` is byte-identical to upstream | a fork patch changed the default token stream, or the fork has drifted from YAML |
| `yamluna-core` loader (`tests/corpus.rs`) | every comment of the source appears exactly once in the tree, in source order; every blank-line run is a real run; every node in the arena is reachable from the root exactly once | a comment was dropped, duplicated, or filed against the wrong node, which reaches a user as a comment that moves or disappears on a dump |
| `yamluna-core` trivia (`tests/trivia_rules.rs`) | one test per attachment rule, plus the cases where two rules meet | a comment that will move in a dump; the assertion names the slot it moved to |
| `yamluna-core` round trip (`tests/roundtrip.rs`) | `emit(parse(text)) == text` for all 41 corpus files, with nothing mutated | a fact the source carried and the document model or the emitter did not |
| `yamluna-core` fuzz (`tests/proptest_roundtrip.rs`) | the same invariant over the 308 suite cases and over generated documents: nested block and flow collections, every scalar style, anchors, aliases, tags, comments in every slot, blank lines, multi-document streams, unicode, CRLF | the model can spell a document it cannot spell back |
| `yamluna-py` (`tests/test_bindings.py`) | the records Rust builds are the ones `python/yamluna/_record.py` describes, a position stays a char offset the whole way across, and a parse failure arrives as the right exception class | a recorded fact does not cross the FFI, so the Rust score and the Python score diverge |
| `python/yamluna` (`test_constructor.py`, `test_representer.py`, `test_comments.py`, `test_scalars.py`, `test_registry.py`, `test_errors.py`) | the object model, registry and error hierarchy against hand-built record trees, with no extension built | the Python half is wrong on its own, independently of anything Rust did |
| end to end (`tests/test_roundtrip.py`) | `YAML().dump(YAML().load(text)) == text` for every corpus file | the whole stack lost something, and the layer tests above say which one |
| end to end, wide (`tests/test_suite_roundtrip.py`) | the same over all 308 suite cases, extracted the same way as the Rust harness so the two numbers subtract | as above, over documents nobody wrote for this library |
| mutation (`tests/test_mutation.py`) | comments stay attached to the right node across `insert`, `del`, `pop`, `move_to_end`, key rename, `reverse` and `sort` | the drift bug this library exists to not have is back |
| differential (`tests/differential.py`) | every divergence from ruamel is either a deliberate fix or a defect here | an unrecorded divergence, which is a defect until somebody argues otherwise |

The `test_representer.py` round trips are worth singling out: they build a record tree,
construct it into Python objects, represent it back and compare records. A per-direction test
can pass while the two directions disagree about which slot a comment lives in. A round trip
cannot.

## The known-gap lists are two-sided

`KNOWN_LOSSES`, `KNOWN_GAPS`, `KNOWN_FAILURES` and `KNOWN_RECORD_GAPS` each name every case
that does not pass, with a one-line cause. Every one of them fails the run in **both**
directions: an unlisted failure fails, and a listed case that starts passing also fails, with a
message telling you to drop it from the list. A fix cannot leave a stale excuse behind, and a
gap cannot go quiet.

Three of the four are currently empty. `KNOWN_GAPS` in
`crates/yamluna-core/tests/proptest_roundtrip.rs` is empty because the core loses none of the
308; `KNOWN_FAILURES` in `crates/yamluna-core/tests/roundtrip.rs` and `KNOWN_RECORD_GAPS` in
`tests/test_bindings.py` are empty because the core and the FFI records both reproduce all 41
corpus files.

What is left:

| list | entries |
|---|---|
| `KNOWN_LOSSES` (`tests/test_roundtrip.py`) | `key-duplicate`: `CommentedMap` is a `dict`, so two entries with equal keys collapse into one |
| `KNOWN_GAPS` (`tests/test_suite_roundtrip.py`) | `2JQS` and `X38W`, both ill-formed documents that YAML's own key-uniqueness rule rejects and every peer implementation refuses. `DuplicateKeyError` naming both positions is the answer, not a fix |

Alongside those, `tests/test_mutation.py` carries 12 xfails from two causes: eight because the
loader files a first child's own-line comment on the enclosing collection's `inner` slot rather
than on the child's `before` slot, so an insertion at the front mislabels it, and four because
an insertion strands the `-` of the item preceding an own-line comment. Every byte still
round-trips in both cases; only the ownership is wrong.

## The corpus

41 files under `tests/corpus/`, one concern each. The first line of each file says what it
covers, so `head -3 corpus/*.yaml` is the index. Files are read as bytes and decoded UTF-8 with
nothing normalised: `text-bom.yaml` keeps its BOM, `text-crlf.yaml` keeps its CRLF,
`comment-eof-no-newline.yaml` has no final newline. Those are exactly the bytes a round trip
loses.

`differential.py` exits non-zero if the corpus itself is malformed (not UTF-8, or a file whose
first line is not a `# covers:` comment), so it doubles as the corpus lint.

Writing a test against it is one fixture. Take `corpus_path`, `corpus_bytes` or `corpus_text`
and the test is parametrised over all 41 files, with the file stem as the test id
(`pytest -k comment-eol`). The acceptance test itself is six lines:

```python
def test_corpus_file_round_trips_byte_for_byte(
    corpus_text: str, corpus_path: Path, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """A load followed by a dump reproduces the source exactly, nothing having touched it."""
    if corpus_path.stem in KNOWN_LOSSES:
        pytest.skip(f'known loss: {KNOWN_LOSSES[corpus_path.stem]}')
    assert yamluna_roundtrip(corpus_text) == corpus_text
```

Two things the corpus deliberately leaves out. **Tabs as block-context separation**
(`key:<TAB>value`, `-<TAB>item`) are legal per YAML 1.2 and rejected by every libyaml-derived
parser, ruamel 0.19.1 included; matching libyaml there is a compatibility decision rather than
an accident, and `corpus/text-tabs.yaml` keeps only the tab positions that actually parse. **An
alias before its anchor** has no legal ordering to test, so `corpus/anchors-aliases.yaml`
covers every other ordering instead.

## Running it

```bash
# the Rust side: 726 tests, of which 6 are doc tests
cargo test --workspace

# the scanner fork on its own: 585 tests, of which 402 are the conformance suite
cargo test -p yamluna-scanner

# the Python side: 1097 passed, 45 skipped, 12 xfailed
PYTHONPATH=python .venv/bin/pytest tests -q

# both libraries over the whole corpus, with a unified diff per changed file
PYTHONPATH=python .venv/bin/python tests/differential.py --diff

# one corpus file, with its diff
PYTHONPATH=python .venv/bin/python tests/differential.py comment-eol

# the suite through the Python API, and again alongside the Rust core's score
PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py
PYTHONPATH=python .venv/bin/python tests/suite_roundtrip.py --rust
```

The corpus tests need the extension built (`maturin develop --uv`); without it they skip rather
than fail. Of the 45 skips, 41 are the round-trip file declining a case that does not apply to
it (40 files that are not known losses, plus `key-duplicate`, which is), 2 are `key-duplicate`
again refusing to load at all under the default `allow_duplicate_keys=False`, and 2 are the
tests of the *unbuilt* extension's error messages, which cannot run on a built checkout. None
of them is a gap.
