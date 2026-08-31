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
    Loader::new(src, bom).run()
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
                self.blanks(span.start.index());
                let raw = self.scalar_raw(&value, style, span);
                let implicit_empty = raw.is_none();
                let mut node = Node::new(NodeKind::Scalar, Style::Scalar(style));
                node.anchor = anchor.name.map(std::borrow::Cow::into_owned);
                node.tag = tag.map(|t| self.node_tag(&t));
                node.value = Some(if implicit_empty {
                    String::new()
                } else {
                    value.into_owned()
                });
                node.raw = Some(raw.unwrap_or_default());
                let id = self.new_node(node, span);
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
        self.blanks(span.start.index());
        let directives = if explicit {
            self.tag_directives(self.scan_from, span.start.index())
        } else {
            Vec::new()
        };
        self.doc = Document::default();
        self.doc.explicit_start = explicit;
        self.doc.version = version;
        self.doc.tag_directives = directives;
        self.doc.leading = self.pending.take_all();
        self.ends.clear();
        self.explicit.clear();
        self.last_node = None;
        self.saw_node = false;
        self.doc_end_line = None;
        self.doc_start_line = if explicit {
            Some(small(span.start.line()))
        } else {
            None
        };
        self.advance(span, true);
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
        self.blanks(span.start.index());
        let rest = self.pending.take_all();
        if self.docs.is_empty() {
            // A source of nothing but trivia. Rule 4 still has to put it somewhere, and a rootless
            // document re-emits as exactly the trivia it holds.
            self.docs.push(Document::default());
        }
        let last = self.docs.last_mut().expect("just pushed");
        last.trailing.extend(rest);
        last.final_line_break = self.src.ends_with(['\n', '\r']);
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
        self.explicit
            .push(self.explicit_indicator_before(span.start.index()));
        self.take_before(id);
        id
    }

    /// Give the pending run of trivia to `node` — or, if `node` is the first child of the
    /// collection that encloses it, to that collection's `inner` slot (DESIGN §2.1).
    fn take_before(&mut self, node: NodeId) {
        if self.pending.is_empty() {
            return;
        }
        let run = self.pending.take_all();
        match self.stack.last() {
            Some(f) if !f.has_child => {
                let owner = f.node;
                self.doc.node_mut(owner).trivia.inner.extend(run);
            }
            _ => self.doc.node_mut(node).trivia.before.extend(run),
        }
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
            self.stack[fi].entries.push(Entry {
                key,
                value: id,
                merge,
                explicit,
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
        // we are going to; only the whole lines in between can be blank.
        let parts: Vec<&str> = between.split('\n').collect();
        let n = parts
            .get(1..parts.len().saturating_sub(1))
            .unwrap_or_default()
            .iter()
            .filter(|l| l.trim().is_empty())
            .count();
        if let Ok(n) = u32::try_from(n) {
            if n > 0 {
                self.pending.push(Trivia::BlankLines(n));
            }
        }
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

    // ---------------------------------------------------------------- source probes

    fn slice(&self, start: usize, end: usize) -> &'a str {
        self.map.slice(self.src, start, end)
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

    /// The block-scalar header (`|`, `>-`, `|2`, ...) that introduces the scalar starting at char
    /// `index`, together with the line break that follows it.
    fn block_header(&self, index: usize) -> Option<(String, &'a str)> {
        let head = self.slice(self.scan_from, index);
        let at = head.find(['|', '>'])?;
        let indicator: String = head[at..]
            .chars()
            .take_while(|c| matches!(c, '|' | '>' | '-' | '+' | '0'..='9'))
            .collect();
        let brk = if head[at..].contains("\r\n") {
            "\r\n"
        } else {
            "\n"
        };
        Some((indicator, brk))
    }

    /// The lexeme of a scalar, or `None` when the parser synthesised the node (`key:` with nothing
    /// after it, an empty document, an explicit key with no value).
    fn scalar_raw(&self, value: &str, style: ScalarStyle, span: Span) -> Option<String> {
        let text = self.slice(span.start.index(), span.end.index());
        match style {
            ScalarStyle::Literal | ScalarStyle::Folded => {
                let (indicator, brk) = self.block_header(span.start.index())?;
                // Widen the body back to the start of its first line so the block keeps its own
                // indentation, and drop the single break that terminates its last line: the
                // emitter writes that itself.
                let start = self.map.byte(span.start.index());
                let line_start = self.src[..start].rfind('\n').map_or(0, |i| i + 1);
                let body = &self.src[line_start..self.map.byte(span.end.index())];
                let body = body
                    .strip_suffix("\r\n")
                    .or_else(|| body.strip_suffix('\n'))
                    .unwrap_or(body);
                Some(format!("{indicator}{brk}{body}"))
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

    fn tag_directives(&self, from: usize, to: usize) -> Vec<TagDirective> {
        self.slice(from, to)
            .lines()
            .filter_map(|line| {
                let rest = line.strip_prefix("%TAG")?;
                if !rest.starts_with([' ', '\t']) {
                    return None;
                }
                let mut it = rest.split_whitespace();
                Some(TagDirective {
                    handle: it.next()?.to_owned(),
                    prefix: it.next()?.to_owned(),
                })
            })
            .collect()
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
