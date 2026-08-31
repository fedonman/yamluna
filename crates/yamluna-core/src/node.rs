//! The round-trip document model of DESIGN §2.
//!
//! Owned and `'static`: the source text is kept *beside* the tree, never borrowed by it, so a node
//! can cross the FFI boundary and a subtree can migrate between documents.

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
            style,
            value: None,
            raw: None,
            pos: Position::default(),
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
