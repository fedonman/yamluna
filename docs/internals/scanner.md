# The forked scanner

`crates/yamluna-scanner` is a vendored copy of
[`saphyr-parser`](https://github.com/saphyr-rs/saphyr) 0.0.12, not a dependency on it. The
reason is that the upstream event stream throws away three things a round-trip library cannot
work without, and none of the three can be recovered downstream.

## What the upstream event stream does not carry

Here is `saphyr-parser` 0.0.12 driven directly, printing every event and the span it reports.
The program is fifteen lines and a `saphyr-parser = "0.0.12"` dependency; the output below is
what it printed.

```text
--- comments: "# top\na: 1  # eol\n"
StreamStart   span 0..0 (line 1, col 0)
DocumentStart(false)   span 6..6 (line 2, col 0)
MappingStart(0, None)   span 6..6 (line 2, col 0)
Scalar("a", Plain, 0, None)   span 6..7 (line 2, col 0)
Scalar("1", Plain, 0, None)   span 9..10 (line 2, col 3)
MappingEnd   span 18..18 (line 3, col 0)
DocumentEnd   span 18..18 (line 3, col 0)
StreamEnd   span 18..18 (line 3, col 0)
```

**Comments produce no event.** Both `# top` and `# eol` are consumed by the scanner and
dropped. There is no token type for them and no flag that turns them on.

```text
--- anchors: "a: &name 1\nb: *name\n"
MappingStart(0, None)   span 0..0 (line 1, col 0)
Scalar("a", Plain, 0, None)   span 0..1 (line 1, col 0)
Scalar("1", Plain, 1, None)   span 9..10 (line 1, col 9)
Scalar("b", Plain, 0, None)   span 11..12 (line 2, col 0)
Alias(1)   span 14..19 (line 2, col 3)
```

**An anchor arrives as an interned integer.** `&name` and `*name` are both `1`. The map from
`1` back to `"name"` is private to the parser, so a consumer cannot write `&name` again.

**A collection start says nothing about how it was written.** `MappingStart(0, None)` is the
whole event: an anchor id and a tag. Block and flow are the same event.

## Why block-versus-flow cannot be recovered from the spans

The obvious workaround is to read the style off the span. A flow collection is introduced by a
real `[` or `{`, so its start event should have a one-character span; a block collection is
introduced by nothing, so its span should be empty. Three inputs, same program:

```text
--- explicit flow mapping: "[{a: 1}]"
SequenceStart(0, None)   span 0..1 (line 1, col 0)
MappingStart(0, None)   span 1..2 (line 1, col 1)

--- block mapping in a block sequence: "- a: 1"
SequenceStart(0, None)   span 0..0 (line 1, col 0)
MappingStart(0, None)   span 2..2 (line 1, col 2)

--- implicit flow mapping: "[a: 1]"
SequenceStart(0, None)   span 0..1 (line 1, col 0)
MappingStart(0, None)   span 1..1 (line 1, col 1)
```

The first two behave. The third does not: `[a: 1]` is a flow sequence holding a flow mapping
written without braces of its own, and the scanner introduces it with a synthetic
`FlowMappingStart` token that has an empty span. Its `MappingStart` is `1..1`, the same shape
as the block mapping's `2..2`. The heuristic calls it a block mapping, and a block mapping
written inside `[ ]` is not YAML.

The fork carries the distinction instead of reconstructing it, so the case is not a special
case:

```python
from yamluna import YAML

yaml = YAML()
print(repr(yaml.dump(yaml.load('[a: 1]\n'))))
print(repr(yaml.dump(yaml.load('seq: [x: 1, y: 2]\n'))))
```

```text
'[a: 1]\n'
'seq: [x: 1, y: 2]\n'
```

## The patch list

Every change to the vendored source is logged in
[`crates/yamluna-scanner/FORK.md`](https://github.com/qilimanjaro-tech/yamluna/blob/master/crates/yamluna-scanner/FORK.md),
with the file, the line and the test that pins it. Each one is in one of two piles.

### Upstreamable bug fixes

Defects in `saphyr-parser` itself. They are not about round-tripping, and they should be filed
upstream.

| | what was wrong | what it cost |
|---|---|---|
| **B1** | `parser_process_directives` declared its `%TAG` map *inside* the directive loop and assigned it out on every iteration | only the last `%TAG` line of a document survived, a `%YAML` line after a `%TAG` line wiped the table, and the duplicate-handle check was dead code |
| **B2** | `impl Display for Tag` wrote `{handle}!{suffix}` for a non-`!` handle | a resolved tag rendered as `tag:example.com,2000:!foo`, which is not a tag and re-parses as a plain scalar; the non-specific `!` rendered as `!!` |
| **B3** | `scan_flow_scalar` built its `Span` after the trailing-content check | for `a: "q"   # c` the scalar's span covered `"q"   # c`, so slicing the source by the span did not give back the lexeme |
| **B4** | three doc comments and one `Display` impl described `Marker` wrongly | `Marker::index` is a **char** offset, not bytes, and `Marker::col` is **0-based**, not 1-indexed; the error text said `at byte N` |
| **B5** | `fetch_value` rejected a TAB after `:` regardless of context | `{a:\tb}` was refused, though it is legal YAML 1.2 and ruamel 0.19.1 loads it |

B5 is the one with a user-visible result. YAML 1.2.2 [148] makes the separator after a flow
mapping's `:` an `s-separate`, [80] resolves that to `s-white+` in a flow context, and [33]
`s-white` includes TAB, so a tab there is ordinary separation white space. In **block** context
what follows a `:` on the same line may start a nested block collection, whose indentation is
spaces only, so the check is right there and stays. Upstream, both are errors:

```text
saphyr-parser 0.0.12
"{a:\tb}\n"  -> ':' must be followed by a valid YAML whitespace at byte 4 line 1 column 5
"a:\tb\n"    -> ':' must be followed by a valid YAML whitespace at byte 3 line 1 column 4

yamluna-scanner
"{a:\tb}\n"  -> ok
"a:\tb\n"    -> ':' must be followed by a valid YAML whitespace at char 3 line 1 column 4
```

The second message is also B4: `at char`, because that is what the number is. The two libraries
agree on both inputs:

```text
yamluna  '{a:\tb}\n'    -> {'a': 'b'}
ruamel   '{a:\tb}\n'    -> {'a': 'b'}
yamluna  'a:\tb\n'      -> ScannerError
ruamel   'a:\tb\n'      -> ScannerError
```

### yamluna features

Things a round-trip library needs and a parser has no reason to carry.

| | what it adds |
|---|---|
| **F1** | `TokenType::Comment`, `Event::Comment` and `Parser::keep_comments(bool)`. Comment text includes the leading `#`, excludes the line break, and keeps trailing white space verbatim. Off by default, and the 402 conformance cases are what pin the default stream to upstream's. |
| **F2** | `StructureStyle { Block, Flow }` as a third field of `Event::SequenceStart` and `Event::MappingStart`. The scanner always had four distinct token types; only the parser collapsed them. |
| **F3** | `AnchorRef { id, name }` in place of the bare `usize` on `Alias`, `Scalar`, `SequenceStart` and `MappingStart`. `id == 0` still means "no anchor"; `name` is `Some` exactly when `id != 0`. |
| **F4** | `Parser::version()`, returning the `%YAML` version that used to be validated and discarded. |

The same inputs through the fork, with `keep_comments(true)`, in excerpt:

```text
Comment("# top")   span 0..5
Scalar("1", Plain, AnchorRef { id: 1, name: Some("name") }, None)   span 9..10
Alias(AnchorRef { id: 1, name: Some("name") })   span 14..19
SequenceStart(AnchorRef { id: 0, name: None }, None, Flow)   span 0..1
MappingStart(AnchorRef { id: 0, name: None }, None, Flow)   span 1..1
```

Two costs worth knowing before you read the diff:

* `Input::skip_ws_to_eol` gained an `Option<&mut String>` out-parameter. That is a breaking
  change to a public trait method, which a fork can afford and upstream cannot. It is also
  where the comment feature is easiest to get wrong: **three** sites consume a `#`, not two,
  and the third is the one that eats end-of-line comments after quoted scalars, directives,
  block-scalar headers and flow indicators.
* F4 is an accessor rather than a field on `Event::DocumentStart`, so a consumer driving the
  parser with `Parser::load` and a receiver cannot see the version. `yamluna-core` drives the
  parser as an iterator and reads it on `DocumentStart`.

## The regression net

The vendored copy brings the upstream unit tests and the 402-case `yaml-test-suite` with it,
and they stay green on every patch:

```bash
cargo test -p yamluna-scanner
```

That is 585 tests, of which `--test yaml-test-suite` is the 402. `tests/roundtrip_extras.rs`
adds 30 more for the new capabilities, each run through both `StrInput` and `BufferedInput` so
the overridden and the default `skip_ws_to_eol` stay honest against each other. See
[Testing](testing.md) for what the rest of the layers assert.

## Licence

`saphyr-parser` is MIT or Apache-2.0, at your option, and so is the fork. There are two sets of
licences, because the upstream repository was originally maintained by
[chyh1990](https://github.com/chyh1990) and later by
[Ethiraric](https://github.com/Ethiraric); a redistribution must include one licence from each
set. Both sets ship in `crates/yamluna-scanner/.licenses/`, and
`crates/yamluna-scanner/LICENSE` says which commit divides them.
