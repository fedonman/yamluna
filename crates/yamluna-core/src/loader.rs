//! Events → documents (DESIGN §2.3), with the trivia attachment pass of DESIGN §2.2 folded in.
//!
//! This is our own [`Parser`] driver rather than `saphyr::YamlLoader`, which forces a
//! `LinkedHashMap` mapping (no duplicate keys, nowhere to hang per-entry trivia), silently
//! last-wins on duplicate keys, and resolves aliases by cloning the target so `*base` is
//! unrecoverable after load.

use std::collections::HashMap;

use yamluna_scanner::{Event, Parser, ScalarStyle, ScanError, Span, StructureStyle};

use crate::charmap::CharMap;
use crate::node::{
    Document, DuplicateKey, Entry, Node, NodeId, NodeKind, NodeTag, Position, Style, TagDirective,
};
use crate::trivia::{Pending, Trivia};

/// A block scalar's lexeme without the single line break that terminates its last line: the
/// emitter writes that itself, as it does for any other scalar.
fn strip_break(s: &str) -> String {
    s.strip_suffix("\r\n")
        .or_else(|| s.strip_suffix('\n'))
        .unwrap_or(s)
        .to_owned()
}

/// A scanner count (a line, a column, a char index) as a `u32`, saturating rather than wrapping.
fn small(v: usize) -> u32 {
    u32::try_from(v).unwrap_or(u32::MAX)
}

/// What went wrong while parsing.
///
/// The kind is carried structurally so the Python layer never has to classify by string-matching
/// (DESIGN §3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorKind {
    /// A lexical or syntactic error reported by the scanner.
    Scanner,
}

/// A parse failure, with the position in the same coordinates the model uses.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
#[error("{message} at line {} column {}", line + 1, col + 1)]
pub struct ParseError {
    /// Which layer produced the error.
    pub kind: ErrorKind,
    /// Human-readable message, verbatim from the scanner.
    pub message: String,
    /// 0-based line.
    pub line: u32,
    /// 0-based column.
    pub col: u32,
    /// Character offset into the source (after the BOM, if any, was stripped).
    pub index: usize,
}

impl From<ScanError> for ParseError {
    fn from(e: ScanError) -> Self {
        let m = *e.marker();
        Self {
            kind: ErrorKind::Scanner,
            message: e.info().to_owned(),
            line: small(m.line()).saturating_sub(1),
            col: small(m.col()),
            index: m.index(),
        }
    }
}

/// Load every document of `source`.
///
/// A UTF-8 BOM is stripped (`Parser::new_from_str` does not do it) and recorded on the first
/// document. A source that holds nothing but trivia yields one rootless document carrying it.
///
/// # Errors
/// Returns the scanner's error, with its position translated into the model's coordinates.
pub fn parse(source: &str) -> Result<Vec<Document>, ParseError> {
    let (src, bom) = match source.strip_prefix('\u{feff}') {
        Some(rest) => (rest, true),
        None => (source, false),
    };
    let mut docs = Loader::new(src, bom).run()?;
    if let Some(first) = docs.first_mut() {
        first.line_space = line_space(src);
    }
    Ok(docs)
}

/// The lines of `src` whose white space the emitter cannot reproduce from a column alone. See
/// [`Document::line_space`].
fn line_space(src: &str) -> HashMap<u32, String> {
    let mut out = HashMap::new();
    for (i, line) in src.split('\n').enumerate() {
        let line = line.strip_suffix('\r').unwrap_or(line);
        if line.contains('\t') || line.ends_with([' ', '\t']) {
            out.insert(small(i), line.to_owned());
        }
    }
    out
}

/// An open collection.
struct Frame {
    node: NodeId,
    /// Whether it is a sequence (as opposed to a mapping).
    seq: bool,
    /// Whether it is bracket-delimited.
    flow: bool,
    /// The column its content starts at; the threshold of DESIGN §2.2 rule 2.
    col: u32,
    /// Whether any child node has been created yet.
    has_child: bool,
    items: Vec<NodeId>,
    entries: Vec<Entry>,
    /// A key whose value has not been seen yet.
    pending_key: Option<NodeId>,
    /// Keys seen so far, for duplicate reporting.
    keys: HashMap<String, Position>,
}

impl Frame {
    fn new(node: NodeId, seq: bool, flow: bool, col: u32) -> Self {
        Self {
            node,
            seq,
            flow,
            col,
            has_child: false,
            items: Vec::new(),
            entries: Vec::new(),
            pending_key: None,
            keys: HashMap::new(),
        }
    }
}

struct Loader<'a> {
    src: &'a str,
    map: CharMap,
    bom: bool,
    docs: Vec<Document>,
    doc: Document,
    stack: Vec<Frame>,
    pending: Pending,
    /// Per node: the 1-based line and the char index its last token ends at.
    ends: Vec<(u32, usize)>,
    /// Per node: the char index its first token starts at.
    starts: Vec<usize>,
    /// Per node: whether an explicit `?` indicator introduces it.
    explicit: Vec<bool>,
    /// The most recently completed node of the current document.
    last_node: Option<NodeId>,
    /// Char index just past the last thing consumed, comments included. Blank lines are counted
    /// from the source between it and the next token, never from line arithmetic: the parser gives
    /// a flow collection's end event the span of the token *before* its closing bracket, so the
    /// bracket's line would otherwise be miscounted as blank.
    last_index: usize,
    /// Char index just past the last *structural* token; comments never move it, so a `?`
    /// indicator stays findable across the comment that follows it.
    scan_from: usize,
    /// An end-of-line comment that follows a `key:` and therefore belongs to the value that has
    /// not been created yet (DESIGN §2.2 rule 1).
    deferred_eol: Option<Trivia>,
    doc_start_line: Option<u32>,
    doc_end_line: Option<u32>,
    saw_node: bool,
}

impl<'a> Loader<'a> {
    fn new(src: &'a str, bom: bool) -> Self {
        Self {
            src,
            map: CharMap::new(src),
            bom,
            docs: Vec::new(),
            doc: Document::default(),
            stack: Vec::new(),
            pending: Pending::default(),
            ends: Vec::new(),
            starts: Vec::new(),
            explicit: Vec::new(),
            last_node: None,
            last_index: 0,
            scan_from: 0,
            deferred_eol: None,
            doc_start_line: None,
            doc_end_line: None,
            saw_node: false,
        }
    }

    fn run(mut self) -> Result<Vec<Document>, ParseError> {
        let mut parser = Parser::new_from_str(self.src).keep_comments(true);
        while let Some(next) = parser.next_event() {
            let (ev, span) = next?;
            let end = matches!(ev, Event::StreamEnd);
            self.event(ev, span, parser.version());
            if end {
                break;
            }
        }
        Ok(self.docs)
    }

    fn event(&mut self, ev: Event<'_>, span: Span, version: Option<(u32, u32)>) {
        match ev {
            Event::Nothing | Event::StreamStart => {}
            Event::Comment(text) => self.comment(text.into_owned(), span),
            Event::DocumentStart(explicit) => self.document_start(explicit, span, version),
            Event::DocumentEnd => self.document_end(span),
            Event::Scalar(value, style, anchor, tag) => {
                // A block scalar's span starts at its *body*, so the run to probe for blank lines
                // ends at the header: everything past it is the lexeme's own, and `scalar_raw`
                // keeps it. Probing to the body instead counts a blank first content line twice —
                // once in `raw`, once as a `BlankLines` trivia the emitter writes again.
                let probe = match style {
                    ScalarStyle::Literal | ScalarStyle::Folded => self
                        .block_header(span.start.index())
                        .map_or(span.start.index(), |(_, end)| end),
                    _ => span.start.index(),
                };
                self.blanks(probe);
                let raw = self.scalar_raw(&value, style, span);
                let implicit_empty = raw.is_none();
                let mut node = Node::new(NodeKind::Scalar, Style::Scalar(style));
                node.anchor = anchor.name.map(std::borrow::Cow::into_owned);
                node.tag = tag.map(|t| self.node_tag(&t));
                self.record_props(&mut node, span.start.index());
                node.value = Some(if implicit_empty {
                    String::new()
                } else {
                    value.into_owned()
                });
                node.raw = Some(raw.unwrap_or_default());
                let lexeme_end = self.block_lexeme_end(style, node.raw.as_deref(), span);
                let id = self.new_node(node, span);
                if let Some(end) = lexeme_end {
                    self.ends[id as usize] = end;
                }
                self.place(id);
                // A synthetic node has whatever token was current as its span, so it must not be
                // allowed to move the cursor the `?` scan starts from.
                self.advance(span, !implicit_empty);
            }
            Event::Alias(anchor) => {
                self.blanks(span.start.index());
                let name = anchor
                    .name
                    .map(std::borrow::Cow::into_owned)
                    .unwrap_or_default();
                let node = Node::new(NodeKind::Alias { anchor: name }, Style::Block);
                let id = self.new_node(node, span);
                self.place(id);
                self.advance(span, true);
            }
            Event::SequenceStart(anchor, tag, style) => {
                self.collection_start(true, anchor, tag, style, span);
            }
            Event::MappingStart(anchor, tag, style) => {
                self.collection_start(false, anchor, tag, style, span);
            }
            Event::SequenceEnd => self.collection_end(true, span),
            Event::MappingEnd => self.collection_end(false, span),
            Event::StreamEnd => self.stream_end(span),
        }
    }

    // ---------------------------------------------------------------- documents

    fn document_start(&mut self, explicit: bool, span: Span, version: Option<(u32, u32)>) {
        // An implicit start carries the span of the first token of the *content*. When that is a
        // block scalar the token is its body, and the empty lines between the header and it are
        // the scalar's own content — `Event::Scalar` probes to the same place for that reason.
        let probe = if explicit {
            span.start.index()
        } else {
            self.block_header(span.start.index())
                .map_or(span.start.index(), |(_, end)| end)
        };
        self.blanks(probe);
        let (directives, tags_before_version) = if explicit {
            self.directives(self.scan_from, span.start.index())
        } else {
            (Vec::new(), 0)
        };
        let region = explicit
            .then(|| self.directive_region(self.scan_from, span.start.index()))
            .flatten();
        self.doc = Document::default();
        self.doc.explicit_start = explicit;
        self.doc.version = version;
        self.doc.tag_directives = directives;
        self.doc.tags_before_version = tags_before_version;
        self.doc.leading = self.pending.take_all();
        // Every trivium just taken was read between the last consumed token and the `---`, which
        // is exactly the region: the count is where the emitter's own leading run resumes.
        self.doc.directives_raw = region.map(|raw| (raw, self.doc.leading.len()));
        self.ends.clear();
        self.starts.clear();
        self.explicit.clear();
        self.last_node = None;
        self.saw_node = false;
        self.doc_end_line = None;
        self.doc_start_line = if explicit {
            Some(small(span.start.line()))
        } else {
            None
        };
        // An implicit start has no marker of its own: the event carries the span of the first
        // token of the document's *content*, so treating it as consumed would hide that token's
        // own leading indicators from every forward probe below.
        self.advance(span, explicit);
    }

    fn document_end(&mut self, span: Span) {
        self.blanks(span.start.index());
        let explicit = self.slice(span.start.index(), span.end.index()) == "...";
        self.doc.explicit_end = explicit;
        if explicit {
            self.doc_end_line = Some(small(span.start.line()));
            self.advance(span, true);
        }
        self.doc_start_line = None;
        self.last_node = None;
        self.docs.push(std::mem::take(&mut self.doc));
    }

    fn stream_end(&mut self, span: Span) {
        let unread = self.last_index;
        self.blanks(span.start.index());
        let rest = self.pending.take_all();
        // A `...` that closes a document with no root of its own is not a parser event, so the
        // marker has to be read off the page here.
        let bare_end = self.docs.is_empty()
            && self
                .src
                .get(self.map.byte(unread)..)
                .unwrap_or_default()
                .lines()
                .any(|l| l.trim_end() == "...");
        if self.docs.is_empty() {
            // A source of nothing but trivia. Rule 4 still has to put it somewhere, and a rootless
            // document re-emits as exactly the trivia it holds.
            self.docs.push(Document::default());
        }
        // A stream that does not end in a break can still end in white space, and no node and no
        // trivium owns it: the emitter writes a line's tail only when it breaks the line, and it
        // never breaks the last one. Bounded by the last lexeme, so a `|+` scalar that kept those
        // lines as its own content does not get them twice.
        let lexemes = self
            .ends
            .iter()
            .map(|e| e.1)
            .max()
            .unwrap_or(self.scan_from);
        let tail = self.slice(lexemes, self.map.len());
        let final_line_break = self.src.ends_with(['\n', '\r']);
        // `blanks` filed every *whole* line in here as trivia and the emitter writes those
        // itself; only the tail of the last lexeme's line and the file's last line are left.
        let stream_tail =
            if final_line_break || !tail.chars().all(|c| matches!(c, ' ' | '\t' | '\n' | '\r')) {
                ""
            } else if tail.matches('\n').count() > 1 {
                tail.rsplit_once('\n').map_or(tail, |(_, l)| l)
            } else {
                tail
            };
        let last = self.docs.last_mut().expect("just pushed");
        // Trivia read before that marker sit *above* it, which is `leading`: `trailing` is what
        // the emitter writes after the `...`.
        if bare_end {
            last.explicit_end = true;
            last.leading.extend(rest);
        } else {
            last.trailing.extend(rest);
        }
        last.final_line_break = final_line_break;
        stream_tail.clone_into(&mut last.stream_tail);
        if let Some(first) = self.docs.first_mut() {
            first.bom = self.bom;
        }
    }

    // ---------------------------------------------------------------- nodes

    fn collection_start(
        &mut self,
        seq: bool,
        anchor: yamluna_scanner::AnchorRef<'_>,
        tag: Option<std::borrow::Cow<'_, yamluna_scanner::Tag>>,
        style: StructureStyle,
        span: Span,
    ) {
        self.blanks(span.start.index());
        let flow = style == StructureStyle::Flow;
        let kind = if seq {
            NodeKind::Sequence { items: Vec::new() }
        } else {
            NodeKind::Mapping {
                entries: Vec::new(),
            }
        };
        let mut node = Node::new(kind, if flow { Style::Flow } else { Style::Block });
        node.anchor = anchor.name.map(std::borrow::Cow::into_owned);
        node.tag = tag.map(|t| self.node_tag(&t));
        self.record_props(&mut node, span.start.index());
        let id = self.new_node(node, span);
        self.stack
            .push(Frame::new(id, seq, flow, small(span.start.col())));
        self.advance(span, true);
    }

    fn collection_end(&mut self, seq: bool, span: Span) {
        // Before the run is split: a collection that ends at the last line of the file must not
        // swallow the blank lines that follow it.
        self.blanks(span.start.index());
        let frame = self.stack.pop().expect("unbalanced collection events");
        // DESIGN §2.2 rule 2. A flow collection is delimited, so everything still pending is
        // inside it. A block collection takes only the part of the run indented to its content;
        // the rest belongs to a shallower slot. The root collection takes nothing: what follows
        // its last node is the document's trailing trivia (rule 4).
        let after = if frame.flow {
            self.pending.take_all()
        } else if self.stack.is_empty() {
            Vec::new()
        } else {
            self.pending.take_from_col(frame.col)
        };
        let last_child = frame
            .entries
            .last()
            .map(|e| e.value)
            .or_else(|| frame.items.last().copied());
        let end = if frame.flow {
            (small(span.start.line()), span.end.index())
        } else {
            last_child.map_or((small(span.start.line()), span.start.index()), |c| {
                self.ends[c as usize]
            })
        };
        self.ends[frame.node as usize] = end;
        if frame.flow {
            // The end event carries the token *before* the closing bracket whenever a `,` or a
            // comment sits in front of it, so the bracket is scanned for -- and it is where the
            // collection really ends, which is what an enclosing flow collection measures its own
            // separation from.
            match self.flow_seps(&frame) {
                // The bracket is where the collection ends, whatever token the end event names.
                Some(close) => self.ends[frame.node as usize].1 = close + 1,
                // No brackets of its own (`[a: 1]`), or a scan that lost the thread: the end event
                // then names a token that is not this collection's, and only its last child is
                // known to be inside it.
                None => {
                    if let Some(c) = last_child {
                        self.ends[frame.node as usize].1 = self.ends[c as usize].1;
                    }
                }
            }
        }
        let node = self.doc.node_mut(frame.node);
        node.kind = if seq {
            NodeKind::Sequence { items: frame.items }
        } else {
            NodeKind::Mapping {
                entries: frame.entries,
            }
        };
        node.trivia.after = after;
        self.place(frame.node);
        self.advance(span, true);
    }

    /// Create a node: give it its position, its deferred end-of-line comment and the run of
    /// own-line trivia that precedes it.
    fn new_node(&mut self, mut node: Node, span: Span) -> NodeId {
        node.pos = Position::from_marker(span.start);
        if let Some(t) = self.deferred_eol.take() {
            node.trivia.eol = Some(t);
        }
        let id = self.doc.push(node);
        debug_assert_eq!(self.ends.len(), id as usize);
        self.ends.push((small(span.end.line()), span.end.index()));
        self.starts.push(span.start.index());
        self.explicit
            .push(self.explicit_indicator_before(span.start.index()));
        self.take_before(id);
        id
    }

    /// Give the pending run of trivia to `node` — or, if `node` is the first child of the
    /// collection that encloses it, to that collection's `inner` slot (DESIGN §2.1).
    ///
    /// A nested block collection first takes the tail of the run that is indented into it: those
    /// comments precede its *first child*, not the collection, so they are its `inner` and must
    /// die with it when the entry is replaced. Left in `before` they land in the parent's record
    /// for the entry (a sequence element's `before` *is* `C_ELEM_PRE`) and outlive the subtree
    /// they describe.
    fn take_before(&mut self, node: NodeId) {
        if self.pending.is_empty() {
            return;
        }
        let mut lifted = Vec::new();
        let n = self.doc.node(node);
        if n.style == Style::Block && n.is_collection() && !self.stack.is_empty() {
            let mut inside = self.pending.take_to_col(n.pos.col);
            // A blank run at the head of what was taken sits *above* the `-` that introduces this
            // node, so it divides the node from the sibling before it — unless the node is the
            // collection's first child, whose `-` was written before the run began at all.
            if self.stack.last().is_some_and(|f| f.has_child) {
                let at = inside.iter().position(|t| t.col().is_some()).unwrap_or(0);
                lifted = inside.drain(..at).collect();
            }
            // Blank lines directly above the collection's own first line sit under the `-` or the
            // `key:` that introduces it, so they are inside it; ones separated from it by that
            // indicator's line are what divides it from the sibling before it.
            if self.line_above_is_blank(self.starts[node as usize]) {
                inside.extend(self.pending.take_trailing_blanks());
            }
            self.doc.node_mut(node).trivia.inner.extend(inside);
            if self.pending.is_empty() && lifted.is_empty() {
                return;
            }
        }
        let eol = |t: &Trivia| {
            matches!(
                t,
                Trivia::Comment {
                    own_line: false,
                    ..
                }
            )
        };
        let mut run = lifted;
        run.append(&mut self.pending.take_all());
        if let Some(owner) = self.stack.last().filter(|f| !f.has_child).map(|f| f.node) {
            // An end-of-line comment in the run sits on the line of the indicator that introduces
            // this node — after the `-` — which is what `before` writes; only what precedes it
            // has lines of its own, between the collection and its first child.
            let rest = run.split_off(run.iter().position(eol).unwrap_or(run.len()));
            self.doc.node_mut(owner).trivia.inner.extend(run);
            run = rest;
        }
        // A block scalar's `|` header is written ahead of the comment that ends its line, so that
        // comment belongs in `eol`, which the emitter writes straight after the header.
        let n = self.doc.node(node);
        if n.trivia.eol.is_none()
            && matches!(
                n.style,
                Style::Scalar(ScalarStyle::Literal | ScalarStyle::Folded)
            )
        {
            if let Some(i) = run.iter().position(eol) {
                self.doc.node_mut(node).trivia.eol = Some(run.remove(i));
            }
        }
        self.doc.node_mut(node).trivia.before.extend(run);
    }

    /// File a completed node into its parent.
    fn place(&mut self, id: NodeId) {
        self.last_node = Some(id);
        self.saw_node = true;
        let Some(fi) = self.stack.len().checked_sub(1) else {
            self.doc.root = Some(id);
            return;
        };
        if self.stack[fi].seq {
            self.stack[fi].items.push(id);
        } else if let Some(key) = self.stack[fi].pending_key.take() {
            let merge = self.is_merge_key(key);
            let explicit = self.explicit[key as usize];
            let colon = self.colon_after(self.ends[key as usize]);
            self.stack[fi].entries.push(Entry {
                key,
                value: id,
                merge,
                explicit,
                colon,
            });
        } else {
            let repr = self.key_repr(id);
            let pos = self.doc.node(id).pos;
            if let Some(first) = self.stack[fi].keys.insert(repr.clone(), pos) {
                self.doc.duplicate_keys.push(DuplicateKey {
                    key: repr,
                    first,
                    again: pos,
                });
            }
            self.stack[fi].pending_key = Some(id);
        }
        self.stack[fi].has_child = true;
    }

    // ---------------------------------------------------------------- trivia

    fn comment(&mut self, text: String, span: Span) {
        let line = small(span.start.line());
        self.blanks(span.start.index());
        self.last_index = self.last_index.max(span.end.index());
        let own_line = self.is_own_line(span.start.index());
        let t = Trivia::Comment {
            text,
            own_line,
            col: small(span.start.col()),
        };
        if own_line {
            self.pending.push(t);
            return;
        }
        // A block sequence's `-` introduces its *item*, not the sequence: a comment after it sits
        // on the item's own first line. The item does not exist yet, and `before` is the slot the
        // emitter writes straight after the `-`, so the run it is built from is where this goes.
        if self
            .stack
            .last()
            .is_some_and(|f| f.seq && !f.flow && !f.has_child)
        {
            self.pending.push(t);
            return;
        }
        // Rule 1. A collection that opened on this line and has no children yet owns the comment:
        // it sits after the `[` or the `-`, and the emitter writes it on the collection's own line.
        if let Some(frame) = self.stack.last() {
            if !frame.has_child {
                let n = frame.node;
                let open = self.doc.node(n);
                if open.pos.line + 1 == line && open.trivia.eol.is_none() {
                    self.doc.node_mut(n).trivia.eol = Some(t);
                    return;
                }
            }
        }
        // Rule 1. Inside a mapping entry whose value has not been seen, the comment belongs to the
        // value — unless it falls between the key and the `:`, in which case it is the key's.
        if let Some(frame) = self.stack.last() {
            if let Some(key) = frame.pending_key {
                if self.ends[key as usize].0 == line {
                    if self
                        .slice(self.ends[key as usize].1, span.start.index())
                        .contains(':')
                    {
                        self.deferred_eol = Some(t);
                    } else {
                        self.doc.node_mut(key).trivia.eol = Some(t);
                    }
                    return;
                }
            }
        }
        // Rule 1, the ordinary case: the last node whose final token is on this line.
        if let Some(n) = self.last_node {
            if self.ends[n as usize].0 == line && self.doc.node(n).trivia.eol.is_none() {
                self.doc.node_mut(n).trivia.eol = Some(t);
                return;
            }
        }
        // Rule 4: on the `...` line, or on the `---` line before any node.
        if self.doc_end_line == Some(line) {
            if let Some(d) = self.docs.last_mut() {
                d.trailing.push(t);
                return;
            }
        } else if self.doc_start_line == Some(line) && !self.saw_node {
            self.doc.leading.push(t);
            return;
        }
        self.pending.push(t);
    }

    /// Rule 3: record the empty lines between the last thing consumed and char `index`.
    fn blanks(&mut self, index: usize) {
        if index <= self.last_index {
            return;
        }
        let between = self.slice(self.last_index, index);
        // The first and last pieces are the tail of the line we were on and the head of the line
        // we are going to; only the whole lines in between can be blank. At the very start of the
        // stream there is no line we were on, so the first piece is a whole line as well.
        let parts: Vec<&str> = between.split('\n').collect();
        // One trivium per *consecutive* run: a line with content between two empty ones is an
        // indicator the parser gives no event of its own (a `-`, a `---`), and the empty lines on
        // either side of it go to different slots.
        let mut run: u32 = 0;
        let flush = |run: &mut u32, pending: &mut Pending| {
            if *run > 0 {
                pending.push(Trivia::BlankLines(*run));
                *run = 0;
            }
        };
        for line in parts
            .get(usize::from(self.last_index > 0)..parts.len().saturating_sub(1))
            .unwrap_or_default()
        {
            if line.trim().is_empty() {
                run += 1;
            } else {
                flush(&mut run, &mut self.pending);
            }
        }
        flush(&mut run, &mut self.pending);
        self.last_index = index;
    }

    /// Advance the "last thing consumed" cursors past an event.
    fn advance(&mut self, span: Span, structural: bool) {
        let end = if span.is_empty() {
            span.start.index()
        } else {
            span.end.index()
        };
        self.last_index = self.last_index.max(end);
        if structural {
            self.scan_from = self.scan_from.max(end);
        }
    }

    // ---------------------------------------------------------------- flow separation

    /// Record what the source wrote *between* this flow collection's lexemes, and return the char
    /// index of its closing bracket.
    ///
    /// One run in front of each child and one in front of the bracket. Each is found by scanning
    /// forward from the end of the lexeme before it: inside `[]` or `{}` only white space, `,`,
    /// `:`, `?` and comments can separate two things, so the first character that is none of those
    /// is the next lexeme -- or its own `&anchor` or tag, which the emitter writes from the node --
    /// and ends the run.
    fn flow_seps(&mut self, frame: &Frame) -> Option<usize> {
        // `[a: 1]`: a single pair written with no brackets of its own, which the parser gives the
        // span of its key. It has no punctuation, and the `,` after it belongs to the sequence
        // that holds it. This is the emitter's own test for the form, so the two cannot disagree.
        if frame
            .entries
            .first()
            .is_some_and(|e| self.doc.node(frame.node).pos == self.doc.node(e.key).pos)
        {
            return None;
        }
        let kids: Vec<NodeId> = if frame.seq {
            frame.items.clone()
        } else {
            frame
                .entries
                .iter()
                .flat_map(|e| [e.key, e.value])
                .collect()
        };
        // Just past the `[` or `{`. The span may begin at the collection's own `&anchor` or tag,
        // and neither of those may contain a flow indicator.
        let from = self.starts[frame.node as usize];
        let open = self
            .slice(from, self.map.len())
            .chars()
            .position(|c| c == '[' || c == '{')?;
        let mut at = from + open + 1;
        let mut seps = Vec::with_capacity(kids.len() + 1);
        for &k in &kids {
            // Bounded by where the parser says the child's content begins: in flow context a
            // plain scalar may *start* with `:` or `?` (`[:x]`, `{x: :x}`), and an unbounded scan
            // would take that first character for separation and write it twice. A node the
            // parser supplied has no content and no trustworthy span, and nothing of its own to
            // write twice, so its run runs on to the next lexeme.
            let empty = is_implicit_empty(self.doc.node(k));
            let limit = if empty {
                usize::MAX
            } else {
                self.starts[k as usize]
            };
            let (run, stop) = self.separation(at, limit);
            seps.push(run);
            // A node the parser supplied has no lexeme to advance past, and the span it was given
            // is the *next* token's -- for `{a: , b}` already beyond the `}`. The scan's own stop
            // is the cursor there; a real node ends where its last token does.
            at = if empty {
                // ...and an empty node's `&anchor` and tag *are* written, so the scan has to
                // step over them itself: they are the one thing between it and the next run.
                let n = self.doc.node(k);
                let props = usize::from(n.anchor.is_some()) + usize::from(n.tag.is_some());
                stop + past_properties(self.slice(stop, self.map.len()), props)
            } else {
                stop.max(self.ends[k as usize].1)
            };
        }
        let (run, close) = self.separation(at, usize::MAX);
        // Anything but the closing bracket here means the scan lost the thread. Record nothing
        // rather than runs the emitter would echo wrongly: empty is "not recorded".
        if !matches!(
            self.slice(close, self.map.len()).chars().next(),
            Some(']' | '}')
        ) {
            return None;
        }
        seps.push(run);
        self.doc.node_mut(frame.node).flow_seps = seps;
        Some(close)
    }

    /// The separation the source wrote from char `from` up to the next lexeme -- or to `limit`,
    /// whichever comes first -- with its comments taken out, and the char index it stops at.
    fn separation(&self, from: usize, limit: usize) -> (String, usize) {
        let mut run = String::new();
        let mut at = from;
        let mut it = self.slice(from, self.map.len()).chars();
        while at < limit {
            let Some(c) = it.next() else { break };
            at += 1;
            match c {
                ',' | ':' | '?' => run.push(c),
                // A comment is trivia and is written back from there; the break that ends it is
                // separation and stays.
                '#' => {
                    for c in it.by_ref() {
                        at += 1;
                        if c == '\n' || c == '\r' {
                            run.push(c);
                            break;
                        }
                    }
                }
                c if c.is_whitespace() => run.push(c),
                _ => return (run, at - 1),
            }
        }
        (run, at)
    }

    // ---------------------------------------------------------------- source probes

    /// The 0-based line and column of char `index`.
    fn position_at(&self, index: usize) -> Position {
        let before = &self.src[..self.map.byte(index)];
        match before.rsplit_once('\n') {
            Some((head, line)) => Position {
                line: small(head.matches('\n').count() + 1),
                col: small(line.chars().count()),
            },
            None => Position {
                line: 0,
                col: small(before.chars().count()),
            },
        }
    }

    fn slice(&self, start: usize, end: usize) -> &'a str {
        self.map.slice(self.src, start, end)
    }

    /// Is the line directly above the one char `index` sits on empty?
    fn line_above_is_blank(&self, index: usize) -> bool {
        let before = &self.src[..self.map.byte(index)];
        let Some(nl) = before.rfind('\n') else {
            return false;
        };
        let above = &before[..nl];
        above[above.rfind('\n').map_or(0, |i| i + 1)..]
            .trim()
            .is_empty()
    }

    /// Is there only whitespace between the start of the line and char `index`?
    fn is_own_line(&self, index: usize) -> bool {
        let before = &self.src[..self.map.byte(index)];
        let line = before.rsplit_once('\n').map_or(before, |(_, l)| l);
        line.chars().all(|c| c == ' ' || c == '\t' || c == '\r')
    }

    /// Is the node starting at char `index` introduced by an explicit `?` key indicator?
    ///
    /// Only whitespace, indicators and comments can sit between the previous structural token and
    /// a node, so scanning forward for a `?` while skipping comment runs is exact.
    fn explicit_indicator_before(&self, index: usize) -> bool {
        let mut rest = self.slice(self.scan_from, index).chars();
        while let Some(c) = rest.next() {
            match c {
                '?' => return true,
                // Indicators that may legitimately sit between the previous token and a key.
                '-' | ',' | '[' | '{' | ']' | '}' => {}
                '#' => {
                    for c in rest.by_ref() {
                        if c == '\n' {
                            break;
                        }
                    }
                }
                c if c.is_whitespace() => {}
                _ => return false,
            }
        }
        false
    }

    /// Record where the node's `&anchor` and its tag were written, and with them their order.
    ///
    /// Only whitespace, indicators and comments can sit between the previous structural token and
    /// a node's properties, so a scan of that text finds both.
    fn record_props(&self, node: &mut Node, index: usize) {
        if node.anchor.is_none() && node.tag.is_none() {
            return;
        }
        let gap = self.slice(self.scan_from, index);
        let (anchor, tag) = property_offsets(gap);
        let at = |o: usize| self.position_at(self.scan_from + gap[..o].chars().count());
        node.anchor_at = node.anchor.is_some().then_some(anchor).flatten().map(at);
        node.tag_at = node.tag.is_some().then_some(tag).flatten().map(at);
        node.tag_first = match (node.anchor_at, node.tag_at) {
            (Some(a), Some(t)) => (t.line, t.col) < (a.line, a.col),
            _ => false,
        };
    }

    /// Where the `:` of the entry whose key ends at `from` was written, or `None` when the source
    /// wrote none (`{a: 1, b}`, a `? key` with no `: value` line).
    ///
    /// Only white space and comments can sit between a key and its `:`, so anything else means
    /// the entry has no `:` at all and the scan stops there.
    fn colon_after(&self, from: (u32, usize)) -> Option<Position> {
        let gap = self.slice(from.1, self.map.len());
        let at = colon_offset(gap)?;
        Some(self.position_at(from.1 + gap[..at].chars().count()))
    }

    /// The block-scalar header (`|`, `>-`, `|2`, ...) that introduces the scalar starting at char
    /// `index`, together with the line break that follows it.
    fn block_header(&self, index: usize) -> Option<(String, usize)> {
        // The span of a block scalar starts at its *body*, so the header is behind it — except
        // for one with no body at all (`a: |`), which the parser reports at the header itself.
        let mut from = self.scan_from;
        let mut head = self.slice(from, index);
        if !head.contains(['|', '>']) {
            from = index;
            let rest = &self.src[self.map.byte(index)..];
            head = &rest[..rest.find('\n').unwrap_or(rest.len())];
        }
        let at = head.find(['|', '>'])?;
        let indicator: String = head[at..]
            .chars()
            .take_while(|c| matches!(c, '|' | '>' | '-' | '+' | '0'..='9'))
            .collect();
        // `at` is a byte offset into `head`; everything else here counts characters.
        let end = from + head[..at].chars().count() + indicator.chars().count();
        Some((indicator, end))
    }

    /// Where a block scalar's lexeme ends, as `ends` records it: the 1-based line and the char
    /// index just past its last character.
    ///
    /// The parser's span runs on to the *next* token, so without this a comment on the line that
    /// follows the scalar reads as the scalar's own end-of-line comment.
    fn block_lexeme_end(
        &self,
        style: ScalarStyle,
        raw: Option<&str>,
        span: Span,
    ) -> Option<(u32, usize)> {
        if !matches!(style, ScalarStyle::Literal | ScalarStyle::Folded) {
            return None;
        }
        let (indicator, header_end) = self.block_header(span.start.index())?;
        let end = header_end + raw?.chars().count() - indicator.chars().count();
        let over = self.slice(end.min(span.end.index()), span.end.index());
        Some((
            small(span.end.line()).saturating_sub(small(over.matches('\n').count())),
            end,
        ))
    }

    /// The lexeme of a scalar, or `None` when the parser synthesised the node (`key:` with nothing
    /// after it, an empty document, an explicit key with no value).
    fn scalar_raw(&self, value: &str, style: ScalarStyle, span: Span) -> Option<String> {
        let text = self.slice(span.start.index(), span.end.index());
        match style {
            ScalarStyle::Literal | ScalarStyle::Folded => {
                let (indicator, header_end) = self.block_header(span.start.index())?;
                // A block scalar with no body at all and nothing after it is reported at its own
                // header rather than a line below it, so there is no body to widen. All that can
                // follow the header is the rest of its line and the empty lines a `+` keeps.
                if span.start.index() < header_end {
                    let tail = &self.src[self.map.byte(header_end)..];
                    let blank: usize = tail
                        .split_inclusive('\n')
                        .take_while(|l| l.trim().is_empty())
                        .map(str::len)
                        .sum();
                    return Some(format!("{indicator}{}", strip_break(&tail[..blank])));
                }
                // Widen the body back to the start of its first line so the block keeps its own
                // indentation, and drop the single break that terminates its last line: the
                // emitter writes that itself.
                let start = self.map.byte(span.start.index());
                let line_start = self.src[..start].rfind('\n').map_or(0, |i| i + 1);
                // What the source wrote between the indicators and the body's first line, from
                // its first break on. Usually just that break — but a blank line here is block
                // scalar *content* (`|+` keeps it, and the cooked value starts with it), so it
                // belongs in the lexeme. Anything before the break is the header line's own tail:
                // white space, or a comment already lifted into `Trivia4::eol`.
                let gap = self
                    .src
                    .get(self.map.byte(header_end)..line_start)
                    .unwrap_or("\n");
                let brk = match gap.find('\n') {
                    Some(i) => &gap[i - usize::from(gap[..i].ends_with('\r'))..],
                    None => "\n",
                };
                let body = &self.src[line_start..self.map.byte(span.end.index())];
                // The span runs to the *next* token, so it can end part-way into that token's
                // indentation. A body line is never white space alone with no break after it, so
                // such a tail is the next line's indent and not the scalar's.
                let body = match body.rfind('\n') {
                    Some(i) if !body[i + 1..].is_empty() && body[i + 1..].trim().is_empty() => {
                        &body[..=i]
                    }
                    _ => body,
                };
                // The break that terminates the last line is the emitter's, not the lexeme's —
                // and with no body at all that break is the one right after the header, so it
                // has to come off the whole lexeme rather than off `body`.
                Some(strip_break(&format!("{indicator}{brk}{body}")))
            }
            ScalarStyle::Plain => {
                // A synthetic empty node has whatever token happened to be current as its span.
                if (value == "~" || value.is_empty()) && text.trim() != value {
                    None
                } else {
                    Some(text.to_owned())
                }
            }
            _ => Some(text.to_owned()),
        }
    }

    /// The document's `%TAG` lines, and how many of them sat above its `%YAML` line.
    ///
    /// The version itself comes from the parser; only its place among the `%TAG` lines has to be
    /// read back off the page.
    fn directives(&self, from: usize, to: usize) -> (Vec<TagDirective>, usize) {
        let mut tags = Vec::new();
        let mut above = None;
        for line in self.slice(from, to).lines() {
            if let Some(rest) = line.strip_prefix("%TAG") {
                if rest.starts_with([' ', '\t']) {
                    let mut it = rest.split_whitespace();
                    if let (Some(handle), Some(prefix)) = (it.next(), it.next()) {
                        tags.push(TagDirective {
                            handle: handle.to_owned(),
                            prefix: prefix.to_owned(),
                        });
                    }
                }
            } else if above.is_none()
                && line
                    .strip_prefix("%YAML")
                    .is_some_and(|rest| rest.starts_with([' ', '\t']))
            {
                above = Some(tags.len());
            }
        }
        (tags, above.unwrap_or(0))
    }

    /// The document's directive region as written, or `None` when it holds no directive line.
    ///
    /// See [`Document::directives_raw`](crate::Document::directives_raw): the region is kept
    /// verbatim because a directive line's spelling is not recoverable from its meaning.
    fn directive_region(&self, from: usize, to: usize) -> Option<String> {
        let text = self.slice(from, to);
        // `from` can land mid-line, just past the `...` that closed the document before. That
        // line's tail and its break belong to the line above, not to the region.
        let byte = self.map.byte(from);
        let text = if byte == 0 || self.src[..byte].ends_with('\n') {
            text
        } else {
            text.split_once('\n')?.1
        };
        if !text.lines().any(|l| l.starts_with('%')) {
            return None;
        }
        // The break before `---` is written by the emitter, not echoed with the region.
        let text = text.strip_suffix('\n').unwrap_or(text);
        Some(text.strip_suffix('\r').unwrap_or(text).to_owned())
    }

    /// Turn the parser's already-resolved tag back into the form it was written in.
    ///
    /// The event carries the *prefix* in `handle`, so the shorthand is recovered by inverting the
    /// `%TAG` table of the document (plus the two default handles).
    fn node_tag(&self, tag: &yamluna_scanner::Tag) -> NodeTag {
        let resolved = format!("{}{}", tag.handle, tag.suffix);
        let handle = match tag.handle.as_str() {
            // A verbatim `!<uri>`, or the non-specific `!`.
            "" => String::new(),
            "!" => "!".to_owned(),
            prefix => self
                .doc
                .tag_directives
                .iter()
                .find(|d| d.prefix == prefix)
                .map_or_else(
                    || {
                        if prefix == "tag:yaml.org,2002:" {
                            "!!".to_owned()
                        } else {
                            String::new()
                        }
                    },
                    |d| d.handle.clone(),
                ),
        };
        NodeTag {
            handle,
            suffix: tag.suffix.clone(),
            resolved,
        }
    }

    // ---------------------------------------------------------------- keys

    fn is_merge_key(&self, id: NodeId) -> bool {
        let n = self.doc.node(id);
        n.tag.is_none()
            && n.style == Style::Scalar(ScalarStyle::Plain)
            && n.value.as_deref() == Some("<<")
    }

    /// A canonical rendering of a key, for duplicate reporting only.
    fn key_repr(&self, id: NodeId) -> String {
        let n = self.doc.node(id);
        match &n.kind {
            NodeKind::Scalar => n.value.clone().unwrap_or_default(),
            NodeKind::Alias { anchor } => format!("*{anchor}"),
            NodeKind::Sequence { items } => {
                let inner: Vec<String> = items.iter().map(|i| self.key_repr(*i)).collect();
                format!("[{}]", inner.join(","))
            }
            NodeKind::Mapping { entries } => {
                let inner: Vec<String> = entries
                    .iter()
                    .map(|e| format!("{}:{}", self.key_repr(e.key), self.key_repr(e.value)))
                    .collect();
                format!("{{{}}}", inner.join(","))
            }
        }
    }
}

/// The byte offset of the `:` between a key and its value, in the source text that follows the
/// key. `None` when the next thing written is not a `:`.
fn colon_offset(gap: &str) -> Option<usize> {
    let mut it = gap.char_indices();
    while let Some((i, c)) = it.next() {
        match c {
            ':' => return Some(i),
            '#' => {
                for (_, c) in it.by_ref() {
                    if c == '\n' {
                        break;
                    }
                }
            }
            c if c.is_whitespace() => {}
            _ => return None,
        }
    }
    None
}

/// The byte offsets of the `&anchor` and of the tag, in the text between the previous structural
/// token and the node.
///
/// Both open a word of their own: only white space, comments and the indicators that may precede
/// a node can sit in front of them. *Inside* a word the two characters are ordinary — an anchor
/// name may contain a `!` and a tag suffix a `&` — so the word boundary is what makes the scan
/// exact.
fn property_offsets(gap: &str) -> (Option<usize>, Option<usize>) {
    let (mut anchor, mut tag) = (None, None);
    let mut fresh = true;
    let mut it = gap.char_indices();
    while let Some((i, c)) = it.next() {
        match c {
            '&' if fresh && anchor.is_none() => anchor = Some(i),
            '!' if fresh && tag.is_none() => tag = Some(i),
            '#' => {
                for (_, c) in it.by_ref() {
                    if c == '\n' {
                        break;
                    }
                }
                fresh = true;
                continue;
            }
            _ => {}
        }
        fresh = c.is_whitespace() || matches!(c, '-' | '?' | ':' | ',' | '[' | '{');
    }
    (anchor, tag)
}

/// How many characters at the start of `text` `props` node properties take up, white space
/// between them included.
///
/// A node the parser supplied has no span of its own, so a scan that stops at the first character
/// of its `&anchor` or its tag has nothing else to get it past them.
fn past_properties(text: &str, props: usize) -> usize {
    let mut at = 0;
    let mut rest = text;
    for _ in 0..props {
        let ws: String = rest.chars().take_while(|c| c.is_whitespace()).collect();
        at += ws.chars().count();
        rest = &rest[ws.len()..];
        if !rest.starts_with(['&', '!']) {
            break;
        }
        // A verbatim tag runs to its `>` and may hold a `,`; an anchor and a tag shorthand end at
        // the first white space or flow indicator.
        let end = if rest.starts_with("!<") {
            rest.find('>').map_or(rest.len(), |i| i + 1)
        } else {
            rest.find([' ', '\t', '\n', '\r', ',', '[', ']', '{', '}'])
                .unwrap_or(rest.len())
        };
        at += rest[..end].chars().count();
        rest = &rest[end..];
    }
    at
}

/// A value the parser supplied because the source wrote none: `{a: }`, or the `b` of `{a: 1, b}`.
fn is_implicit_empty(n: &Node) -> bool {
    matches!(n.kind, NodeKind::Scalar) && n.raw.as_deref() == Some("")
}
