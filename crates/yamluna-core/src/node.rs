//! The round-trip document model of DESIGN §2.
//!
//! Owned and `'static`: the source text is kept *beside* the tree, never borrowed by it, so a node
//! can cross the FFI boundary and a subtree can migrate between documents.

use std::collections::HashMap;

use crate::trivia::{Trivia, Trivia4};
pub use yamluna_scanner::ScalarStyle;

/// Index of a [`Node`] in [`Document::nodes`].
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
    /// Build a position from a scanner marker, whose line is 1-based and column 0-based.
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
    /// A scalar, in one of the five YAML scalar styles.
    Scalar(ScalarStyle),
    /// An indentation-delimited collection.
    Block,
    /// A bracket-delimited collection.
    Flow,
}

/// A tag as written *and* as resolved: round-trip needs the former, the tag registry the latter.
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
    /// The value node.
    pub value: NodeId,
    /// Whether the key is a merge key (`<<`). Recorded, never expanded (DESIGN §2.3).
    pub merge: bool,
    /// Whether the entry was written with the explicit `? key` / `: value` indicators.
    ///
    /// Not in DESIGN §2, but without it `? [a, b]\n: v` cannot be re-emitted as written.
    pub explicit: bool,
    /// Where the `:` between the key and the value was written, or `None` when the source wrote
    /// none (`{a: 1, b}`) or the entry was built rather than loaded.
    ///
    /// Not in DESIGN §2. The gap between a key and its `:` is white space the model held nowhere,
    /// so `date   : 2001-01-23` came back as `date:    2001-01-23` — the same columns, the wrong
    /// spelling. Recorded, the emitter echoes it instead of reconstructing it.
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
    /// An alias (`*name`). Never a clone of the target, which is what lets recursive anchors load.
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
    /// The anchor (`&name`), without the `&`.
    pub anchor: Option<String>,
    /// The tag.
    pub tag: Option<NodeTag>,
    /// Where the `&anchor` was written, or `None` for a node with none or one the user built.
    ///
    /// Not in DESIGN §2. [`Self::pos`] is the node's *content*, so a property sits ahead of it at
    /// a line and column nothing else records: without these an anchored key lands at the
    /// cursor's column rather than the mapping's, the gap after `&anchor` becomes padding to the
    /// content column rather than the space the source wrote, and a property the source put on a
    /// line of its own is pulled up onto the node's.
    pub anchor_at: Option<Position>,
    /// Where the tag was written. See [`Self::anchor_at`].
    pub tag_at: Option<Position>,
    /// Whether the tag was written *before* the anchor (`!!str &a v`, not `&a !!str v`).
    ///
    /// Not in DESIGN §2. YAML allows either order and neither is canonical, so the emitter has
    /// to be told which one the source used.
    pub tag_first: bool,
    /// The style the node was written in.
    pub style: Style,
    /// Cooked scalar value (escapes resolved, block scalars folded). `Scalar` nodes only.
    pub value: Option<String>,
    /// The lexeme exactly as written, including quotes and block-scalar header. `Scalar` nodes
    /// only. This is what makes an unmodified round trip byte-exact.
    ///
    /// For a block scalar the header and the body are joined by the line break that separated them
    /// in the source, any comment on the header line having been lifted out into
    /// [`Trivia4::eol`]; the single line break that terminates the last body line is *not*
    /// included, so the emitter writes `raw` and then its own break exactly as for any other
    /// scalar.
    ///
    /// An implicit empty node (`key:` with nothing after it) has `Some("")`.
    pub raw: Option<String>,
    /// 0-based line and column of the node's first character.
    pub pos: Position,
    /// What the source wrote *between* this flow collection's lexemes: one run before each child
    /// and one before the closing bracket, so a recorded vector is always `children + 1` long.
    ///
    /// Each run is the separation verbatim -- white space, `,`, `:`, `?` -- with its comments
    /// taken out, because those are trivia and are written from there. Anything else (a node's own
    /// `&anchor` or tag) ends the run: the emitter writes that from the node.
    ///
    /// It is the one fact that tells `[1, 2]` from `[1, 2, ]` from `[ 1 , 2 ]`, says which key of
    /// `{a: 1, b}` was written with no `:`, and remembers that the gap in `[a\t, b]` was a TAB.
    /// Empty for a collection the user built or edited, which the emitter lays out instead -- and
    /// empty is the only "not recorded", so a stale vector cannot survive an insertion or deletion.
    ///
    /// Not in DESIGN §2.
    pub flow_seps: Vec<String>,
    /// The node's four trivia slots.
    pub trivia: Trivia4,
}

impl Node {
    /// A bare node of the given kind and style, with no anchor, tag, value or trivia.
    #[must_use]
    pub fn new(kind: NodeKind, style: Style) -> Self {
        Self {
            kind,
            anchor: None,
            tag: None,
            anchor_at: None,
            tag_at: None,
            tag_first: false,
            style,
            value: None,
            raw: None,
            pos: Position::default(),
            flow_seps: Vec::new(),
            trivia: Trivia4::default(),
        }
    }

    /// The children of a collection, in source order (`key, value, key, value` for a mapping).
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

    /// Whether the node is a sequence or a mapping.
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
/// The loader records every one of them; the Python layer raises `DuplicateKeyError` or warns per
/// `allow_duplicate_keys` (DESIGN §2.3). Nothing is silently dropped.
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
// Four independent facts about the source text; grouping them into a struct would buy nothing.
#[allow(clippy::struct_excessive_bools)]
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Document {
    /// The `%YAML` version directive, if any.
    pub version: Option<(u32, u32)>,
    /// The `%TAG` directive lines, in source order.
    pub tag_directives: Vec<TagDirective>,
    /// The document's directive region, verbatim: every line from the last thing consumed
    /// through the line before `---`, without the break that ends it, together with how many of
    /// [`Self::leading`]'s trivia were read from inside it. `None` when the document has no line
    /// beginning with `%`.
    ///
    /// Not in DESIGN §2. [`Self::version`] and [`Self::tag_directives`] are the *semantics* of a
    /// directive line, never its spelling: `%YAML  1.1` is the same version as `%YAML 1.1`,
    /// a reserved directive (`%FOO bar`) has no model at all, and a comment may sit on any of
    /// those lines or between them. The region is kept as written and the emitter echoes it; the
    /// trivia inside it stay in [`Self::leading`] so the comment API still sees them, and the
    /// count is what tells the emitter it has already written them.
    pub directives_raw: Option<(String, usize)>,
    /// How many of [`Self::tag_directives`] were written *above* the `%YAML` line; the rest were
    /// written below it. `0` when the version came first, or when there is no version.
    ///
    /// Not in DESIGN §2. The two kinds of directive interleave freely on the page and the model
    /// keeps them in separate fields, so this is what says where the `%YAML` line sat among them.
    pub tags_before_version: usize,
    /// Whether the document was introduced by `---`.
    pub explicit_start: bool,
    /// Whether the document was terminated by `...`.
    pub explicit_end: bool,
    /// Whether the stream began with a UTF-8 BOM. Only ever set on the first document; the loader
    /// strips the BOM, and the emitter writes it back (not in DESIGN §2, but a byte-exact round
    /// trip of a BOM-prefixed file needs it).
    pub bom: bool,
    /// Whether the source ended with a line break. Only meaningful on the last document of a
    /// stream; without it a dump appends a newline the input did not have.
    pub final_line_break: bool,
    /// The white space the source ends with that no line break closes: a trailing run on the last
    /// line, and a last line that holds nothing but padding. Only meaningful on the last document
    /// of a stream, and always empty when [`Self::final_line_break`] is set — that flag already
    /// says the stream ends in a break.
    ///
    /// Not in DESIGN §2. `line_space` puts a line's tail back when the emitter breaks the line;
    /// at the end of a stream it never does, so the last line's white space had nowhere to live.
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
    /// ones holding a TAB, and the ones that end in white space. Keyed by 0-based line, verbatim,
    /// without the break. Like [`Self::bom`] this is a fact about the *stream*, so only the first
    /// document of one carries it, and a stream with no such line carries nothing.
    ///
    /// Not in DESIGN §2. White space *between* two lexemes belongs to neither, and white space at
    /// the end of a line belongs to nothing at all, so no node owns either: reaching a recorded
    /// column with spaces loses the TAB the source reached it with, and dropping padding no
    /// content follows loses the line's tail. Read only while the model still matches the page,
    /// and only for a run that is white space on both sides, so an edited document can pick up
    /// neither.
    pub line_space: HashMap<u32, String>,
}

impl Document {
    /// The node with the given id.
    ///
    /// # Panics
    /// Panics if `id` is not a node of this document.
    #[must_use]
    pub fn node(&self, id: NodeId) -> &Node {
        &self.nodes[id as usize]
    }

    /// The node with the given id, mutably.
    ///
    /// # Panics
    /// Panics if `id` is not a node of this document.
    pub fn node_mut(&mut self, id: NodeId) -> &mut Node {
        &mut self.nodes[id as usize]
    }

    /// Push a node into the arena and return its id.
    ///
    /// # Panics
    /// Panics if the document already holds `u32::MAX` nodes.
    pub fn push(&mut self, node: Node) -> NodeId {
        let id = NodeId::try_from(self.nodes.len()).expect("too many nodes");
        self.nodes.push(node);
        id
    }

    /// Every trivium of the document, in the order an emitter would write them.
    ///
    /// The loader's structural invariant is checked against this: each comment of the source must
    /// appear exactly once, in source order.
    #[must_use]
    pub fn trivia_in_order(&self) -> Vec<&Trivia> {
        let mut out: Vec<&Trivia> = self.leading.iter().collect();
        if let Some(root) = self.root {
            self.walk_trivia(root, &mut out);
        }
        out.extend(self.trailing.iter());
        out
    }

    fn walk_trivia<'a>(&'a self, id: NodeId, out: &mut Vec<&'a Trivia>) {
        let n = self.node(id);
        // A collection's `eol` sits on the line that introduces it, ahead of `before`, which sits
        // on the lines between that and the first child.
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
