# `yamluna-scanner` — fork log

Vendored from **`saphyr-parser` 0.0.12** (MIT OR Apache-2.0, see `LICENSE`), plus the
402-case `yaml-test-suite` and the upstream unit tests, which are the regression net for every
patch below. They must stay green:

```sh
cargo test -p yamluna-scanner
```

Every patch is either an **upstreamable bug fix** (a defect in `saphyr-parser` that should be
filed there) or a **yamluna feature** (something a round-trip library needs that upstream has no
reason to carry). Line numbers are as of this file being written.

---

## Upstreamable bug fixes

### B1 — `%TAG` table was reset on every directive (DESIGN §1.4.1)

`parser_process_directives` declared `let mut tags = BTreeMap::new()` *inside* the directive
loop and assigned `self.tags = tags` on every iteration. Consequences: only the last `%TAG` line
of a document survived, a `%YAML` line after a `%TAG` line wiped the tag table, and the
duplicate-handle check (`tags.contains_key`) was dead code because the map was always empty.

- `src/parser.rs:833` — declaration hoisted out of the loop.
- `src/parser.rs:870` — `self.tags.append(&mut tags)` once, after the loop. `append` rather than
  assignment so a document with no directives does not clobber the table `keep_tags(true)` is
  deliberately carrying over, and so `keep_tags` merges rather than replaces.
- Tests: `src/parser.rs::test::{test_multiple_tag_directives_accumulate,
  test_tag_directive_survives_later_version_directive, test_duplicate_tag_handle_is_rejected}`.

### B2 — `impl Display for Tag` emitted invalid YAML (DESIGN §1.4.2)

For a non-`!` handle it wrote `{handle}!{suffix}` — for a resolved tag, whose `handle` field
holds the *prefix*, that is `tag:example.com,2000:!foo`, which is not a tag at all (it re-parses
as a plain scalar). It also wrote `!!` for the non-specific `!` tag.

- `src/parser.rs:162` — a resolved tag is now written in the verbatim form `!<uri>`, which needs
  no `%TAG` directive in scope; local tags stay `!suffix`; the non-specific tag stays `!`.
- Test: `src/parser.rs::test::test_tag_display_round_trips`, which also feeds every rendered form
  back through the parser and checks the tags come back identical.

### B3 — a quoted scalar's span swallowed its trailing whitespace and comment

`scan_flow_scalar` built its `Span` from `self.mark` *after* the `skip_ws_to_eol` that checks for
invalid trailing content, so for `a: "q"   # c` the scalar's span covered `"q"   # c`. Slicing the
source by the span therefore did not give back the lexeme — which is exactly what
`yamluna-core` does to fill `Node::raw` (DESIGN §2), so this would have made a byte-exact round
trip impossible for quoted scalars and duplicated end-of-line comments into scalar values.

- `src/scanner.rs:2178` — capture `end_mark` right after the closing quote; the token span is
  `start_mark..end_mark`.
- Test: `tests/roundtrip_extras.rs::quoted_scalar_span_stops_at_the_closing_quote`.

### B4 — `Marker` documentation contradicted the implementation (DESIGN §1.5)

`Marker::index` is a **char** offset, not a byte offset, and `Marker::col` is **0-based**, not
1-indexed. Three doc comments and the `ScanError` `Display` impl said otherwise.

- `src/scanner.rs:65`, `src/scanner.rs:69` and the `Marker` accessors — doc comments corrected.
- `src/scanner.rs:150` — `ScanError`'s message says `at char N` instead of `at byte N`. This
  changes the text of the error string; `tests/basic.rs` and `tests/issues.rs` were updated.
- The `Input` trait was **not** changed to carry byte offsets (DESIGN §1.5 forbids it);
  `yamluna-core` builds a char→byte table instead.
- Test: `tests/roundtrip_extras.rs::markers_are_char_offsets_and_zero_based_columns`.

### B5 — a TAB after `:` inside a flow mapping was rejected (spec compliance)

`fetch_value` errored on `':' must be followed by a valid YAML whitespace` whenever a `:` was
followed by a tab and then `-` or an alphanumeric — regardless of context. That check only makes
sense in **block** context, where what follows a `:` on the same line must be an
`s-l+flow-in-block` node: a nested block collection would need `s-indent`, which is spaces only,
so `:\t-` and `:\tkey:` are genuinely errors (yaml-test-suite `Y79Y-08`, `Y79Y-10`).

Inside a flow collection there is no indentation and no block collection can start, so a tab
after `:` is ordinary separation white space. YAML 1.2.2 [148] `c-ns-flow-map-separate-value`
is `":" ( s-separate(n,c) ns-flow-node(n,c) | e-node )`; [80] `s-separate` in a flow context is
`s-separate-lines(n)`, whose second alternative is `s-white+`; [33] `s-white ::= s-space |
s-tab`. `{a:\tb}` is therefore valid, and ruamel 0.19.1 (libyaml-derived) loads it — libyaml
rejects tabs in *block* context, not in flow.

- `src/scanner.rs:2595` — the check is gated on `self.flow_level == 0`. Nothing else changed;
  the tab is then consumed by `skip_to_next_token`'s `'\t' | ' ' => self.skip_blank()` arm,
  which already treats a tab as a blank outside block indentation.
- Tests: `tests/plain_tab.rs::{tab_after_colon_in_flow_mapping_is_separation_whitespace,
  tab_after_colon_in_block_context_is_still_rejected}`; the 402-case suite is unchanged.
- **Upstreamable**: yes. It is a one-condition spec-compliance fix in `saphyr-parser`'s own
  `fetch_value`, with the suite as the regression net.

---

## yamluna features

### F1 — comments (DESIGN §1.1)

`TokenType::Comment` (`src/scanner.rs:255`), `Event::Comment` (`src/parser.rs:108`) and
`Parser::keep_comments(bool)` (`src/parser.rs:430`), mirroring the existing `keep_tags`. Off by
default, and with it off the token stream and the event stream are byte-identical to upstream's —
that is what keeps the 402 conformance cases green.

Comment text includes the leading `#` and excludes the line break; trailing whitespace before the
break is kept verbatim. The `Span` covers exactly that text.

**Three sites consume a `#`**, not two, and all three emit:

1. `Scanner::skip_to_next_token` — `src/scanner.rs:972`
2. `Scanner::skip_yaml_whitespace` — `src/scanner.rs:1023`
3. `Input::skip_ws_to_eol` — `src/input.rs:197`, overridden in `src/input/str.rs:186`, driven by
   the scanner wrapper at `src/scanner.rs:1040`. This is the one that eats end-of-line comments
   after quoted scalars, directives, block-scalar headers and flow indicators. **Missing it is the
   classic way this patch silently loses comments**, so the test file covers each of those
   positions separately.

Supporting pieces:

- `Input::fetch_while_non_breakz` (`src/input.rs:397`) — `skip_while_non_breakz` that keeps what
  it skipped. Default method; `StrInput` does not override it.
- `Input::skip_ws_to_eol` gained an `Option<&mut String>` out-parameter. **This is a breaking
  change to a public trait method** — acceptable in a fork, and the reason the feature does not
  need a second, parallel method.
- `Scanner::{set_keep_comments, scan_comment, flush_comments}` — `src/scanner.rs:596`, `:604`,
  `:625`. Comments are parked in `pending_comments` rather than pushed straight into `tokens`,
  because a comment is scanned in the middle of fetching the token that *precedes* it. They are
  flushed at two points that put them in source order: after `skip_to_next_token`
  (`src/scanner.rs:774`, comments that precede the token about to be fetched) and after the whole
  fetch (`src/scanner.rs:760`, comments that follow the token just pushed). Parking them also
  keeps the simple-key bookkeeping (`SimpleKey::token_number`, `insert_token`) valid, since
  comments never land between a saved key and the position it was recorded at.
- `fetch_block_scalar` (`src/scanner.rs:1729`) flushes *before* pushing its token: it is the only
  fetch function whose token starts after the comment it may scan (`a: |- # header`).
- Parser side: comment tokens never reach the state machine. `peek_token`
  (`src/parser.rs:531`) turns them into `Event::Comment` in a `pending` queue
  (`src/parser.rs:217`); `next_event_impl` drains that queue ahead of the event it belongs before;
  `next_event_no_comments` (`src/parser.rs:513`) forwards them to the receiver so the `load_*`
  functions only ever see structural events.

Known quirk left alone: the span of a flow **indicator** token (`[`, `]`, `{`, `}`, `,`) still
extends to the end of any whitespace and comment after it, because `fetch_flow_collection_*` and
`fetch_flow_entry` build their spans after `skip_ws_to_eol`. Unlike B3 this is harmless — those
spans only feed a collection's *start* position, and collections have no `raw` — so it was left
as upstream has it. The comment's own span is exact either way.

### F2 — collection style (DESIGN §1.2)

`StructureStyle { Block, Flow }` (`src/parser.rs:131`) as a third field of
`Event::SequenceStart`/`Event::MappingStart`. The scanner always had the four distinct token
types; only the parser collapsed them, so this carries the distinction through instead of
reconstructing it downstream — which cannot be done, since an implicit flow mapping (`[a: 1]`) is
introduced by a synthetic `FlowMappingStart` token with an empty span.

`tests/issues.rs::test_issue1` used to assert one event list for `- a:\n  - 42`, `[{a: [42]}]`
*and* `[a: [42]]`; it is now parameterised by style, which is the regression guard for exactly
that.

### F3 — anchor names (DESIGN §1.3)

`AnchorRef { id, name }` (`src/parser.rs:117`) replaces the bare `usize` on `Event::Alias`,
`Event::Scalar`, `Event::SequenceStart` and `Event::MappingStart`. `id == 0` still means "no
anchor"; `name` is `Some` exactly when `id != 0`. The interning map stays private, so without the
name a round trip could not reproduce `&name`/`*name`.

`tests/yaml-test-suite.rs::format_anchor` renders the id, so the 402 expected event strings are
unchanged; it also asserts the id/name invariant on every anchored node in the whole suite.

### F4 — `%YAML` version accessor (DESIGN §1.4.3)

The parsed version used to be validated and thrown away. `Parser::version()`
(`src/parser.rs:441`) returns it. It is set while a document's directives are consumed — i.e.
before that document's `Event::DocumentStart` — and reset at the start of the next document's
directives, so it is valid for the whole document. A document with no `%YAML` line reports `None`.

An accessor rather than a new event field: adding it to `Event::DocumentStart` would have changed
a variant every consumer matches on, for data that is per-document rather than per-event. A loader
driving the parser as an iterator (which `yamluna-core` does, per DESIGN §2.3) reads it on
`DocumentStart`. **A receiver driven by `Parser::load` cannot see it** — that is the one cost of
this choice.

---

## Tests added

- `tests/roundtrip_extras.rs` — 30 tests for the new capabilities: comments in every position the
  scanner can meet one, `#` characters that are *not* comments, block-vs-flow style including the
  implicit flow mapping, anchor names on all four event kinds, and the position invariants. Every
  case is run through both `StrInput` and `BufferedInput`, which is what keeps the overridden and
  the default `skip_ws_to_eol` honest against each other.
- `src/parser.rs::test` — one test per §1.4 bug fix, each written so it fails against upstream.

## Files touched relative to upstream

```
src/parser.rs      Event::Comment, AnchorRef, StructureStyle, Parser::{keep_comments, version},
                   pending-event queue, comment-forwarding load path, B1, B2
src/scanner.rs     TokenType::Comment, keep_comments/pending_comments plumbing, B3, B4, B5
src/input.rs       skip_ws_to_eol comment capture, fetch_while_non_breakz
src/input/str.rs   skip_ws_to_eol override kept in sync
src/lib.rs         re-export AnchorRef and StructureStyle
tests/*            updated for the new event shapes; strings the suite compares are unchanged
```
