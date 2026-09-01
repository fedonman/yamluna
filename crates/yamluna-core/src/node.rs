//! The round-trip document model: documents, nodes, entries, and where the source wrote them.
//!
//! The model is owned and `'static`. The source text is kept beside the tree and is never
//! borrowed by it, so a node can cross the FFI boundary and a subtree can migrate from one
//! document into another.
//!
//! Fields come in two kinds. Most say what the document means: a version, a tag, a scalar's
//! value, an entry's key. The rest say only how the source spelled it: [`Node::raw`],
//! [`Entry::colon`], [`Node::anchor_at`], [`Node::tag_at`], [`Node::header_at`],
//! [`Node::tag_first`], [`Node::flow_seps`], [`Document::directives_raw`],
//! [`Document::tags_before_version`], [`Document::stream_tail`] and [`Document::line_space`].
//! Each of those holds what the source wrote *between* two lexemes, verbatim, and together they
//! are what makes an unmodified round trip byte-exact. A gap is a fact about the document: it
//! is recorded as written, never derived from the lexemes on either side of it.

use std::collections::HashMap;

use crate::trivia::{Trivia, Trivia4};
pub use yamluna_scanner::ScalarStyle;

/// Index of a [`Node`] in [`Document::nodes`]. Valid only for the document it came from.
pub type NodeId = u32;

/// A 0-based line and column.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Position {
    /// 0-based line.
    pub line: u32,
    /// 0-based column, in characters.
    pub col: u32,
}

impl Position {
    /// Builds a position from a scanner marker, whose line is 1-based and column 0-based.
    #[must_use]
    pub(crate) fn from_marker(m: yamluna_scanner::Marker) -> Self {
        Self {
            line: u32::try_from(m.line())
                .unwrap_or(u32::MAX)
                .saturating_sub(1),
            col: u32::try_from(m.col()).unwrap_or(u32::MAX),
        }
    }
}

/// How a node was written.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Style {
    /// A scalar, in one of the five YAML scalar styles: plain, single quoted, double quoted,
    /// literal (`|`) or folded (`>`).
    Scalar(ScalarStyle),
    /// A collection delimited by indentation.
    Block,
    /// A collection delimited by brackets: `[a, b]`, `{a: 1}`.
    Flow,
}

/// A tag as written and as resolved: a round trip needs the spelling, a tag registry needs the
/// resolution.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NodeTag {
    /// The handle as written (`!`, `!!`, `!e!`), or `""` when the tag was written verbatim
    /// (`!<uri>`) or no handle for its prefix is in scope.
    pub handle: String,
    /// The tag suffix as written. For a verbatim tag this is the whole URI.
    pub suffix: String,
    /// The full resolved tag (`tag:example.com,2000:custom`, or `!local` for a local tag).
    pub resolved: String,
}

/// One `key: value` pair of a mapping.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    /// The key node.
    pub key: NodeId,
    /// The value node. An entry the source wrote without a value (`key:`) still has one: a
    /// scalar whose [`Node::raw`] is `""`.
    pub value: NodeId,
    /// Whether the key is a merge key (`<<`). Recorded, never expanded here, so a dump writes
    /// `<<: *base` back rather than the mapping it stands for.
    pub merge: bool,
    /// Whether the entry was written with the explicit `? key` / `: value` indicators.
    // Without this, `? [a, b]\n: v` cannot be re-emitted as written.
    pub explicit: bool,
    /// Where the `:` between the key and the value was written. `None` when the source wrote
    /// none (`{a: 1, b}`) or the entry was built rather than loaded, and the emitter then
    /// places the `:` itself.
    // The gap between a key and its `:` is white space the model held nowhere, so
    // `date   : 2001-01-23` came back as `date:    2001-01-23`: the same columns, the wrong
    // spelling. Recorded, the emitter echoes it instead of reconstructing it.
    pub colon: Option<Position>,
}

/// What a node is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NodeKind {
    /// A scalar. See [`Node::value`] and [`Node::raw`].
    Scalar,
    /// A sequence.
    Sequence {
        /// The items, in source order.
        items: Vec<NodeId>,
    },
    /// A mapping.
    Mapping {
        /// The entries, in source order. Duplicate keys are kept, not merged.
        entries: Vec<Entry>,
    },
    /// An alias (`*name`). Never a clone of the target, which is what lets recursive anchors
    /// load.
    Alias {
        /// The anchor name, without the `*`.
        anchor: String,
    },
}

/// A node of the document tree.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Node {
    /// What the node is.
    pub kind: NodeKind,
    /// The anchor (`&name`), without the `&`. `None` for a node the source did not anchor.
    pub anchor: Option<String>,
    /// The tag, or `None` for an untagged node.
    pub tag: Option<NodeTag>,
    /// Where the `&anchor` was written, or `None` for a node with none and for one the user
    /// built, which the emitter places itself.
    // [`Self::pos`] is the node's *content*, so a property sits ahead of it at a line and
    // column nothing else records: without these an anchored key lands at the cursor's column
    // rather than the mapping's, the gap after `&anchor` becomes padding to the content column
    // rather than the space the source wrote, and a property the source put on a line of its
    // own is pulled up onto the node's.
    pub anchor_at: Option<Position>,
    /// Where the tag was written. See [`Self::anchor_at`].
    pub tag_at: Option<Position>,
    /// Where a block scalar's header (`|`, `>-`, `|2`, ...) was written, or `None` for any
    /// other node and for one the user built.
    // Same reason as [`Self::anchor_at`]: [`Self::pos`] is the scalar's *body*, so the header
    // is a lexeme on a line and column of its own. The source may put it below the node's
    // properties (`!foo` on one line, `>1` on the next), which nothing else records.
    pub header_at: Option<Position>,
    /// Whether the tag was written before the anchor (`!!str &a v`, not `&a !!str v`).
    // YAML allows either order and neither is canonical, so the emitter has to be told which
    // one the source used.
    pub tag_first: bool,
    /// The style the node was written in.
    pub style: Style,
    /// Cooked scalar value (escapes resolved, block scalars folded). `Scalar` nodes only:
    /// `None` on a collection, on an alias, and on a node built with [`Node::new`] until you
    /// set one.
    pub value: Option<String>,
    /// The lexeme exactly as written, including quotes and block-scalar header. `Scalar` nodes
    /// only. This is what makes an unmodified round trip byte-exact. `None` on a collection, on
    /// an alias, and on a scalar the user built, which the emitter then writes from
    /// [`Self::value`] in the style it chooses.
    ///
    /// For a block scalar the header and the body are joined by the line break that separated
    /// them in the source, any comment on the header line having been lifted out into
    /// [`Trivia4::eol`]; the single line break that terminates the last body line is not
    /// included, so the emitter writes `raw` and then its own break exactly as for any other
    /// scalar.
    ///
    /// An implicit empty node (`key:` with nothing after it) has `Some("")`.
    pub raw: Option<String>,
    /// 0-based line and column of the node's first character. A node the user built carries
    /// `Position::default()`.
    pub pos: Position,
    /// What the source wrote *between* this flow collection's lexemes: one run before each
    /// child and one before the closing bracket, so a recorded vector is `children + 1` long. A
    /// single pair written with no brackets of its own (`[a: 1]`, `[? a : b]`, `[&c c: d]`) is
    /// the exception: it has no closing bracket to separate from and records exactly `children`
    /// runs. That length is the one fact that says the pair wrote no brackets, and a mapping's
    /// children always come in pairs, so a stale vector cannot be mistaken for it.
    ///
    /// Each run is the separation verbatim, white space and `,` and `:` and `?`, with a bare
    /// `#` where a comment stood, because a comment's text is trivia and is written from the
    /// slot it was filed in. Keeping the mark means the run can go back *around* the comment:
    /// `[ word1\n# c\n, word2]` wrote its `,` below the comment, not above it. Anything else (a
    /// node's own `&anchor` or tag) ends the run: the emitter writes that from the node.
    ///
    /// It is the one fact that tells `[1, 2]` from `[1, 2, ]` from `[ 1 , 2 ]`, says which key
    /// of `{a: 1, b}` was written with no `:`, and remembers that the gap in `[a\t, b]` was a
    /// TAB. Empty for a collection the user built or edited, which the emitter lays out
    /// instead. Empty is the only "not recorded" state, so a stale vector cannot survive an
    /// insertion or a deletion.
    pub flow_seps: Vec<String>,
    /// The node's four trivia slots.
    pub trivia: Trivia4,
}

impl Node {
    /// Builds a bare node of the given kind and style, with no anchor, tag, value or trivia.
    ///
    /// The node records no source spelling, so the emitter lays it out from
    /// [`crate::EmitOptions`] rather than echoing it.
    #[must_use]
    pub fn new(kind: NodeKind, style: Style) -> Self {
        Self {
            kind,
            anchor: None,
            tag: None,
            anchor_at: None,
            tag_at: None,
            header_at: None,
            tag_first: false,
            style,
            value: None,
            raw: None,
            pos: Position::default(),
            flow_seps: Vec::new(),
            trivia: Trivia4::default(),
        }
    }

    /// Returns the children of a collection, in source order (`key, value, key, value` for a
    /// mapping). Empty for a scalar or an alias.
    #[must_use]
    pub fn children(&self) -> Vec<NodeId> {
        match &self.kind {
            NodeKind::Scalar | NodeKind::Alias { .. } => Vec::new(),
            NodeKind::Sequence { items } => items.clone(),
            NodeKind::Mapping { entries } => {
                entries.iter().flat_map(|e| [e.key, e.value]).collect()
            }
        }
    }

    /// Returns whether the node is a sequence or a mapping.
    #[must_use]
    pub fn is_collection(&self) -> bool {
        matches!(
            self.kind,
            NodeKind::Sequence { .. } | NodeKind::Mapping { .. }
        )
    }
}

/// A `%TAG` directive line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TagDirective {
    /// The handle, including the bangs (`!`, `!e!`).
    pub handle: String,
    /// The prefix the handle expands to.
    pub prefix: String,
}

/// A key that was already present in the same mapping.
///
/// The loader records every one of them and drops nothing; the Python layer raises
/// `DuplicateKeyError` or warns, according to its `allow_duplicate_keys` setting.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DuplicateKey {
    /// A canonical rendering of the key, used only for reporting.
    pub key: String,
    /// Where the key was first seen.
    pub first: Position,
    /// Where it was seen again.
    pub again: Position,
}

/// One YAML document of a stream.
///
/// [`crate::parse`] returns one of these per document in the source; [`crate::emit`] writes a
/// slice of them back out.
// Four independent facts about the source text; grouping them into a struct would buy nothing.
#[allow(clippy::struct_excessive_bools)]
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Document {
    /// The `%YAML` version directive as `(major, minor)`, or `None` when the document declared
    /// none.
    pub version: Option<(u32, u32)>,
    /// The `%TAG` directive lines, in source order.
    pub tag_directives: Vec<TagDirective>,
    /// The raw region above the document: every whole line from the last thing consumed through
    /// the line before the document's first token, without the break that ends it, together
    /// with how many of [`Self::leading`]'s trivia were read from inside it. `None` unless the
    /// region holds a line the model cannot spell out again: a `%` directive, or a `...` that
    /// ends no document of its own. The emitter echoes the region as written and skips that
    /// many leading trivia, having written them with it.
    // [`Self::version`] and [`Self::tag_directives`] are the *semantics* of a directive line,
    // never its spelling: `%YAML  1.1` is the same version as `%YAML 1.1`, a reserved directive
    // (`%FOO bar`) has no model at all, and a comment may sit on any of those lines or between
    // them. A bare `...` is the same problem with nothing at all behind it: the parser gives no
    // event for a document-end marker that closes no document (`explicit_end` is the marker
    // that *does* close one), so the only record of it is the line. The trivia inside the
    // region stay in `leading` so the comment API still sees them, and the count is what tells
    // the emitter it has already written them.
    pub directives_raw: Option<(String, usize)>,
    /// How many of [`Self::tag_directives`] were written *above* the `%YAML` line; the rest
    /// were written below it. `0` when the version came first, or when there is no version.
    // The two kinds of directive interleave freely on the page and the model keeps them in
    // separate fields, so this is what says where the `%YAML` line sat among them.
    pub tags_before_version: usize,
    /// Whether the document was introduced by `---`.
    pub explicit_start: bool,
    /// Whether the document was terminated by `...`.
    pub explicit_end: bool,
    /// Whether the stream began with a UTF-8 BOM. Only ever set on the first document; the
    /// loader strips the BOM and the emitter writes it back, so a BOM-prefixed file round-trips
    /// byte for byte.
    pub bom: bool,
    /// Whether the source ended with a line break. Only meaningful on the last document of a
    /// stream; without it a dump appends a newline the input did not have.
    pub final_line_break: bool,
    /// The white space the source ends with that no line break closes: a trailing run on the
    /// last line, and a last line that holds nothing but padding. Only meaningful on the last
    /// document of a stream, and always empty when [`Self::final_line_break`] is set, which
    /// already says the stream ends in a break.
    // `line_space` puts a line's tail back when the emitter breaks the line; at the end of a
    // stream it never does, so the last line's white space had nowhere to live.
    pub stream_tail: String,
    /// The root node, or `None` for a stream of nothing but trivia.
    pub root: Option<NodeId>,
    /// The node arena. [`NodeId`] is an index into it.
    pub nodes: Vec<Node>,
    /// Trivia before the document's first token.
    ///
    /// At most one of these is an end-of-line comment (`own_line: false`), and if present it is
    /// last: it is the comment that follows `---` on the same line.
    pub leading: Vec<Trivia>,
    /// Trivia after the document's last node.
    ///
    /// An end-of-line comment here follows `...` on the same line, and is first.
    pub trailing: Vec<Trivia>,
    /// Every duplicate key found while loading, in source order.
    pub duplicate_keys: Vec<DuplicateKey>,
    /// The source lines whose white space the emitter cannot reproduce from a column alone: the
    /// ones holding a TAB, and the ones that end in white space. Keyed by 0-based line,
    /// verbatim, without the break. Like [`Self::bom`] this is a fact about the *stream*, so
    /// only the first document of one carries it, and a stream with no such line carries
    /// nothing.
    // White space between two lexemes belongs to neither, and white space at the end of a line
    // belongs to nothing at all, so no node owns either: reaching a recorded column with spaces
    // loses the TAB the source reached it with, and dropping padding no content follows loses
    // the line's tail. Read only while the model still matches the page, and only for a run
    // that is white space on both sides, so an edited document can pick up neither.
    pub line_space: HashMap<u32, String>,
}

impl Document {
    /// Returns the node with the given id.
    ///
    /// # Panics
    ///
    /// Panics if `id` is not a node of this document.
    #[must_use]
    pub fn node(&self, id: NodeId) -> &Node {
        &self.nodes[id as usize]
    }

    /// Returns the node with the given id, mutably.
    ///
    /// # Panics
    ///
    /// Panics if `id` is not a node of this document.
    pub fn node_mut(&mut self, id: NodeId) -> &mut Node {
        &mut self.nodes[id as usize]
    }

    /// Pushes a node into the arena and returns its id.
    ///
    /// # Panics
    ///
    /// Panics if the document already holds `u32::MAX` nodes.
    pub fn push(&mut self, node: Node) -> NodeId {
        let id = NodeId::try_from(self.nodes.len()).expect("too many nodes");
        self.nodes.push(node);
        id
    }

    /// Returns every trivium of the document, in the order an emitter would write them.
    ///
    /// `crates/yamluna-core/tests/corpus.rs` checks the loader against this: each comment of
    /// the source must appear exactly once, in source order.
    ///
    /// # Panics
    ///
    /// Panics if a node names a child that is not in [`Self::nodes`], which cannot happen for a
    /// document built by [`crate::parse`].
    #[must_use]
    pub fn trivia_in_order(&self) -> Vec<&Trivia> {
        let mut out: Vec<&Trivia> = self.leading.iter().collect();
        if let Some(root) = self.root {
            self.walk_trivia(root, &mut out);
        }
        out.extend(self.trailing.iter());
        out
    }

    /// Appends the trivia of `id` and of its subtree to `out`, in emission order.
    fn walk_trivia<'a>(&'a self, id: NodeId, out: &mut Vec<&'a Trivia>) {
        let n = self.node(id);
        // A collection's `eol` sits on the line that introduces it, ahead of `before`, which
        // sits on the lines between that and the first child.
        if n.is_collection() {
            out.extend(n.trivia.eol.iter());
            out.extend(n.trivia.before.iter());
        } else {
            out.extend(n.trivia.before.iter());
            out.extend(n.trivia.eol.iter());
        }
        out.extend(n.trivia.inner.iter());
        for child in n.children() {
            self.walk_trivia(child, out);
        }
        out.extend(n.trivia.after.iter());
    }
}
