# yamluna — design contract

`yamluna` is a round-trip YAML library: a Rust core (forked `saphyr-parser` scanner + a
purpose-built round-trip document model and emitter) behind a Python API that replaces
`ruamel.yaml`'s `typ='rt'`.

**Scope.** `typ='rt'` only. No `safe`/`base`/`unsafe`, no `!!python/object:`, no component
substitution (`yaml.Parser = ...`), no plug-ins, no legacy module-level `load()`/`dump()`, no
`scan()`/`compose()`/`serialize()` low-level pipeline.

This file is the contract between the layers. Everything below is normative: if code and this
file disagree, one of them is a bug.

---

## 0. Layering

```
python/yamluna/            pure Python: YAML(), CommentedMap/Seq, scalar types, registry, errors
        │  flat Node records (both directions)
crates/yamluna-py/         PyO3: Node <-> yamluna_core::Node, GIL release, error mapping
crates/yamluna-core/       document model, loader (events -> nodes), trivia attachment, emitter
crates/yamluna-scanner/    forked saphyr-parser: comments, collection style, anchor names
```

The FFI boundary is **symmetric and flat**: `parse(str) -> list[Node]`, `emit(list[Node]) -> str`.
Rust never walks a `CommentedMap`; Python never formats YAML text. Each half is independently
testable — the emitter has pure-Rust unit tests, the Python layer has pure-Python tests against
hand-built record lists.

---

## 1. `yamluna-scanner` — the fork

Vendored from `saphyr-parser` 0.0.12 (MIT OR Apache-2.0, see `crates/yamluna-scanner/LICENSE`).
The 402-case `yaml-test-suite` and the upstream unit tests come with it and **must stay green**;
they are the regression net for every patch below.

### 1.1 Comments (new)

```rust
pub enum TokenType<'input> {
    // ...
    /// A `#` comment, from the `#` through the last character before the line break.
    Comment(Cow<'input, str>),
}

pub enum Event<'input> {
    // ...
    /// A `#` comment. Only produced when `Parser::keep_comments(true)`.
    Comment(Cow<'input, str>),
}

impl<'input, I: Input> Parser<'input, I> {
    /// Emit `Event::Comment` for every comment in the source. Off by default.
    #[must_use] pub fn keep_comments(self, value: bool) -> Self;
}
```

- Text **includes** the leading `#` and **excludes** the line break. Trailing whitespace before
  the break is kept verbatim (the emitter reproduces it).
- The `Span` covers exactly the emitted text.
- Comments never enter the parser state machine: the parser forwards the token to the receiver
  and continues. With `keep_comments(false)` (default) behaviour is byte-identical to upstream —
  that is what keeps the conformance suite green.
- Every path that consumes a `#` must emit one: the two `#` arms in `scanner.rs`
  (`skip_to_next_token`, `skip_yaml_whitespace`) **and** `Input::skip_ws_to_eol`, which swallows
  comments of its own (`input.rs`, doc line "Also skips comments"). Missing the third site is the
  classic way this patch silently loses EOL comments after quoted scalars.

### 1.2 Collection style (new)

```rust
pub enum StructureStyle { Block, Flow }

pub enum Event<'input> {
    SequenceStart(usize, Option<Cow<'input, Tag>>, StructureStyle),
    MappingStart(usize, Option<Cow<'input, Tag>>, StructureStyle),
}
```

The scanner already distinguishes `BlockSequenceStart`/`FlowSequenceStart`/
`BlockMappingStart`/`FlowMappingStart`; the parser currently collapses them. Carry the
distinction through instead of reconstructing it.

Do **not** try to recover this downstream from spans. Implicit flow mappings push a synthetic
empty-span `FlowMappingStart`, so `[a: 1]` misclassifies and re-emits as invalid YAML.

### 1.3 Anchor names (new)

Anchor names are interned to `usize` and the map is private, so a round-trip cannot reproduce
`&name`/`*name`. Carry the name alongside the id — additive, so existing id-based consumers and
the test-suite formatter keep working:

```rust
/// An anchor as written in the source, plus the parser's interned id.
#[derive(Clone, PartialEq, Eq, Debug, Default)]
pub struct AnchorRef<'input> { pub id: usize, pub name: Option<Cow<'input, str>> }

pub enum Event<'input> {
    Alias(AnchorRef<'input>),
    Scalar(Cow<'input, str>, ScalarStyle, AnchorRef<'input>, Option<Cow<'input, Tag>>),
    SequenceStart(AnchorRef<'input>, Option<Cow<'input, Tag>>, StructureStyle),
    MappingStart(AnchorRef<'input>, Option<Cow<'input, Tag>>, StructureStyle),
}
```

`id == 0` means "no anchor" exactly as today. `name` is `Some` whenever `id != 0`.

### 1.4 Bug fixes carried in the fork

These are upstream defects; fix them here and file them upstream separately.

1. **`%TAG` table reset per directive.** `let mut tags = BTreeMap::new();` is declared *inside*
   the directive loop, so only the last `%TAG` line survives and the duplicate-handle check is
   dead code. Hoist the declaration out of the loop.
2. **`impl Display for Tag` emits invalid YAML** for non-`!` handles: it writes
   `{handle}!{suffix}`, producing `tag:example.com,2000:!foo`. A resolved tag must be written
   `!<full-uri>` (verbatim form) unless a handle is in scope.
3. **`%YAML` version is parsed and discarded.** Surface it so the document can re-emit it.

### 1.5 Positions

`Marker::index()` is a **char** offset, not bytes, despite three doc comments and one `Display`
impl saying otherwise. `Marker::col()` is **0-based** despite its doc saying 1-indexed.

Do not change the `Input` trait to carry byte offsets — that is a breaking API change for a
gain we do not need. `yamluna-core` builds a char→byte table once per document (`Vec<u32>`,
one entry per char, `O(n)`, built during the single pass we already make over the source).
Fix the doc comments in the fork so the next reader is not misled.

ruamel's `Mark.index`/`.pointer` are also char offsets and its `Mark.column` is also 0-based,
so once the units are known the two agree and `.lc` needs only `line - 1`.

---

## 2. `yamluna-core` — the document model

Owned, `'static`, no `'input` parameter. The source text is kept *beside* the tree, never
borrowed by it. Nodes are `'static` so they can cross the FFI boundary and so a subtree can
migrate between documents without a use-after-free.

The model carries two kinds of field. Most say what the document **means** — a version, a tag,
a scalar's value, an entry's key. A second kind says only how the source **spelled** it: `raw`,
`colon`, `anchor_at`, `tag_at`, `header_at`, `tag_first`, `flow_seps`, `directives_raw`,
`stream_tail`, `line_space`. Those exist for one reason, stated in §2.5, and they are what the byte-identity
invariant of §2.4 is actually built out of. They are marked below.

```rust
pub struct Document {
    // -- meaning --------------------------------------------------------------------------
    pub version: Option<(u32, u32)>,          // `%YAML 1.2`
    pub tag_directives: Vec<TagDirective>,    // `%TAG` lines, in source order
    pub explicit_start: bool,                 // had `---`
    pub explicit_end: bool,                   // had `...`
    pub root: Option<NodeId>,                 // `None` for a stream of nothing but trivia
    pub nodes: Vec<Node>,                     // arena; NodeId is an index
    pub leading: Vec<Trivia>,                 // before the document's first token
    pub trailing: Vec<Trivia>,                // after the last node
    pub duplicate_keys: Vec<DuplicateKey>,    // every repeat, in source order (§2.3)

    // -- spelling (§2.5) ------------------------------------------------------------------
    /// The raw region above the document verbatim: every whole line from the last thing
    /// consumed through the line before the document's first token, plus how many of
    /// `leading`'s trivia were read from inside it. `None` unless the region holds a line
    /// the model cannot spell out again.
    ///
    /// Two shapes qualify. `version` and `tag_directives` are a directive line's *meaning*,
    /// never its spelling: `%YAML  1.1` is the same version as `%YAML 1.1`, a reserved
    /// directive (`%FOO bar`) has no model at all, and a comment may sit on any of those
    /// lines or between them. A bare `...` is the same problem with nothing behind it: the
    /// parser gives no event for a document-end marker that closes no document
    /// (`explicit_end` is the marker that *does* close one), so the line is its only record.
    pub directives_raw: Option<(String, usize)>,
    /// How many of `tag_directives` were written *above* the `%YAML` line; the rest below.
    /// The two kinds interleave freely on the page and the model keeps them in separate
    /// fields, so this is what says where the `%YAML` line sat among them.
    pub tags_before_version: usize,
    pub bom: bool,                            // the stream began with a BOM; first doc only
    pub final_line_break: bool,               // the source ended with a break; last doc only
    /// White space the source ends with that no line break closes. Always empty when
    /// `final_line_break` is set — that flag already says the stream ends in a break.
    pub stream_tail: String,
    /// The source lines whose white space no column can reproduce: the ones holding a TAB,
    /// and the ones ending in white space. Keyed by 0-based line, verbatim, without the
    /// break. A fact about the *stream*, so only its first document carries it, and it is
    /// read only while the model still matches the page.
    pub line_space: HashMap<u32, String>,
}

pub struct TagDirective { pub handle: String, pub prefix: String }

/// A key already present in the same mapping. Recorded, never silently merged (§2.3).
pub struct DuplicateKey { pub key: String, pub first: Position, pub again: Position }

pub type NodeId = u32;

pub enum NodeKind {
    Scalar,
    Sequence { items: Vec<NodeId> },
    Mapping  { entries: Vec<Entry> },
    Alias    { anchor: String },
}

pub struct Entry {
    pub key: NodeId,
    pub value: NodeId,
    pub merge: bool,                  // a `<<` key; recorded, never expanded (§2.3)
    pub explicit: bool,               // written in the `? key` / `: value` form
    // -- spelling (§2.5) --
    /// Where the `:` between the key and the value was written, or `None` when the source
    /// wrote none (`{a: 1, b}`) or the entry was built rather than loaded. The gap between a
    /// key and its `:` is white space no node owns, so without this `date   : 2001-01-23`
    /// comes back as `date:    2001-01-23` — the same columns, the wrong spelling.
    pub colon: Option<Position>,
}

pub struct Node {
    // -- meaning --------------------------------------------------------------------------
    pub kind: NodeKind,
    pub anchor: Option<String>,       // `&name`, without the `&`
    pub tag: Option<NodeTag>,
    pub style: Style,
    /// Cooked scalar value (escapes resolved, block scalars folded). `Scalar` nodes only.
    pub value: Option<String>,
    pub pos: Position,                // 0-based line and column of the node's first character
    pub trivia: Trivia4,

    // -- spelling (§2.5) ------------------------------------------------------------------
    /// The lexeme exactly as written, including quotes / block header. `Scalar` nodes only.
    /// This is what makes an unmodified round trip byte-exact. An implicit empty node
    /// (`key:` with nothing after it) has `Some("")`; a node the user built has `None`.
    pub raw: Option<String>,
    /// Where the `&anchor` and the tag were written, or `None` for a node with neither or
    /// one the user built. `pos` is the node's *content*, and a property sits ahead of it at
    /// a line and column nothing else records: without these an anchored key lands at the
    /// cursor's column rather than the mapping's, the gap after `&anchor` becomes padding to
    /// the content column rather than the space the source wrote, and a property the source
    /// put on a line of its own is pulled up onto the node's.
    pub anchor_at: Option<Position>,
    pub tag_at: Option<Position>,
    /// Where a block scalar's header (`|`, `>-`, `|2`, ...) was written, or `None` for any
    /// other node and one the user built. Same reason as `anchor_at`: `pos` is the scalar's
    /// *body*, so the header is a lexeme on a line and column of its own, and the source may
    /// put it a line below the node's properties (`!foo`, then `>1` on the next line).
    pub header_at: Option<Position>,
    /// The tag was written *before* the anchor (`!!str &a v`, not `&a !!str v`). YAML allows
    /// either order and neither is canonical, so the emitter has to be told which was used.
    pub tag_first: bool,
    /// What a flow collection's source wrote *between* its lexemes: one run in front of each
    /// child and one in front of the closing bracket, so a recorded vector is
    /// `children + 1` long — except for a single pair written with no brackets of its own
    /// (`[a: 1]`, `[? a : b]`, `[&c c: d]`), which has no closing bracket to separate from
    /// and records exactly `children` runs. That length is the one fact that says the pair
    /// wrote no brackets. Each run is the separation verbatim — white space, `,`, `:`, `?` —
    /// with a bare `#` where a comment stood, because a comment's *text* is trivia and is
    /// written from the slot it was filed in; keeping the mark is what lets the run go back
    /// around the comment rather than being cut in half by it. Anything else (a node's own
    /// `&anchor` or tag) ends the run.
    ///
    /// It is the one fact that tells `[1, 2]` from `[1, 2, ]` from `[ 1 , 2 ]`, says which
    /// key of `{a: 1, b}` was written with no `:`, and remembers that the gap in `[a<TAB>,
    /// b]` was a TAB. Empty for a collection the user built or edited, which the emitter
    /// lays out instead — and empty is the *only* "not recorded", so a stale vector cannot
    /// survive an insertion or a deletion.
    pub flow_seps: Vec<String>,
}

pub enum Style { Scalar(ScalarStyle), Block, Flow }

/// A tag as written *and* as resolved, because round-trip needs the former and the tag
/// registry needs the latter.
pub struct NodeTag { pub handle: String, pub suffix: String, pub resolved: String }
```

### 2.1 Trivia

A `Trivia` is a comment or a run of blank lines. Blank lines are first-class rather than being
smuggled inside comment text as embedded newlines (ruamel does the latter; it is a source of
comment-drift bugs and it makes "how many blank lines" unanswerable).

```rust
pub enum Trivia {
    Comment { text: String, own_line: bool, col: u32 },
    BlankLines(u32),
}
```

`own_line` is `false` for an end-of-line comment (source has a non-space character before the
`#` on the same line), `true` otherwise. `col` is the 0-based column of the `#`, so the emitter
can preserve alignment.

Each node carries four ordered slots, and *the slots are keyed by node identity, not by index*:

```rust
#[derive(Default)]
pub struct Trivia4 {
    pub before: Vec<Trivia>,   // own-line trivia immediately preceding this node
    pub eol:    Option<Trivia>,// end-of-line comment on the node's last line
    pub inner:  Vec<Trivia>,   // trivia between a collection's start token and its first child
    pub after:  Vec<Trivia>,   // trailing trivia of a collection, before its parent continues
}
```

**Why not ruamel's `ca.items[index]`.** ruamel keys sequence comments by integer index. The
renumbering itself is not the bug — `CommentedSeq.insert` and `__delsingleitem__` do renumber
`ca.items` correctly, and for end-of-line-only comments the result is right. The bug is that an
**own-line comment is stored glued into the previous sibling's end-of-line `CommentToken.value`**,
so renumbering faithfully moves a token that contains a comment belonging to a *different*
element. `insert(0, x)` therefore puts the following item's comment above the new item, and
`del seq[i]` destroys the neighbour's comment along with the deleted one. (Measured; see
`docs/RUAMEL-BEHAVIOR.md` §3 and `docs/DIVERGENCES.md` A1–A3.)

We key by the node itself and keep own-line trivia in its own slot rather than glued to a
sibling's token; `.ca.items` is *projected* from that at the Python API surface, so existing code
that reads `.ca` still works while mutation stays correct. This is a deliberate, documented
divergence — it is one of the "current bugs" this library exists to not have.

**`inner` has no ruamel counterpart.** For a nested collection, ruamel duplicates the child's
leading comments into *both* the parent's slot 3 and the child's `ca.comment[1]`, and emits only
the child's. The `.ca` projection must reproduce that duplication, or existing code reading
`ca.items[k][3]` sees `None` where ruamel gave it a token.

### 2.2 Attachment rules

Given the comment stream and the node spans (both in one char-offset coordinate system), the
attachment is a merge over one integer axis. Normative rules:

1. A comment on the same line as, and after, the end of a node's last token is that node's
   `eol`. For a mapping entry, "the node" is the *value*, unless the comment falls between the
   key and the `:` in which case it is the key's `eol`.
2. An own-line comment attaches `before` the next node that starts at or after it, **unless**
   that node is outside the enclosing collection — then it is the enclosing collection's `after`.
   This is the "trailing comment of this block vs. leading comment of the next sibling"
   distinction, and the enclosing-collection test is what decides it.

   **Not yet true, and pinned.** A comment indented into a nested block collection currently
   lands on that collection's `inner` slot rather than on its first child's `before`. The three
   slots are written one after another, so every byte comes back either way; what differs is
   which subtree the comment dies with, and `inner` is why `seq.insert(0, x)` still labels the
   new element with the old first element's comment. The eight `test_mutation.py` xfails naming
   `take_before()` are that gap, and they fail the suite if it closes without being noticed.
3. A run of ≥1 empty lines becomes `Trivia::BlankLines(n)` in whichever slot the next non-blank
   trivia or node would take.
4. Comments before the first token of a document go to `Document::leading`; comments after the
   last node go to `Document::trailing`.
5. A comment inside an aliased subtree belongs to the anchored node and is emitted once, at the
   anchor. Alias sites re-emit `*name` and nothing else.

### 2.3 Loader

Own `SpannedEventReceiver` over `Parser::new_from_str(...).keep_comments(true)`. Not
`saphyr::YamlLoader` — it forces a `LinkedHashMap` mapping (no duplicate keys, nowhere to hang
per-entry trivia), requires `Clone + Hash + Eq`, silently last-wins on duplicate keys, and
resolves aliases by cloning the target so `*base` is unrecoverable after load.

- **BOM** is stripped by the loader; `Parser::new_from_str` does not do it.
- **Duplicate keys** are reported, not silently merged. The loader records every duplicate;
  the Python layer raises `DuplicateKeyError` or warns per `allow_duplicate_keys`.
- **Aliases stay aliases.** `Alias` is a node kind, not a clone of the target. Recursive
  anchors therefore load without the `BadValue` failure the upstream loader has.
- **Merge keys** (`<<`) are recorded as `Entry { merge: true }` and resolved lazily by the
  Python layer, so a dump re-emits `<<: *base` rather than the expanded mapping.

### 2.4 Emitter

Written from scratch; nothing in `saphyr`'s emitter is reusable (it is monomorphic on `Yaml`,
hardcodes indent 2, unconditionally writes `---`, `todo!()`s on literal/folded representation
scalars, silently drops aliases, and re-quotes already-unescaped values into output that fails
to re-parse).

Order of implementation, each stage independently testable:

1. block layout (indent, sequence offset, `explicit_start`/`explicit_end`)
2. scalar analysis and quoting choice (when is a plain scalar legal here?)
3. block scalar headers and chomping (`|`, `|-`, `|+`, `>`, explicit indent indicator)
4. flow layout
5. line folding and `width`
6. anchors, aliases, tags, `%YAML`/`%TAG` directives
7. trivia interleaving

**Invariant (the acceptance criterion for the whole crate):** for a node loaded and not
mutated, the emitter reproduces `raw` verbatim and re-emits trivia in source position, so
`load → dump` is byte-identical to the input. Style decisions are only consulted for nodes the
user constructed or modified. §2.5 says how far that reaches: not just the lexemes, but
everything between them.

**Emitter options** (`EmitOptions`): `map_indent` (default 2), `seq_indent` (default 2),
`seq_offset` (default 0), `width` (default 80), `line_break` (`\n`/`\r\n`/`\r`), `explicit_start`,
`explicit_end`, `default_flow_style`, `canonical`, `preserve_quotes`.

### 2.5 Record the separation, do not reconstruct it

The rule the "spelling" fields of §2 all come from. Applying it is the single change that
moved the `yaml-test-suite` round-trip score from **209 of 308 to 302 of 308** — measured by
running `cargo test -p yamluna-core --test proptest_roundtrip
yaml_test_suite_round_trips_byte_for_byte -- --nocapture` at `1e3b22d` and at `8b05b39`:

> **Whatever the source wrote *between* two lexemes is a fact about the document. Record it
> verbatim. Never derive it from the two lexemes it sits between.**

A document is not a sequence of nodes. It is a sequence of nodes *and the gaps between them*,
and the gaps carry as much of the author's intent as the nodes do — which column a key is
aligned to, whether a flow sequence has a trailing comma, whether the space before a `:` is
one space or seven, whether a tag sits on the node's line or the line above it. None of that
follows from the tree. A model that keeps only the tree has to *invent* the gaps on the way
out, and an invention that is right for one file is wrong for the next.

The failure mode is specific and it is not a crash: the output is valid YAML, holds the right
data, and has a diff. The reconstruction is *plausible* — `date   : 2001-01-23` comes back as
`date:    2001-01-23`, same columns, different spelling — which is what makes it so easy to
ship. Every gap in the emitter that took a real debugging session had this shape.

So a gap gets a field of its own, sized to the gap and not to a node:

| the gap | the field |
|---|---|
| between a key and its `:` | `Entry::colon` |
| between a `&anchor` or a tag and the node it decorates | `Node::anchor_at`, `Node::tag_at`, `Node::tag_first` |
| between a flow collection's own lexemes — brackets, commas, colons | `Node::flow_seps` |
| between a block scalar's `\|`/`>` header and the properties above it | `Node::header_at` |
| between the `%` lines (or a `...` that ends no document) and the `---` | `Document::directives_raw`, `Document::tags_before_version` |
| after the last lexeme of a line, and inside any line holding a TAB | `Document::line_space` |
| after the last lexeme of the stream | `Document::stream_tail` |
| inside a scalar's own lexeme | `Node::raw` |

Three consequences the rest of the model has to respect:

1. **A recorded gap is verbatim, not normalised.** `flow_seps` keeps the TAB in `[a<TAB>, b]`,
   `directives_raw` keeps the two spaces in `%YAML  1.1`. The moment a recorder "tidies" its
   input it has become a reconstructor with extra steps.
2. **A comment's text comes out of the run; its position does not.** A comment is trivia
   (§2.1) and is written from its slot, so `flow_seps` holds the separation with the comment
   *text* removed — but with a bare `#` left where it stood. Without the mark, a comment that
   *splits* a run takes the rest of it along: the `,` in `[ word1\n# c\n, word2]` was written
   below the comment and would come back above it. The mark is what lets the run go back
   around the comment, and it is why `6HB6` and `7TMG` round-trip.
3. **Empty means "not recorded", and nothing else.** A user edit invalidates the spelling of
   the region it touched, so an edited collection clears `flow_seps` and the emitter lays it
   out from the options instead. There is no third state — no stale vector that outlived the
   `children` it described — which is what keeps a mutation from emitting punctuation for a
   lexeme that is no longer written.

The same rule applies at the FFI boundary (§3): **a recorded fact that does not cross it is a
fact the Python API loses**, no matter how carefully the core kept it. That is not
hypothetical — at `8b05b39`, `Entry::colon`, `Node::anchor_at`, `Node::tag_at` and
`Document::line_space` were recorded and emitted correctly by the core while having no record
slot, and the suite scored **302/308 through `parse`/`emit` against 202/308 through
`YAML().load_all`/`.dump_all`**. Giving them slots is what closed most of that gap; §6.5 is the
acceptance rule that keeps the two numbers measured side by side so it cannot silently
reopen.

---

## 3. `yamluna-py` — the boundary

The record types are defined **once, in Python**, in `python/yamluna/_record.py` as plain
`__slots__` classes. Rust imports that module once, caches `Py<PyType>` for each, and constructs
instances through the C API on load; on dump it reads their attributes. One definition, no
pyclass/dataclass duplication, and the whole Python layer stays testable before the extension is
ever built.

```python
# python/yamluna/_record.py  -- the FFI contract, owned by Python
class Node:
    __slots__ = ('kind', 'style', 'anchor', 'tag', 'value', 'raw',
                 'line', 'col', 'children', 'merge', 'explicit',
                 'before', 'eol', 'inner', 'after',
                 'tag_first', 'flow_seps', 'anchor_at', 'tag_at', 'header_at', 'colon')
    # -- meaning --
    kind: int                             # 0 scalar, 1 sequence, 2 mapping, 3 alias
    style: int                            # ScalarStyle for scalars, BLOCK/FLOW for collections
    anchor: str | None                    # `&name` defined, or `*name` referenced for an alias
    tag: tuple[str, str, str] | None      # (handle, suffix, resolved) as written
    value: str | None                     # cooked scalar value
    line: int; col: int                   # 0-based
    children: list[int]                   # node indices: seq items, or k,v,k,v for mappings
    merge: list[int]                      # positions in `children` holding a `<<` key
    explicit: list[int]                   # positions in `children` holding a `? key`
    before: list[Trivia]; eol: Trivia | None
    inner: list[Trivia]; after: list[Trivia]
    # -- spelling (§2.5) --
    raw: str | None                       # source lexeme, verbatim; byte-exact round trip
    tag_first: bool                       # `!!str &a v`, not `&a !!str v`
    flow_seps: list[str]                  # a flow collection's runs; len(children) + 1, or []
    anchor_at: tuple[int, int] | None     # where the `&anchor` was written
    tag_at: tuple[int, int] | None        # where the tag was written
    header_at: tuple[int, int] | None     # where a block scalar's `|`/`>` header was written
    colon: list[tuple[int, int] | None]   # where each entry's `:` was written; [] if unrecorded

class Trivia:
    __slots__ = ('text', 'own_line', 'col', 'blank_lines')
    # a comment (`text` set, `blank_lines == 0`) or a run of blank lines (`blank_lines > 0`)

class Doc:
    __slots__ = ('version', 'tag_directives', 'explicit_start', 'explicit_end',
                 'root', 'nodes', 'leading', 'trailing',
                 'bom', 'final_line_break', 'tags_before_version',
                 'directives_raw', 'stream_tail', 'line_space')
    # -- meaning --
    version: tuple[int, int] | None
    tag_directives: list[tuple[str, str]]
    explicit_start: bool; explicit_end: bool
    root: int | None; nodes: list[Node]
    leading: list[Trivia]; trailing: list[Trivia]
    # -- spelling (§2.5) --
    bom: bool                             # first document of the stream only
    final_line_break: bool                # last document of the stream only
    tags_before_version: int              # `%TAG` lines written above the `%YAML` line
    directives_raw: tuple[str, int] | None  # the raw region above the doc, + how many `leading` it ate
    stream_tail: str                      # trailing white space no line break closes
    line_space: dict[int, str]            # {0-based line: verbatim} for TAB / trailing-space lines
```

`Entry` has no record class: a mapping's entries are flattened into `children` as
`k, v, k, v, …`, and the per-entry facts of §2 travel as parallel lists indexed off that —
`merge` and `explicit` hold the `children` position of the key, and `colon` holds one slot per
entry in entry order. The flat shape is what keeps the boundary one allocation per node.

**The spelling fields are opaque to the Python layer.** It never reads `raw`, `flow_seps`,
`colon`, `anchor_at`, `tag_at`, `header_at`, `directives_raw`, `stream_tail` or `line_space`; it
carries them
back unchanged for a node it did not change, and drops them for one it did. That is the §2.5
rule at the seam, and it is the whole reason the record list is this long: **every recorded fact
needs a slot, or the Python API loses what the core kept.** Measured — the suite round trip
scores 308/308 through `yamluna_core::{parse, emit}` and 306/308 through
`YAML().load_all`/`.dump_all`, and `tests/test_suite_roundtrip.py` is the gate that keeps the
difference visible instead of letting it grow again. The seam's own half of that is asserted
directly: `emit(parse(x))` through these classes is byte-identical to `parse`-then-`emit`
inside Rust for all 308 cases (§6.6) and for all 41 corpus files
(`tests/test_bindings.py`), so a field that stops crossing fails there rather than at the
headline number.

```python
# yamluna._yamluna, the Rust extension
def parse(source: str, *, allow_duplicate_keys: bool = True,
          name: str = '<unicode string>') -> list[Doc]: ...
def emit(docs: list[Doc], opts: EmitOptions) -> str: ...
def _roundtrip_in_rust(source: str, opts: EmitOptions) -> str: ...
```

`_roundtrip_in_rust` never touches a record: it parses and emits entirely inside the core, so
`tests/test_bindings.py` can subtract it from `emit(parse(...))` and get exactly what the
records lose. It exists for that subtraction and for nothing else.

- `Python::detach` (`allow_threads` before PyO3 0.26) around parse and emit. The core touches
  nothing Python, so this is trivially safe and gives genuinely parallel loads — something
  ruamel cannot do.
- Errors cross as a structured `ParseError { kind, message, line, col, index }`: `line` and
  `col` are 0-based, `index` is a **char** offset after the BOM was stripped (§1.5), and `kind`
  is an `ErrorKind` so the Python layer raises the right class from its own hierarchy without
  string-matching. `ErrorKind` has one variant today, `Scanner` — everything the core can fail
  on is lexical or syntactic. `ComposerError` and `DuplicateKeyError` are raised by the Python
  layer from facts the core *records* rather than errors it raises (`Document::duplicate_keys`,
  an alias with no anchor in scope), which is why they need no variant here.
- `abi3-py311` wheels (`abi3-py311` in `crates/yamluna-py/Cargo.toml`, `cp311-abi3` in the
  filename), Python 3.11+. Freethreaded `cp313t`/`cp314t` builds are **not built yet**: abi3
  does not cover `Py_GIL_DISABLED`, so they need their own matrix entry, and the wheel job in
  `.github/workflows/ci.yml` builds only the abi3 wheel today.

---

## 4. Python layer

### 4.1 Object model

`CommentedMap(dict)`, `CommentedSeq(list)`, `CommentedSet`, `CommentedKeySeq`/`CommentedKeyMap`
(tuple-based, so complex keys stay hashable), `TaggedScalar`; scalar types subclassing `str`
(`LiteralScalarString`, `FoldedScalarString`, `SingleQuotedScalarString`,
`DoubleQuotedScalarString`, `PlainScalarString`), `int` (`ScalarInt`, `HexInt`, `OctalInt`,
`BinaryInt`), `float` (`ScalarFloat`), and `ScalarBoolean`.

**They subclass the builtins.** That is the whole source of the ergonomics: `isinstance(x, dict)`,
`json.dumps(x)`, `pickle`, `copy.deepcopy`, `x == {"a": 1}`, and user code hanging arbitrary
attributes off nodes all work for free. `#[pyclass(mapping)]` fails every one of those;
`#[pyclass(extends=PyDict)]` stores the data twice and desynchronises on any `dict` method you
forget to override.

Per-node attributes, ruamel-shaped: `.ca` (`Comment`), `.lc` (`LineCol`), `.fa` (`Format`),
`.anchor` (`Anchor`), `.tag`, `.merge`. `.ca.items` is a **projection** over the
identity-keyed store (§2.1), not the store itself.

### 4.2 `YAML`

`YAML(typ='rt')` — `typ` accepts only `'rt'`; anything else raises with a message pointing at
this scope decision. `.load`, `.load_all`, `.dump`, `.dump_all`, `.indent(mapping=, sequence=,
offset=)`, `.preserve_quotes`, `.default_flow_style`, `.width`, `.explicit_start`,
`.explicit_end`, `.allow_duplicate_keys`, `.version`, and the context-manager dump form.

### 4.3 Errors

`YAMLError` → `MarkedYAMLError` → `{ScannerError, ParserError, ComposerError, ConstructorError,
RepresenterError, EmitterError, DuplicateKeyError}`, plus `YAMLStreamError`. `Mark` carries
`.name`, `.line`, `.column`, `.index`, `.buffer`, `.pointer` and `.get_snippet()`. Defined in
Python; Rust imports the module and raises.

---

## 5. The tag registry

This is the part that is deliberately *not* ruamel-compatible, because ruamel's design here is
the bug we are fixing.

### 5.1 The bug

`ruamel.yaml.YAML.register_class(cls)` keys the constructor/representer registry on the tag
**name alone** (`'!' + cls.__name__`). Two libraries — or two modules inside one library — that
both define `Circuit` overwrite each other, and which one wins depends on import order. The
loser silently constructs the wrong class.

### 5.2 Registration

The registry key is the **fully qualified class path**, so registration can never overwrite:

```python
key = f"{cls.__module__}.{cls.__qualname__}"     # "libx.circuits.Circuit"
```

Each entry carries:

| field      | default                            | override                                  |
|------------|------------------------------------|-------------------------------------------|
| `tag_name` | `cls.__name__`                     | `cls.yaml_tag` or `register_class(tag=)`  |
| `source`   | `cls.__module__.split('.')[0]`     | `cls.yaml_source` or `register_class(source=)` |

**Automatic promotion.** If two registered classes end up with the same `(source, tag_name)`
pair, *both* have their `source` promoted to the full module path (`libx.circuits` and
`libx.gates`). Promotion is recomputed on every registration and is a pure function of the
registry contents, so it is deterministic for a given set of installed libraries. An explicit
`source=` pins the value and is never promoted.

The wire identity of a class is therefore the global tag `tag:{source}/{tag_name}`, e.g.
`tag:libx/Circuit`.

### 5.3 Wire format — `%TAG` directives

The namespace is written with YAML's own namespacing mechanism. It costs one directive line per
source per document, pollutes no user data, works identically for scalar-, sequence- and
mapping-shaped classes, and any conformant YAML parser round-trips it.

Single source in the document — the tag is bare:

```yaml
%TAG ! tag:libx/
---
main: !Circuit
  qubits: 2
```

Two sources — the most-used source keeps the primary `!` handle, the rest get named handles
derived from the source (sanitised to `[A-Za-z0-9-]`, deduplicated by suffixing a digit):

```yaml
%TAG ! tag:libx/
%TAG !liby! tag:liby/
---
a: !Circuit       {qubits: 2}      # tag:libx/Circuit
b: !liby!Circuit  {n: 3}           # tag:liby/Circuit
```

Two modules inside one library — promotion (§5.2) makes the sources distinct, and the same rule
applies:

```yaml
%TAG ! tag:libx.circuits/
%TAG !libx-gates! tag:libx.gates/
---
a: !Circuit
  qubits: 2
b: !libx-gates!Circuit
  width: 1
```

(That block is the verbatim output of `examples/custom_classes.py`, which is what the handle
derivation actually produces: `libx.gates` sanitised to `libx-gates`, not shortened.)

**When no directive is written.** If the document uses no registered classes at all, no `%TAG`
line is emitted. A document that *does* use registered classes always declares its sources, even
when only one is in play — that is what makes the file self-describing and its meaning stable
when the reader later installs a second library that also defines `Circuit`.

### 5.4 Loading

1. A tag that resolves through a `%TAG` directive to `tag:{source}/{name}` is looked up by
   `(source, tag_name)`. Exactly one match → construct. No match → `ConstructorError` naming the
   unresolved tag.
2. A **bare** `!Name` with no directive in scope (a hand-written file) is looked up by
   `tag_name` alone. Exactly one registered class → construct it. More than one → `ConstructorError`
   listing every candidate's fully qualified path and telling the user to add a `%TAG` line or
   register with an explicit `source=`. **Never guess.** Silently picking one is precisely the
   ruamel behaviour this design exists to eliminate.
3. An unregistered tag round-trips untouched as a tagged `CommentedMap`/`CommentedSeq`/
   `TaggedScalar`, with the tag preserved exactly as written.

### 5.5 API

```python
yaml.register_class(Circuit)                       # tag !Circuit, source from __module__
yaml.register_class(Circuit, tag='Circ')           # explicit tag name
yaml.register_class(Circuit, source='qilisdk')     # pinned source, never promoted
@yaml.register                                     # decorator form
class Circuit: ...
```

`to_yaml`/`from_yaml` classmethod hooks keep ruamel's signatures so existing classes port
unchanged.

---

## 6. Acceptance

1. `crates/yamluna-scanner`: the upstream unit tests and all 402 `yaml-test-suite` cases stay
   green after every patch.
2. `yamluna-core`: for every file in the corpus, `load → dump` is **byte-identical to the input**
   when nothing is mutated. This is stricter than ruamel, which normalises some inputs.
3. Differential harness: for the same corpus, compare against `ruamel.yaml==0.19.1`. Every
   divergence is either a fixed bug (recorded in `docs/DIVERGENCES.md` with the reason) or a
   defect in yamluna.
4. Mutation tests: comments stay attached to the right node across `insert`, `del`, `pop`,
   `move_to_end` and key rename — the cases where ruamel's index-keyed model drifts.
5. **The same invariant at both ends of the FFI.** `yaml-test-suite` is round-tripped twice: by
   `yamluna_core::{parse, emit}` (`crates/yamluna-core/tests/proptest_roundtrip.rs`) and by
   `YAML().load_all` / `.dump_all` (`tests/test_suite_roundtrip.py`), over the same case list,
   extracted the same way. Both scores are published, separately, and every case either passes
   or is on that harness's `KNOWN_GAPS` with a cause. **The second number is the library's**;
   the first is only the core's, and the difference between them is what the boundary and the
   object model lose (§2.5). Measured on this commit: 308/308 and 306/308 — the core's
   `KNOWN_GAPS` is empty and the Python one holds two permanent entries. Quoting one of them
   alone is quoting the wrong layer.
6. **The boundary loses nothing, and it is asserted rather than believed.** The same 308 cases
   go through `emit(parse(x))` via the `_record` classes and through `parse`/`emit` inside
   Rust, and the two must be byte-identical
   (`test_the_record_seam_loses_nothing_over_the_suite`). While it holds, rule 5's difference
   is the object model (§4.1) alone — currently `2JQS` and `X38W`, both a `dict` that cannot
   hold one key twice — and any new Python-side gap that is *not* that shape is a record slot
   that went missing.
