//! Writes a stream of documents back out as YAML text.
//!
//! # Two paths, kept apart
//!
//! **The round-trip path.** A node loaded and not touched carries the lexeme it was written as
//! (`raw`), the line and column it was written at (`pos`), and its comments and blank lines in
//! source order. Emitting it is then bookkeeping rather than judgement: write the lexeme
//! verbatim, at the recorded column, with the trivia back in their slots. Nothing in this path
//! consults a style rule, a width or an indent option, which is what makes `load` → `dump`
//! byte-identical. A file that is never asked about is never reformatted.
//!
//! **The layout path.** A node the user built or modified has no `raw` and no useful position.
//! Only then do [`EmitOptions`] and [`mod@scalar`] decide anything: which quoting style a value
//! can take, which column a key lands in, whether a flow collection is too wide for one line.
//!
//! The switch between them is one rule, applied per construct: *a recorded position is used
//! only while the cursor is still on the line it names.* A model that matches its source never
//! leaves that line, so the round trip is exact; a model that has been edited falls off it at
//! the edit and the layout path takes over from there. The cursor also never jumps more than
//! one line ([`layout`]), so a deleted entry closes up instead of leaving a hole.
//!
//! # White space
//!
//! White space between two lexemes belongs to neither of them, and white space at the end of a
//! line belongs to nothing at all, so the model records it beside the tree, on the document
//! itself. `Document::line_space` holds every source line the writer cannot reproduce from a
//! column alone: the ones with a TAB in them, and the ones with a trailing run.
//! [`Writer`](layout::Writer) echoes those verbatim while the model still matches the page.
//! Every other line is plain spaces to a recorded column, which is what the writer produces
//! anyway.
//!
//! The gaps *inside* a construct are recorded on the construct: `Entry::colon` for a key's `:`,
//! `Node::anchor_at` and `Node::tag_at` for the properties that sit ahead of a node, and
//! `Node::flow_seps` for everything between a flow collection's brackets.

mod layout;
mod scalar;
mod trivia;

use crate::node::{Document, Entry, Node, NodeId, NodeKind, NodeTag, Position, ScalarStyle, Style};
use std::fmt::Write as _;

use crate::trivia::Trivia;
use layout::{Place, Writer, child_col, dash_col, placed};

pub use scalar::{EmitError, ScalarAnalysis, ScalarContext, analyze, choose_style};

/// The line break an emitted stream is written with.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum LineBreak {
    /// Taken from the documents: `\r\n` if any lexeme spans lines with it, `\n` otherwise.
    ///
    /// Only the breaks *inside* a lexeme are visible here: a block scalar, a folded quoted
    /// scalar, a multi-line plain scalar. A CRLF file with no multi-line scalar in it therefore
    /// comes back with `\n`; set [`LineBreak::CrLf`] explicitly for those.
    // The break between two lines is not a fact the model records, so a lexeme is the only
    // place one can be read off.
    // ponytail: inference from lexemes only, exact if the loader ever records the document's
    // own break.
    #[default]
    Auto,
    /// `\n`.
    Lf,
    /// `\r\n`.
    CrLf,
    /// `\r`.
    Cr,
}

impl LineBreak {
    fn resolve(self, docs: &[Document]) -> &'static str {
        match self {
            Self::Lf => "\n",
            Self::CrLf => "\r\n",
            Self::Cr => "\r",
            Self::Auto => {
                let crlf = docs.iter().flat_map(|d| &d.nodes).any(|n| {
                    n.raw
                        .as_deref()
                        .is_some_and(|raw| raw.contains("\r\n") || raw.contains('\r'))
                });
                if crlf { "\r\n" } else { "\n" }
            }
        }
    }
}

/// How to write a stream.
///
/// Every one of these is consulted **only** where the source cannot answer: they lay out nodes
/// the user built, and leave nodes the user did not touch exactly as they were found. The
/// exceptions are the three deliberate overrides. [`EmitOptions::explicit_start`],
/// [`EmitOptions::explicit_end`] and [`EmitOptions::default_flow_style`] replace what the
/// document had when they are `Some`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EmitOptions {
    /// Columns a nested mapping is indented by. Default 2.
    pub map_indent: usize,
    /// Columns a sequence's items are indented by, measured from the key that holds them.
    /// Default 2.
    pub seq_indent: usize,
    /// Columns the `-` itself is indented by, inside `seq_indent`. Default 0.
    pub seq_offset: usize,
    /// Column to fold at. Default 80.
    pub width: usize,
    /// The line break to write. Default [`LineBreak::Auto`].
    pub line_break: LineBreak,
    /// Force `---` on (or off); `None` keeps what each document had.
    pub explicit_start: Option<bool>,
    /// Force `...` on (or off); `None` keeps what each document had.
    pub explicit_end: Option<bool>,
    /// Force every collection into flow (or block) style; `None` keeps each node's own style.
    pub default_flow_style: Option<bool>,
    /// Keep the quoting style of a modified scalar when it is still legal.
    pub preserve_quotes: bool,
    /// Write non-ASCII characters as themselves rather than escaping them.
    pub allow_unicode: bool,
}

impl Default for EmitOptions {
    fn default() -> Self {
        Self {
            map_indent: 2,
            seq_indent: 2,
            seq_offset: 0,
            width: 80,
            line_break: LineBreak::Auto,
            explicit_start: None,
            explicit_end: None,
            default_flow_style: None,
            preserve_quotes: false,
            allow_unicode: true,
        }
    }
}

/// Writes a stream of documents as YAML text.
///
/// For documents loaded and not modified this reproduces the source byte for byte, including
/// its comments, blank lines, indentation, quoting, directives and byte-order mark.
///
/// # Errors
///
/// Returns [`EmitError`] if a scalar the user built cannot be written in the style it asks for.
///
/// # Panics
///
/// Panics if a document refers to a [`NodeId`] that is not one of its own nodes.
///
/// # Examples
///
/// ```
/// let docs = yamluna_core::parse("a: 1  # kept\n")?;
/// let opts = yamluna_core::EmitOptions::default();
/// assert_eq!(yamluna_core::emit(&docs, &opts)?, "a: 1  # kept\n");
/// # Ok::<(), Box<dyn std::error::Error>>(())
/// ```
pub fn emit(docs: &[Document], opts: &EmitOptions) -> Result<String, EmitError> {
    let brk = opts.line_break.resolve(docs);
    let mut e = Emitter {
        w: Writer::new(brk),
        brk,
        map_ind: step(opts.map_indent, 2),
        seq_ind: step(opts.seq_indent, 2),
        offset: step(opts.seq_offset, 0),
        o: opts,
        headed: None,
        ahead: None,
    };
    if let Some(first) = docs.first() {
        if first.bom {
            e.w.bom();
        }
        e.w.keep_line_space(first.line_space.clone());
    }
    let mut open = false;
    for doc in docs {
        e.document(doc, open)?;
        open = !opts.explicit_end.unwrap_or(doc.explicit_end);
    }
    if let Some(last) = docs.last() {
        if last.final_line_break {
            e.w.fresh_line();
        } else if let Some(rest) = last
            .stream_tail
            .strip_prefix("\r\n")
            .or_else(|| last.stream_tail.strip_prefix('\n'))
        {
            // A tail that opens with a break has the last line's own trailing white space in
            // front of it, and only `fresh_line` writes that back.
            e.w.fresh_line();
            e.w.push(rest);
        } else {
            e.w.push(&last.stream_tail);
        }
    }
    Ok(e.w.finish())
}

fn step(v: usize, fallback: u32) -> u32 {
    u32::try_from(v).unwrap_or(fallback)
}

/// Where a node is being written.
// Four independent facts about one position in the document; a state machine over them would be
// a state machine with sixteen states.
#[allow(clippy::struct_excessive_bools)]
#[derive(Clone, Copy, Debug)]
struct Site {
    /// The column this node's own lines begin at: a block sequence's `-`, a block mapping's
    /// keys. Only a fallback for a node whose position the source still steers.
    ind: u32,
    /// What introduces the node.
    lead: Lead,
    /// Reproduce the recorded positions (the round-trip path).
    echo: bool,
    /// Inside `[]` or `{}`.
    flow: bool,
    /// The node is a mapping key.
    key: bool,
    /// The caller writes this node's end-of-line comment, because a `,` goes first.
    defer_eol: bool,
}

/// What comes immediately before a node.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Lead {
    /// Whatever the caller has just written: a `:`, a `[`, a `,`.
    Follows {
        /// The column to start a fresh line at, when one has to be started.
        at: u32,
        /// A space is required between that and the node.
        sep: bool,
        /// The node is a mapping value, so a comment on the `key:` line is *its* end-of-line
        /// comment even though the value itself is on a line further down. A flow item after a
        /// `[` or a `,` is not: a comment after it trails the item.
        value: bool,
    },
    /// A line of the node's own, introduced by an indicator at `col`.
    Line {
        /// Column of the indicator, or of the node when there is none.
        col: u32,
        /// `-`, `?` or `:`.
        mark: Option<char>,
        /// Whether the node may stay on the line the caller is on (a compact `- key: v`).
        share: bool,
    },
}

impl Lead {
    /// Where the node goes once whatever introduces it has been written.
    fn place(self) -> Place {
        match self {
            Self::Follows { sep, at, .. } => Place::Same { sep, fallback: at },
            // `space()` does nothing at the start of a line, so asking for separation costs
            // nothing there and is exactly right after a `-` the node is sharing a line with.
            Self::Line { col, mark, .. } => Place::Same {
                sep: true,
                fallback: if mark.is_some() { col + 2 } else { col },
            },
        }
    }
}

struct Emitter<'a> {
    w: Writer,
    o: &'a EmitOptions,
    /// The resolved line break, which a scalar the emitter renders has to use as well.
    brk: &'static str,
    map_ind: u32,
    seq_ind: u32,
    offset: u32,
    /// The node whose `&anchor` and tag [`Emitter::document`] has written already, because the
    /// source put them on the `---` line, ahead of the comment that ends it.
    headed: Option<NodeId>,
    /// The node whose own run of trivia [`Emitter::flow_items`] has written already, because
    /// the flow separation in front of it went back verbatim and that run was inside it.
    ahead: Option<NodeId>,
}

impl Emitter<'_> {
    // ------------------------------------------------------------------ documents

    fn document(&mut self, d: &Document, force_start: bool) -> Result<(), EmitError> {
        // A directive region the source wrote is echoed whole, and the trivia it swallowed are
        // the ones the emitter must not write a second time.
        let region = d.directives_raw.as_ref();
        let lead = &d.leading[region.map_or(0, |(_, n)| *n).min(d.leading.len())..];
        let (lead_lines, marker_comment) = trivia::split_marker_comment(lead);
        trivia::run(&mut self.w, lead_lines);
        if let Some((raw, _)) = region {
            self.w.fresh_line();
            self.w.push(raw);
        } else {
            // `%TAG` lines sit on both sides of the `%YAML` line, and `tags_before_version`
            // says how many of them were above it.
            let split = d.tags_before_version.min(d.tag_directives.len());
            let tag_line = |w: &mut Writer, t: &crate::node::TagDirective| {
                w.fresh_line();
                w.push(&format!("%TAG {} {}", t.handle, t.prefix));
            };
            for t in &d.tag_directives[..split] {
                tag_line(&mut self.w, t);
            }
            if let Some((major, minor)) = d.version {
                self.w.fresh_line();
                self.w.push(&format!("%YAML {major}.{minor}"));
            }
            for t in &d.tag_directives[split..] {
                tag_line(&mut self.w, t);
            }
        }
        // A directive line, or a second document that the one before it did not close, has to
        // be introduced: `---` is not decoration there, it is what keeps the stream parseable.
        // A region that holds nothing but `...` markers introduces nothing, so it forces no
        // `---`.
        let start = self.o.explicit_start.unwrap_or(d.explicit_start)
            || force_start
            || d.version.is_some()
            || region.is_some_and(|(raw, _)| raw.lines().any(|l| l.starts_with('%')))
            || !d.tag_directives.is_empty();
        if start {
            self.w.fresh_line();
            self.w.push("---");
            if !d.explicit_start {
                // A marker the source did not have shifts everything below it.
                self.w.desync();
            }
        }
        // A comment ends its line, so a tag or an anchor the source wrote on the `---` line was
        // written before it. Those go down here, ahead of the comment; `node` is told so it
        // does not write them a second time.
        let marker_line = self.w.line();
        self.headed = d.root.filter(|&r| {
            marker_comment.is_some()
                && [d.node(r).tag_at, d.node(r).anchor_at]
                    .into_iter()
                    .flatten()
                    .any(|p| p.line == marker_line)
        });
        if let Some(r) = self.headed {
            let place = Place::Same {
                sep: true,
                fallback: 0,
            };
            self.head(d.node(r), place, true, true);
        }
        trivia::eol(&mut self.w, marker_comment);
        if let Some(root) = d.root {
            self.node(
                d,
                root,
                Site {
                    ind: 0,
                    lead: Lead::Follows {
                        at: 0,
                        sep: true,
                        value: false,
                    },
                    echo: true,
                    flow: false,
                    key: false,
                    defer_eol: false,
                },
            )?;
        }
        if self.o.explicit_end.unwrap_or(d.explicit_end) {
            self.w.fresh_line();
            self.w.push("...");
        }
        trivia::run(&mut self.w, &d.trailing);
        Ok(())
    }

    // ------------------------------------------------------------------ nodes

    /// Writes one node. Returns whether its end-of-line comment has been written, which only a
    /// caller that passed `defer_eol` needs to know.
    fn node(&mut self, d: &Document, id: NodeId, site: Site) -> Result<bool, EmitError> {
        let n = d.node(id);
        // The flow separation in front of this node held its trivia and has written them.
        let pre_written = self.ahead.take() == Some(id);
        let flow = self.flow_style(n);
        // Forcing a loaded collection into the other style invalidates every position under it.
        let echo = site.echo && !(n.is_collection() && flow != (n.style == Style::Flow));
        let block_coll = n.is_collection() && !flow;
        let block_scalar = matches!(
            n.style,
            Style::Scalar(ScalarStyle::Literal | ScalarStyle::Folded)
        ) && n.raw.is_some();
        // A node the source never wrote stands at whatever token came next, so its position
        // must not drag an anchor or a tag down a line with it.
        let synthetic = is_empty_scalar(n);
        let is_value = matches!(site.lead, Lead::Follows { value: true, .. });
        let follows = matches!(site.lead, Lead::Follows { .. });
        // A node with neither a lexeme nor a recorded position is one the user built. It is
        // also the only way to tell a constructed document from a loaded one at line 0 column
        // 0, where the two coincide.
        if echo && !n.is_collection() && n.raw.is_none() && !placed(n.pos) {
            self.w.desync();
        }
        // A collection introduced by a `-` or a `?` may put its first child on that same line
        // (`- key: value`); one introduced by a `key:` may not.
        let compact = matches!(site.lead, Lead::Line { mark: Some(_), .. });

        let place = site.lead.place();
        // The end-of-line comment of a node whose content starts further down belongs to the
        // line the cursor is on now, the `key:` line. A block scalar's belongs on its header
        // line, which `scalar` writes, because the `|` has to come first.
        let opening = |w: &Writer| {
            !block_scalar
                && (block_coll || (is_value && echo && w.synced() && n.pos.line > w.line()))
        };

        let mut eol_written = false;
        // `document` writes the root's properties itself when the source put them on the `---`
        // line, because the comment that ends that line had to come after them.
        let pre_headed = self.headed.take() == Some(id);
        let mut headed = pre_headed;
        if follows {
            // A block collection's anchor and tag stay up here on the `key:` line, ahead of the
            // comment; everything else travels down with the node.
            if block_coll {
                headed |= !pre_headed && self.head(n, place, echo, true);
            }
            if opening(&self.w) {
                trivia::eol(&mut self.w, n.trivia.eol.as_ref());
                eol_written = true;
            }
        }
        let (above, beside) = trivia::split_own_line(&n.trivia.before);
        // Trivia on the indicator's own line, held back until the node's properties are down.
        let mut after_head: &[Trivia] = &[];
        let inside = follows && block_scalar;
        if !inside && !pre_written {
            trivia::run(&mut self.w, if follows { &n.trivia.before } else { above });
        }
        if let Lead::Line { col, mark, share } = site.lead {
            if !share
                || self.w.commented()
                || (echo && self.w.synced() && n.pos.line > self.w.line())
            {
                self.w.fresh_line();
            }
            if let Some(ch) = mark {
                self.w.pad_to(col);
                self.w.push_char(ch);
                // A comment ends its line, so an anchor or a tag the source wrote on the
                // indicator's line came before it. A block collection's properties are written
                // right here; every other node's are written below, so its trivia wait for
                // them.
                if block_coll {
                    headed |= !pre_headed && self.head(n, place, echo, true);
                    trivia::run(&mut self.w, beside);
                } else {
                    after_head = beside;
                }
                if opening(&self.w) {
                    trivia::eol(&mut self.w, n.trivia.eol.as_ref());
                    eol_written = true;
                }
            }
        }
        if !block_coll {
            headed |= !pre_headed && self.head(n, place, echo, block_scalar || synthetic);
            trivia::run(&mut self.w, after_head);
        }
        let place = place.separated(headed);

        match &n.kind {
            NodeKind::Alias { anchor } => {
                self.w.place(n.pos, place, echo);
                self.w.push(&format!("*{anchor}"));
            }
            NodeKind::Scalar => {
                let leading = if inside { &n.trivia.before[..] } else { &[] };
                eol_written |= self.scalar(n, &site, place, echo, leading)?;
            }
            NodeKind::Sequence { items } => {
                if flow || items.is_empty() {
                    self.w.place(n.pos, place, echo);
                    eol_written |= self.flow(d, n, &n.children(), site.ind, echo)?;
                } else {
                    self.sequence(d, n, items, site.ind, echo, compact)?;
                }
            }
            NodeKind::Mapping { entries } => {
                if flow || entries.is_empty() {
                    self.w.place(n.pos, place, echo);
                    eol_written |= self.flow(d, n, &n.children(), site.ind, echo)?;
                } else {
                    self.mapping(d, n, entries, site.ind, echo, compact)?;
                }
            }
        }

        if !eol_written && !site.defer_eol {
            trivia::eol(&mut self.w, n.trivia.eol.as_ref());
            eol_written = true;
        }
        // A collection writes its own `after` once its children are done. A scalar's is only
        // ever filled from the Python side, by `C_VALUE_POST` on a scalar-valued entry, and in
        // ruamel that comment is stored and then never written: the store-then-silently-discard
        // path this library exists to not have. Inside a flow collection there is no line below
        // the value to put it on, so it stays with the collection.
        if !n.is_collection() && !site.flow && !n.trivia.after.is_empty() {
            trivia::run(&mut self.w, &n.trivia.after);
        }
        Ok(eol_written)
    }

    /// The `&anchor` and the tag, which sit ahead of the node, each at the line and column the
    /// source put it at. Those need be neither the node's nor each other's.
    fn head(&mut self, n: &Node, place: Place, echo: bool, stay: bool) -> bool {
        if n.anchor.is_none() && n.tag.is_none() {
            return false;
        }
        let Place::Same { sep, fallback } = place;
        let mut first = true;
        let mut write = |w: &mut Writer, at: Option<Position>, text: String| {
            let at = at.filter(|_| echo && w.synced());
            // A property travels down to the node's own line, except where the node's first
            // line is the one the cursor is on already: a block collection opens on the `key:`
            // line, and a block scalar's `|` header is about to be written here. A recorded
            // line answers that question outright, and is the only thing that can say the
            // source put the property on a line of its own (`key: &a` / ` !!map` / `  a: b`).
            let down = w.commented()
                || match at {
                    Some(p) => p.line > w.line(),
                    None => first && !stay && echo && w.synced() && n.pos.line > w.line(),
                };
            if down {
                w.fresh_line();
                // `fallback` is the layout's answer for a property with no column of its own.
                // A property that has one must not be padded past it first: `pad_to` only ever
                // moves forward, so a recorded column further left would be lost.
                if at.is_none() {
                    w.pad_to(fallback);
                }
            }
            // `pos` is the node's *content*, so a property has a column of its own.
            w.at(at, sep || !first, echo);
            first = false;
            w.push(&text);
        };
        let tag = || n.tag.as_ref().map(|t| (n.tag_at, render_tag(t)));
        let anchor = || n.anchor.as_ref().map(|a| (n.anchor_at, format!("&{a}")));
        let (a, b) = if n.tag_first {
            (tag(), anchor())
        } else {
            (anchor(), tag())
        };
        for (at, text) in [a, b].into_iter().flatten() {
            write(&mut self.w, at, text);
        }
        true
    }

    /// A scalar. Returns whether it wrote the node's end-of-line comment (a block scalar does:
    /// the comment belongs on the header line, ahead of the body).
    fn scalar(
        &mut self,
        n: &Node,
        site: &Site,
        place: Place,
        echo: bool,
        leading_blanks: &[Trivia],
    ) -> Result<bool, EmitError> {
        let Some(raw) = n.raw.as_deref() else {
            return self.new_scalar(n, site, place, echo);
        };
        if matches!(
            n.style,
            Style::Scalar(ScalarStyle::Literal | ScalarStyle::Folded)
        ) {
            // `raw` is the header, the break that followed it and the body from column zero:
            // the header goes where the cursor is, the body carries its own indentation.
            self.block_scalar(n, raw, leading_blanks, echo);
            return Ok(true);
        }
        // An implicit empty node writes nothing, so it must also *move* nothing: its recorded
        // position is the next token's (see `is_empty_scalar`), and placing the cursor there
        // would open the line that token is going to open anyway.
        if raw.is_empty() {
            return Ok(false);
        }
        self.w.place(n.pos, place, echo);
        self.w.push(raw);
        Ok(false)
    }

    /// A block scalar, from its own lexeme or from freshly rendered text. Its end-of-line
    /// comment belongs on the header line, which is why this is not a plain `push`.
    fn block_scalar(&mut self, n: &Node, text: &str, leading_blanks: &[Trivia], echo: bool) {
        let (header, body) = split_first_break(text);
        // The header sits where the source put it, which need not be where the node's
        // properties ended: `!foo` on one line and `>1` on the next is two lexemes, not one
        // run.
        let at = n.header_at.filter(|_| echo && self.w.synced());
        // A comment owns the rest of its line: a header written onto it would be swallowed, and
        // the body below would then be read as a document of its own.
        if self.w.commented() || at.is_some_and(|p| p.line > self.w.line()) {
            self.w.fresh_line();
        }
        self.w.at(at, true, echo);
        self.w.push(header);
        trivia::eol(&mut self.w, n.trivia.eol.as_ref());
        // Empty lines between the header and the first content line are the scalar's own
        // leading content; the loader sees only the lines the span covers and files them as
        // trivia, so they go back inside here.
        for t in leading_blanks {
            if let Trivia::BlankLines(k) = t {
                for _ in 0..*k {
                    self.w.hard_break();
                }
            }
        }
        self.w.push(body);
    }

    /// A scalar the user built: the only place a style is chosen.
    fn new_scalar(
        &mut self,
        n: &Node,
        site: &Site,
        place: Place,
        echo: bool,
    ) -> Result<bool, EmitError> {
        let value = n.value.as_deref().unwrap_or_default();
        let ctx = ScalarContext {
            indent: site.ind as usize,
            in_flow: site.flow,
            is_key: site.key,
            // A node that still remembers where it was written is not re-wrapped: `width`
            // lays out what the user *built*, and re-folding an untouched line would move
            // every construct after it off the line the model gives it. A node the user
            // edited is a new value with no position, so it still folds.
            width: if placed(n.pos) { 0 } else { self.o.width },
            line_break: self.brk,
            allow_unicode: self.o.allow_unicode,
        };
        // A block style is what the value *is* (`LiteralScalarString`), not how it is quoted,
        // so it is asked for either way; a quoting style is only asked for under
        // `preserve_quotes`.
        let requested = match n.style {
            Style::Scalar(s @ (ScalarStyle::Literal | ScalarStyle::Folded)) => Some(s),
            Style::Scalar(s) if self.o.preserve_quotes => Some(s),
            _ => None,
        };
        // An explicit plain style is a statement about the value's *type*: a node holding the
        // integer 1 asks for `1`, not `'1'`. `choose_style` cannot know that; it refuses a
        // plain `1` because for a string that would read back as an integer. So an asked-for
        // plain style is honoured whenever it is syntactically writable here, and falls back to
        // the ladder when it is not.
        let style = if matches!(n.style, Style::Scalar(ScalarStyle::Plain))
            && scalar::plain_writable(value, &ctx)
        {
            ScalarStyle::Plain
        } else {
            choose_style(value, requested, &ctx)
        };
        let mut text = String::new();
        scalar::write(value, style, &ctx, &mut text)?;
        if matches!(style, ScalarStyle::Literal | ScalarStyle::Folded) {
            // Freshly rendered text, so the source's header position does not describe it.
            self.block_scalar(n, &text, &[], false);
            return Ok(true);
        }
        self.w.place(n.pos, place, echo);
        self.w.push(&text);
        Ok(false)
    }

    // ------------------------------------------------------------------ block collections

    fn sequence(
        &mut self,
        d: &Document,
        n: &Node,
        items: &[NodeId],
        ind: u32,
        echo: bool,
        compact: bool,
    ) -> Result<(), EmitError> {
        let first = items
            .first()
            .map_or(Position::default(), |i| d.node(*i).pos);
        let dash = if echo {
            dash_col(n.pos, first, ind)
        } else {
            ind
        };
        let content = dash + self.seq_ind.saturating_sub(self.offset).max(1);
        trivia::run(&mut self.w, &n.trivia.inner);
        for (i, item) in items.iter().enumerate() {
            self.node(
                d,
                *item,
                Site {
                    ind: content,
                    lead: Lead::Line {
                        col: dash,
                        mark: Some('-'),
                        share: compact && i == 0,
                    },
                    echo,
                    flow: false,
                    key: false,
                    defer_eol: false,
                },
            )?;
        }
        trivia::run(&mut self.w, &n.trivia.after);
        Ok(())
    }

    fn mapping(
        &mut self,
        d: &Document,
        n: &Node,
        entries: &[Entry],
        ind: u32,
        echo: bool,
        compact: bool,
    ) -> Result<(), EmitError> {
        let keys = if echo { child_col(n.pos, ind) } else { ind };
        trivia::run(&mut self.w, &n.trivia.inner);
        for (i, e) in entries.iter().enumerate() {
            self.entry(d, e, keys, echo, compact && i == 0)?;
        }
        trivia::run(&mut self.w, &n.trivia.after);
        Ok(())
    }

    fn entry(
        &mut self,
        d: &Document,
        e: &Entry,
        keys: u32,
        echo: bool,
        share: bool,
    ) -> Result<(), EmitError> {
        let value_ind = keys
            + if matches!(d.node(e.value).kind, NodeKind::Sequence { .. }) {
                self.offset
            } else {
                self.map_ind
            };
        let key_site = |mark| Site {
            ind: keys,
            lead: Lead::Line {
                col: keys,
                mark,
                share,
            },
            echo,
            flow: false,
            key: true,
            defer_eol: false,
        };
        let value_site = |lead| Site {
            ind: value_ind,
            lead,
            echo,
            flow: false,
            key: false,
            defer_eol: false,
        };
        if e.explicit {
            self.node(d, e.key, key_site(Some('?')))?;
            // `? key` with nothing under it: the source wrote no `:` line at all, and the value
            // node stands where the *next* token does, so writing one would invent a line. A
            // recorded `:` says the source did write one, empty value or not.
            let v = d.node(e.value);
            if is_absent(v) && e.colon.is_none() {
                self.node(
                    d,
                    e.value,
                    value_site(Lead::Follows {
                        at: value_ind,
                        sep: false,
                        value: true,
                    }),
                )?;
            } else {
                self.node(
                    d,
                    e.value,
                    value_site(Lead::Line {
                        col: keys,
                        mark: Some(':'),
                        share: false,
                    }),
                )?;
            }
            return Ok(());
        }
        self.node(d, e.key, key_site(None))?;
        self.colon(d.node(e.key), e.colon, echo);
        self.node(
            d,
            e.value,
            value_site(Lead::Follows {
                at: value_ind,
                sep: !adjacent(e.colon, d.node(e.value).pos, echo),
                value: true,
            }),
        )?;
        Ok(())
    }

    /// The `:` of an entry, in the column the source put it in. An alias key needs a space of
    /// its own when the source did not record one: `*a:` would scan the `:` as part of the
    /// anchor.
    fn colon(&mut self, key: &Node, at: Option<Position>, echo: bool) {
        let usable = at.filter(|_| echo && self.w.synced());
        if self.w.commented() || usable.is_some_and(|p| p.line > self.w.line()) {
            self.w.fresh_line();
        }
        self.w
            .at(at, matches!(key.kind, NodeKind::Alias { .. }), echo);
        self.w.push_char(':');
    }

    /// A `,` between two of a flow collection's lexemes. Nothing may share a line with a
    /// comment.
    fn comma(&mut self) {
        if self.w.commented() {
            self.w.fresh_line();
        }
        self.w.push_char(',');
    }

    /// Echoes verbatim what the source put between two of a flow collection's lexemes, with the
    /// comments it held written back into the places it marked for them.
    ///
    /// A run goes back as written, punctuation and white space and all, which is what tells
    /// `[1, 2]` from `[1, 2, ]` from `[ 1 , 2 ]` and remembers that the gap in `[a\t, b]` was a
    /// TAB. Its comments are not in it: their text lives in the trivia slots, where the Python
    /// side can edit it, and the run carries a bare `#` where each one stood. Writing the run
    /// in the pieces between those marks puts a comment back at the exact column the source
    /// gave it and leaves the `,` on whichever side of it the source wrote it.
    ///
    /// `head` is a comment that may or may not belong to this gap, because a flow collection's
    /// end-of-line comment sits either after its `[` or after its `]`, one slot for two places.
    /// `rest` are the ones that certainly do, in source order. The marks settle it: a run
    /// records one per comment it really held, so a count that does not add up either way means
    /// the run no longer describes this gap and nothing is echoed. Returns `None` then, and
    /// otherwise whether `head` was used.
    ///
    /// Every trivium listed is written from here or not at all: blank lines are already in the
    /// run as the breaks they are, so a caller that gets `Some` must write none of them again.
    fn echo_gap(
        &mut self,
        run: Option<&str>,
        spread: bool,
        head: Option<&Trivia>,
        rest: &[&Trivia],
    ) -> Option<bool> {
        let r = run.filter(|_| !spread)?;
        // A run is the source's own text, and the source stops describing this page the moment
        // something does not land where the model said it would: a document rebuilt from nodes
        // the user made still carries the runs it was loaded with, and echoing them there
        // writes punctuation for lexemes that are no longer being written.
        if self.w.commented() || !self.w.synced() {
            return None;
        }
        let marks = r.matches('#').count();
        // A comment owns the rest of its line, so the source's own break follows every mark.
        // One that does not is a run some edit has been through, and echoing it would put a
        // lexeme inside a comment.
        if !r.split('#').skip(1).all(|p| p.starts_with(['\n', '\r'])) {
            return None;
        }
        let comments = || rest.iter().copied().filter(|t| t.text().is_some());
        let used = match marks.checked_sub(comments().count()) {
            Some(0) => false,
            Some(1) if head.is_some_and(|t| t.text().is_some()) => true,
            _ => return None,
        };
        let mut texts = used
            .then_some(head)
            .flatten()
            .into_iter()
            .chain(comments())
            .filter_map(Trivia::text);
        let mut pieces = r.split('#');
        self.w.push_separation(pieces.next().unwrap_or_default());
        for piece in pieces {
            // The run already wrote the white space in front of the comment, so its text goes
            // down exactly where the cursor stands: no `space()`, no padding to a column.
            self.w.comment(texts.next().expect("one comment per mark"));
            self.w.push_separation(piece);
        }
        Some(used)
    }

    // ------------------------------------------------------------------ flow collections

    fn flow(
        &mut self,
        d: &Document,
        n: &Node,
        children: &[NodeId],
        ind: u32,
        echo: bool,
    ) -> Result<bool, EmitError> {
        // One line first. If the source was steering after all, that *is* the source's layout
        // and there is nothing to reconsider; otherwise `width` gets a say.
        let mark = self.w.mark();
        let mut opened = self.flow_items(d, n, children, ind, echo, false)?;
        let over = !(echo && self.w.synced()) && {
            let text = self.w.since(&mark);
            !text.contains(['\n', '\r'])
                && mark.col() as usize + text.chars().count() > self.o.width
        };
        if over {
            self.w.rewind(&mark);
            opened = self.flow_items(d, n, children, ind, echo, true)?;
        }
        Ok(opened)
    }

    /// The run the source wrote in the `i`-th gap of a flow collection, while the source is
    /// still steering.
    ///
    /// A run stops describing this page the moment the emitter stops landing where the model
    /// says it should. A document rebuilt from nodes the user made carries the runs it was
    /// loaded with, and the lexemes those runs punctuate are no longer the ones being written.
    /// From the first thing that does not land, the layout answers for every gap.
    fn recorded<'s>(&self, seps: Option<&'s [String]>, i: usize) -> Option<&'s str> {
        seps.filter(|_| self.w.synced())
            .and_then(|s| s.get(i))
            .map(String::as_str)
    }

    // One pass over one flow collection: a gap, an item, a gap, an item. Splitting it would
    // hand the halves a dozen arguments and the `pending` comment between them.
    #[allow(clippy::too_many_lines)]
    fn flow_items(
        &mut self,
        d: &Document,
        n: &Node,
        children: &[NodeId],
        ind: u32,
        echo: bool,
        spread: bool,
    ) -> Result<bool, EmitError> {
        let map = matches!(n.kind, NodeKind::Mapping { .. });
        // What the source wrote between the lexemes, while the source is still steering. A
        // collection the user built recorded nothing, and one an insertion or a deletion has
        // been through no longer has one run per gap; the layout answers for both. `[a: 1]`,
        // `[? a : b]`, `[&c c: d]`: a single pair written with no brackets of its own. The
        // loader records one run per child for it and one *more* than that for everything else,
        // because a bracket-less pair has no closing bracket to separate from: the `,` after it
        // belongs to the collection that holds it. A mapping's children always come in pairs,
        // so a braced collection whose runs went stale cannot pass for one.
        let unbraced = map && !children.is_empty() && n.flow_seps.len() == children.len();
        let seps = (echo && (unbraced || n.flow_seps.len() == children.len() + 1))
            .then_some(&n.flow_seps[..]);
        let braces = !echo || !unbraced;
        if braces {
            self.w.push_char(if map { '{' } else { '[' });
        }
        let home = self.w.home();
        let open = self.w.line();
        let content = ind.max(home + self.map_ind);
        // Whether the collection's own end-of-line comment turned out to be the one the source
        // wrote after its `[`, and has gone down there. The other thing that slot can hold is
        // the comment after the `]`, which is not this collection's business but its caller's.
        let mut opened = false;

        // `chunks(2)` walks the same pairs `entries` holds, in the same order, so the `:` of
        // the n-th chunk is the `:` of the n-th entry.
        let entries: &[Entry] = match &n.kind {
            NodeKind::Mapping { entries } => entries,
            _ => &[],
        };
        let step = if map { 2 } else { 1 };
        // The lexeme whose end-of-line comment is waiting for the `,` that follows it.
        let mut pending: Option<NodeId> = None;
        // Whether the last thing written left the cursor short of where the source is: a run
        // that could not be echoed still owes its line break, and the node after it normally
        // pays that by placing itself. A value the parser supplied writes nothing and places
        // nothing. The run in front of the closing bracket is then no longer measured from
        // where the cursor is, and the layout has to answer for the bracket instead.
        let mut stale = false;
        for (chunk, pair) in children.chunks(step).enumerate() {
            let i = chunk * step;
            // The gap in front of the item, and with it the `,` that separates it from the one
            // before.
            let mut spaced = false;
            // Everything the source wrote in this gap: the end-of-line comment of the lexeme
            // before it, then, for the first gap, what stands between the `[` and the first
            // child, then the run of own-line trivia the child itself carries.
            let first = *pair.first().expect("chunks are never empty");
            let gap: Vec<&Trivia> = pending
                .and_then(|p| d.node(p).trivia.eol.as_ref())
                .into_iter()
                .chain(if i == 0 { &n.trivia.inner[..] } else { &[] })
                .chain(&d.node(first).trivia.before)
                .collect();
            // The run wrote the gap, comments and blank lines and all: nothing here may write
            // any of it a second time.
            if let Some(head) = self.echo_gap(
                self.recorded(seps, i),
                spread,
                (i == 0).then_some(n.trivia.eol.as_ref()).flatten(),
                &gap,
            ) {
                opened |= head;
                pending = None;
                self.ahead = Some(first);
            } else {
                if i == 0 {
                    trivia::run(&mut self.w, &n.trivia.inner);
                }
                if self.recorded(seps, i).map_or(i > 0, |r| r.contains(',')) {
                    self.comma();
                    spaced = true;
                }
                if let Some(p) = pending.take() {
                    trivia::eol(&mut self.w, d.node(p).trivia.eol.as_ref());
                }
            }
            let lead = if spread {
                Lead::Line {
                    col: content,
                    mark: None,
                    share: false,
                }
            } else {
                Lead::Follows {
                    at: content,
                    sep: spaced,
                    value: false,
                }
            };
            let site = |lead, key, defer_eol| Site {
                ind: content,
                lead,
                echo,
                flow: true,
                key,
                defer_eol,
            };
            let last = *pair.last().expect("chunks are never empty");
            stale = false;
            if !map {
                if !self.node(d, last, site(lead, false, true))? {
                    pending = Some(last);
                }
                continue;
            }
            let key = pair[0];
            // `{a: 1, b}`: a key the source wrote with no `:` and no value, so the run where
            // the `:` would be holds none. The parser supplied the value, and writing it back
            // would invent a `:`, unless it has picked up trivia of its own, which must not be
            // dropped to save the two characters.
            let bare = pair.len() == 1
                || (self.recorded(seps, i + 1).is_some_and(|r| !r.contains(':'))
                    && is_absent(d.node(last))
                    && d.node(last).trivia.is_empty());
            let written = self.node(d, key, site(lead, true, bare))?;
            if bare && !written {
                pending = Some(key);
            }
            if pair.len() == 1 {
                continue;
            }
            // Where the `:` goes, or, for a bare key, the separation the source wrote in its
            // place, which is where the `,` of the next entry lives.
            let colon = entries.get(chunk).and_then(|e| e.colon);
            let mut value_sep = false;
            // The key's end-of-line comment, then the value's own run. The value's *own*
            // end-of-line comment can also be in here, since `{k: # c` over `  v}` puts it on
            // the key's line, but the caller writes that one from `pending` in the gap after
            // the value, so a run that holds it will not add up here and is left alone.
            let gap: Vec<&Trivia> = pending
                .and_then(|p| d.node(p).trivia.eol.as_ref())
                .into_iter()
                .chain(&d.node(last).trivia.before)
                .collect();
            if self
                .echo_gap(self.recorded(seps, i + 1), spread, None, &gap)
                .is_some()
            {
                pending = None;
                // A bare key's value is never written, so nothing would ever take the mark.
                self.ahead = (!bare).then_some(last);
            } else if bare {
                if self.recorded(seps, i + 1).is_some_and(|r| r.contains(',')) {
                    self.comma();
                }
                // A run that crossed a line still owes its break, and a bare key's value was
                // supplied by the parser: it writes nothing and places nothing, so nothing
                // after it pays. The closing bracket does, via `close_flow`.
                stale = self
                    .recorded(seps, i + 1)
                    .is_some_and(|r| r.contains(['\n', '\r']));
            } else {
                self.colon(d.node(key), colon, echo);
                value_sep = !adjacent(colon, d.node(last).pos, echo);
                // `{omitted value:,\n}`: the `,` that ends the entry is in the same run as the
                // `:`, because the value between them was supplied by the parser.
                if self.recorded(seps, i + 1).is_some_and(|r| r.contains(',')) {
                    self.comma();
                    value_sep = true;
                }
                stale = is_absent(d.node(last));
            }
            if let Some(p) = pending.take() {
                trivia::eol(&mut self.w, d.node(p).trivia.eol.as_ref());
            }
            if bare {
                continue;
            }
            if !self.node(
                d,
                last,
                site(
                    Lead::Follows {
                        at: content,
                        sep: value_sep,
                        value: true,
                    },
                    false,
                    true,
                ),
            )? {
                pending = Some(last);
            }
        }

        // The gap in front of the closing bracket. A trailing `,` is the source's business, not
        // the layout's: whether one was written is part of the run. Only a collection with no
        // recorded separation falls back to the spelling most files use, which is a comma when
        // the closing bracket takes a line of its own.
        let tail = children.len();
        let gap: Vec<&Trivia> = pending
            .and_then(|p| d.node(p).trivia.eol.as_ref())
            .into_iter()
            .chain(&n.trivia.after)
            .collect();
        let echoed = self
            .echo_gap(self.recorded(seps, tail), spread || stale, None, &gap)
            .is_some();
        if !echoed {
            if braces
                && !children.is_empty()
                && self
                    .recorded(seps, tail)
                    .map_or(self.w.line() > open, |r| r.contains(','))
            {
                self.comma();
            }
            if let Some(p) = pending.take() {
                trivia::eol(&mut self.w, d.node(p).trivia.eol.as_ref());
            }
            trivia::run(&mut self.w, &n.trivia.after);
        }
        if braces {
            if !echoed || self.w.commented() {
                self.close_flow(self.recorded(seps, tail).filter(|_| !spread), home, open);
            }
            self.w.push_char(if map { '}' } else { ']' });
        }
        Ok(opened)
    }

    /// Moves the cursor to where a flow collection's closing bracket goes, when the separation
    /// in front of it was not echoed verbatim.
    ///
    /// A collection whose content took more than one line closes on a line of its own, whatever
    /// else is known. That is what keeps the bracket off the end of the last item, and it holds
    /// even for a tree that has stopped matching its source. The column is then whatever the
    /// recorded run left after its last break, or the indentation of the line that opened the
    /// collection.
    fn close_flow(&mut self, run: Option<&str>, home: u32, open: u32) {
        // A recorded run that crossed a line is not echoed (its comments come from the trivia
        // slots), so the break it held is owed here: `[\n]` closes on the line below its `[`
        // even though nothing was written between them.
        let crossed = run.is_some_and(|r| r.contains(['\n', '\r']));
        if self.w.line() > open || self.w.commented() || crossed {
            self.w.fresh_line();
        }
        let col = run
            .and_then(|r| r.rsplit_once(['\n', '\r']))
            .map_or(home, |(_, t)| {
                u32::try_from(t.chars().count()).unwrap_or(home)
            });
        self.w.pad_to(col);
    }

    fn flow_style(&self, n: &Node) -> bool {
        match self.o.default_flow_style {
            Some(flow) if n.is_collection() => flow,
            _ => n.style == Style::Flow,
        }
    }
}

/// A tag as it was written: `!`, `!!str`, `!e!custom`, or the verbatim `!<uri>` when no handle
/// was in scope for it.
fn render_tag(t: &NodeTag) -> String {
    match (t.handle.as_str(), t.suffix.as_str()) {
        // The non-specific tag: "resolve me by context, not by the schema".
        ("", "!") => "!".to_owned(),
        ("", suffix) => format!("!<{suffix}>"),
        (handle, suffix) => format!("{handle}{}", escape_tag(suffix)),
    }
}

/// Percent-escapes the characters a tag suffix may not spell literally.
///
/// The scanner decodes `%21` to `!` on the way in, so writing the decoded text back would
/// produce a tag that does not re-parse.
fn escape_tag(suffix: &str) -> String {
    let mut out = String::with_capacity(suffix.len());
    for c in suffix.chars() {
        match c {
            '!' | ',' | '[' | ']' | '{' | '}' | '%' => {
                let _ = write!(out, "%{:02X}", c as u8);
            }
            _ => out.push(c),
        }
    }
    out
}

/// Whether the source wrote its value hard against the `:` (`"a":b`), which is the one place
/// the separation a `:` normally needs is not written.
fn adjacent(colon: Option<Position>, value: Position, echo: bool) -> bool {
    echo && colon.is_some_and(|c| c.line == value.line && c.col + 1 == value.col)
}

/// A scalar with no text: `key:` with nothing after it. The parser gives it the span of
/// whatever token came next, so its position says where the *document* goes on, not where it
/// goes.
fn is_empty_scalar(n: &Node) -> bool {
    matches!(n.kind, NodeKind::Scalar) && n.raw.as_deref() == Some("")
}

/// A node that stands for something the source did not write at all: the value of a `? key`
/// with no `: value` line under it. An anchor or a tag means the source did write something.
fn is_absent(n: &Node) -> bool {
    is_empty_scalar(n) && n.anchor.is_none() && n.tag.is_none()
}

/// Splits a block scalar's lexeme into its header and everything from the break onwards.
fn split_first_break(raw: &str) -> (&str, &str) {
    match raw.find(['\r', '\n']) {
        Some(i) => raw.split_at(i),
        None => (raw, ""),
    }
}

#[cfg(test)]
mod tests {
    use super::{EmitOptions, LineBreak, ScalarStyle, emit};
    use crate::parse;

    /// Loads and dumps with the defaults.
    fn round(src: &str) -> String {
        let docs = parse(src).unwrap_or_else(|e| panic!("{src:?}: {e}"));
        emit(&docs, &EmitOptions::default()).expect("emit")
    }

    #[track_caller]
    fn exact(src: &str) {
        assert_eq!(round(src), src);
    }

    #[test]
    fn block_mappings_and_sequences() {
        exact("a: 1\nb: 2\n");
        exact("key:\n  nested: 1\n  deeper:\n    leaf: true\n");
        exact("key:\n- zero offset\n- two\n");
        exact("key:\n  - indented\n  - two\n");
        exact("key:\n      - wide\n");
        exact("- a\n- b\n");
    }

    #[test]
    fn compact_nesting() {
        exact("outer:\n  - - a\n    - b\n  - - c\n");
        exact("outer:\n  - key: value\n    other: 2\n");
        exact("outer:\n- key:\n  - nested\n  - items\n- other: 1\n");
    }

    #[test]
    fn empty_values_and_collections() {
        exact("key:\nseq:\n-\nmap: {}\nlist: []\n");
        exact("nested:\n  empty_child:\n");
    }

    #[test]
    fn explicit_keys() {
        exact("? [a, b]\n: sequence key\n");
        exact("? an explicit key with no value\n?\n: an empty key with a value\n");
        exact("simple: 1\n? explicit: 2\n");
    }

    /// A single pair inside a flow sequence writes no brackets of its own, and whatever
    /// introduces it (a `?`, its own anchor or tag, a bracketed key) must not be mistaken for
    /// one. The pair records one separation run per child where everything else records one
    /// more, and that length is what the emitter reads it back from.
    #[test]
    fn a_flow_pair_that_wrote_no_brackets_gets_none_back() {
        exact("[\n? foo\n bar : baz\n]\n");
        exact("[a: 1]\n");
        exact("[? a : b]\n");
        exact("[&c c: d]\n");
        exact("[!!str c: d]\n");
        exact("[[a]: b]\n");
        exact("[{a: b}:c]\n");
        exact("[:a: b]\n");
        exact("[ : empty ]\n");
        exact("[a: ]\n");
        // ...and everything that *did* write brackets keeps them.
        exact("[{? a : b}]\n");
        exact("[{a: 1}]\n");
        exact("&g { g: h }\n");
        exact("!!map [a: 1]\n");
        exact("{: empty}\n");
    }

    #[test]
    fn flow_collections() {
        exact("a: [1, 2, 3]\n");
        exact("a: {x: 1, y: 2}\n");
        exact("a: [{b: 1}, [c], {d: [e]}]\n");
        exact("a: [\n  one,\n  two,\n]\n");
    }

    /// Where a flow collection's commas, brackets and `:` went is recorded, not guessed: each
    /// of these has a sibling that spells the same thing the other way, and only a recorded
    /// fact can tell the two apart.
    #[test]
    fn flow_punctuation_is_reproduced() {
        exact("a: [1, 2, 3, ]\n");
        exact("a: {x: 1, y: 2, }\n");
        exact("a: [ 1 , 2 ,  3 ]\n");
        exact("a: {x: , y}\n");
        exact("a: [\n  one,\n  two\n]\n");
        exact("a: {\n  x: 1,\n  y: 2\n}\n");
        // A key with no `:` beside one with a value, and a `,` after each.
        exact("a: {w, x: 1, y, z: 2}\n");
    }

    #[test]
    fn anchors_aliases_and_tags() {
        exact("anchor: &s plain value\nalias: *s\n");
        exact("anchor_map: &m\n  a: 1\nalias: *m\n");
        exact("anchor: &s v\nalias_as_key:\n  *s : value\n");
        exact("tagged: !!str 123\nlocal: !mytag value\nverbatim: !<tag:x,2000:t> v\n");
        exact("base: &base\n  a: 1\nmerge:\n  <<: *base\n  b: 2\n");
    }

    /// Either order of the two node properties comes back the way it was written.
    #[test]
    fn anchor_and_tag_keep_the_order_they_were_written_in() {
        exact("anchor_then_tag: &a !!str v\ntag_then_anchor: !!str &t v\n");
        exact("tag_then_anchor: !!str &t v\nanchor_then_tag: &a !!str v\n");
        // On a collection, whose properties stay on the `key:` line.
        exact("seq: !!seq &s\n  - 1\nmap: &m !!map\n  a: 1\n");
        exact("flow: !!seq &s [1]\nother: &m !!map {a: 1}\n");
        // With a comment between the previous token and the properties.
        exact("k: # c\n  !!str &t v\n");
        // As the document root, with no `---` to scan forward from.
        exact("!!str &t v\n");
        exact("--- !!str &t v\n");
        // An empty node the properties are all there is of.
        exact("empty: !!str &t\nnext: 1\n");
    }

    #[test]
    fn block_scalars() {
        exact("literal: |\n  one\n  two\n");
        exact("folded: >-\n  text\n");
        exact("keep: |+\n  text\n\n\nafter: 1\n");
        exact("indented: |2\n    two spaces of content\n");
        exact("header: | # a comment on the header\n  body\n");
    }

    #[test]
    fn documents_and_directives() {
        exact("---\na: 1\n...\n");
        exact("a: 1\n---\nb: 2\n");
        exact("%YAML 1.2\n---\nkey: value\n");
        exact("%TAG ! tag:x/\n---\na: !Thing {}\n");
        exact("--- # after the marker\na: 1\n... # after the end marker\n");
        exact("---\n---\n---\n");
    }

    /// A `...` that closes no document is not a parser event, so the only record of it is the
    /// line: above the document below it, or on a rootless one when nothing follows.
    #[test]
    fn a_document_end_marker_that_closes_nothing_comes_back() {
        exact("...\n");
        exact("# comment\n...\n");
        exact("...\n...\n");
        exact("...\na: 1\n");
        exact("a: 1\n...\n...\n");
        exact("a: 1\n...\n...\nb: 2\n");
        exact("a: 1\n...\n... # after the marker\n---\nb: 2\n");
        exact("a: 1\n...\n\n# between\n...\n\nb: 2\n");
        // The spec's own bare-document example (yaml-test-suite M7A3).
        exact("Bare\ndocument\n...\n# No document\n...\n|\n%!PS-Adobe-2.0 # Not the first line\n");
    }

    /// `%YAML` sits where the source put it among the `%TAG` lines, on either side or between.
    #[test]
    fn directives_keep_the_order_they_were_written_in() {
        exact("%YAML 1.2\n%TAG ! tag:x/\n---\na: !Thing {}\n");
        exact("%TAG ! tag:x/\n%YAML 1.2\n---\na: !Thing {}\n");
        exact("%TAG !a! tag:a/\n%YAML 1.2\n%TAG !b! tag:b/\n---\na: !a!T {}\n");
        // Per document, not per stream.
        exact("%TAG ! tag:x/\n%YAML 1.2\n---\na: 1\n...\n%YAML 1.2\n%TAG ! tag:y/\n---\nb: 2\n");
    }

    #[test]
    fn trivia_come_back_where_they_were() {
        exact("# a header\n\n# after a blank line\nfirst: 1\n");
        exact("a: 1  # eol\n# own line\nb: 2\n");
        exact(
            "items:\n  # before the first\n  - a\n  # between\n  - b\n  # after the last\nnext: 1\n",
        );
        exact("a: 1\n\n\nb: 2\n\n");
        exact("# nothing but a comment\n");
        exact("a: 1\n# a final comment with no newline");
    }

    #[test]
    fn a_bom_and_crlf_come_back() {
        exact("\u{feff}a: 1\n");
        exact("a: |\r\n  body\r\nb: 2\r\n");
    }

    #[test]
    fn a_deleted_entry_does_not_leave_a_hole() {
        let mut docs = parse("a: 1\nb: 2\nc: 3\n").expect("loads");
        let root = docs[0].root.expect("root");
        if let crate::NodeKind::Mapping { entries } = &mut docs[0].node_mut(root).kind {
            entries.remove(1);
        }
        assert_eq!(
            emit(&docs, &EmitOptions::default()).expect("emit"),
            "a: 1\nc: 3\n"
        );
    }

    #[test]
    fn a_built_scalar_is_laid_out_not_echoed() {
        let mut docs = parse("a: 1\nb: 2\n").expect("loads");
        let root = docs[0].root.expect("root");
        let value = match &docs[0].node(root).kind {
            crate::NodeKind::Mapping { entries } => entries[0].value,
            _ => unreachable!(),
        };
        let n = docs[0].node_mut(value);
        n.raw = None;
        n.value = Some("a new value".to_owned());
        assert_eq!(
            emit(&docs, &EmitOptions::default()).expect("emit"),
            "a: a new value\nb: 2\n"
        );
    }

    #[test]
    fn options_only_reach_what_the_source_did_not_write() {
        let docs = parse("a: 1\nb: [2, 3]\n").expect("loads");
        let opts = EmitOptions {
            map_indent: 8,
            seq_indent: 6,
            width: 3,
            line_break: LineBreak::Cr,
            ..EmitOptions::default()
        };
        // Every node here came from the source, so none of that applies.
        assert_eq!(emit(&docs, &opts).expect("emit"), "a: 1\rb: [2, 3]\r");
    }

    #[test]
    fn explicit_start_and_end_can_be_forced() {
        let docs = parse("a: 1\n").expect("loads");
        let opts = EmitOptions {
            explicit_start: Some(true),
            explicit_end: Some(true),
            ..EmitOptions::default()
        };
        assert_eq!(emit(&docs, &opts).expect("emit"), "---\na: 1\n...\n");
    }

    // ---------------------------------------------------------------- the layout path

    /// A document built rather than loaded: no `raw`, no positions, so every column below comes
    /// from [`EmitOptions`].
    fn built() -> crate::Document {
        use crate::{Document, Entry, Node, NodeKind, Style};
        let mut d = Document::default();
        let scalar = |d: &mut Document, v: &str, style| {
            let mut n = Node::new(NodeKind::Scalar, Style::Scalar(style));
            n.value = Some(v.to_owned());
            d.push(n)
        };
        let items = vec![
            scalar(&mut d, "one", ScalarStyle::Plain),
            scalar(&mut d, "two", ScalarStyle::Plain),
        ];
        let seq = d.push(Node::new(NodeKind::Sequence { items }, Style::Block));
        let inner_k = scalar(&mut d, "deep", ScalarStyle::Plain);
        let inner_v = scalar(&mut d, "leaf", ScalarStyle::Plain);
        let inner = d.push(Node::new(
            NodeKind::Mapping {
                entries: vec![Entry {
                    key: inner_k,
                    value: inner_v,
                    merge: false,
                    explicit: false,
                    colon: None,
                }],
            },
            Style::Block,
        ));
        let compact = d.push(Node::new(
            NodeKind::Sequence { items: vec![inner] },
            Style::Block,
        ));
        let mut entries = Vec::new();
        for (k, value) in [("seq", seq), ("compact", compact)] {
            let key = scalar(&mut d, k, ScalarStyle::Plain);
            entries.push(Entry {
                key,
                value,
                merge: false,
                explicit: false,
                colon: None,
            });
        }
        let root = d.push(Node::new(NodeKind::Mapping { entries }, Style::Block));
        d.root = Some(root);
        d.final_line_break = true;
        d
    }

    #[test]
    fn a_built_document_is_laid_out_by_the_options() {
        let d = [built()];
        assert_eq!(
            emit(&d, &EmitOptions::default()).expect("emit"),
            "seq:\n- one\n- two\ncompact:\n- deep: leaf\n"
        );
        let wide = EmitOptions {
            map_indent: 4,
            seq_indent: 4,
            seq_offset: 2,
            ..EmitOptions::default()
        };
        assert_eq!(
            emit(&d, &wide).expect("emit"),
            "seq:\n  - one\n  - two\ncompact:\n  - deep: leaf\n"
        );
    }

    #[test]
    fn a_built_flow_collection_spreads_when_it_passes_width() {
        use crate::{Document, Node, NodeKind, Style};
        let mut d = Document::default();
        let items: Vec<_> = ["alpha", "bravo", "charlie"]
            .iter()
            .map(|v| {
                let mut n = Node::new(NodeKind::Scalar, Style::Scalar(ScalarStyle::Plain));
                n.value = Some((*v).to_owned());
                d.push(n)
            })
            .collect();
        let root = d.push(Node::new(NodeKind::Sequence { items }, Style::Flow));
        d.root = Some(root);
        d.final_line_break = true;
        let docs = [d];
        assert_eq!(
            emit(&docs, &EmitOptions::default()).expect("emit"),
            "[alpha, bravo, charlie]\n"
        );
        let narrow = EmitOptions {
            width: 10,
            ..EmitOptions::default()
        };
        assert_eq!(
            emit(&docs, &narrow).expect("emit"),
            "[\n  alpha,\n  bravo,\n  charlie,\n]\n"
        );
    }

    /// Loading, blanking a scalar's lexeme and dumping is the "modified node" path: only that
    /// node is re-decided, and `preserve_quotes` is what decides it.
    #[test]
    fn preserve_quotes_only_reaches_a_modified_scalar() {
        let restyle = |preserve| {
            let mut docs = parse("a: \"quoted\"\nb: \"other\"\n").expect("loads");
            let root = docs[0].root.expect("root");
            let crate::NodeKind::Mapping { entries } = &docs[0].node(root).kind else {
                unreachable!()
            };
            let value = entries[0].value;
            docs[0].node_mut(value).raw = None;
            let opts = EmitOptions {
                preserve_quotes: preserve,
                ..EmitOptions::default()
            };
            emit(&docs, &opts).expect("emit")
        };
        assert_eq!(restyle(true), "a: \"quoted\"\nb: \"other\"\n");
        assert_eq!(restyle(false), "a: quoted\nb: \"other\"\n");
    }

    /// A node that asks for a plain style says something about its *type*: the representer
    /// only asks for plain where the value either is a number or would not read back as one,
    /// so `1` must come out `1`, not `'1'`. An unwritable value still falls back.
    #[test]
    fn an_explicit_plain_style_is_honoured_where_it_is_writable() {
        use crate::{Document, Entry, Node, NodeKind, Style};
        let built = |value: &str| {
            let mut d = Document::default();
            let mut key = Node::new(NodeKind::Scalar, Style::Scalar(ScalarStyle::Plain));
            key.value = Some("a".to_owned());
            let key = d.push(key);
            let mut n = Node::new(NodeKind::Scalar, Style::Scalar(ScalarStyle::Plain));
            n.value = Some(value.to_owned());
            let value = d.push(n);
            let root = d.push(Node::new(
                NodeKind::Mapping {
                    entries: vec![Entry {
                        key,
                        value,
                        merge: false,
                        explicit: false,
                        colon: None,
                    }],
                },
                Style::Block,
            ));
            d.root = Some(root);
            d.final_line_break = true;
            emit(&[d], &EmitOptions::default()).expect("emit")
        };
        assert_eq!(built("1"), "a: 1\n");
        assert_eq!(built("true"), "a: true\n");
        assert_eq!(built("-42"), "a: -42\n");
        // Not writable as a plain scalar here, so the ladder decides after all.
        assert_eq!(built(""), "a: ''\n");
        assert_eq!(built("x: y"), "a: 'x: y'\n");
    }

    /// `width` lays out what the user built. A node that still knows where it was written is
    /// echoed, and re-folding it would push every construct after it off its recorded line.
    #[test]
    fn width_never_refolds_a_node_that_kept_its_position() {
        let long = ["wordwordwordwordword"; 6].join(" "); // 125 chars, spaces past column 80
        let src = format!("k: {long}\nnext: 1\n");
        let mut docs = parse(&src).expect("loads");
        let root = docs[0].root.expect("root");
        let crate::NodeKind::Mapping { entries } = &docs[0].node(root).kind else {
            unreachable!()
        };
        let value = entries[0].value;
        // The lexeme is gone (the value was read out and put back), the position is not.
        docs[0].node_mut(value).raw = None;
        assert_eq!(emit(&docs, &EmitOptions::default()).expect("emit"), src);
        // A node with no position is the one `width` is for.
        docs[0].node_mut(value).pos = crate::Position::default();
        let folded = emit(&docs, &EmitOptions::default()).expect("emit");
        assert!(folded.contains("\n  "), "{folded:?}");
    }

    #[test]
    fn default_flow_style_forces_a_collection_over() {
        let docs = parse("a:\n  b: 1\n  c: 2\n").expect("loads");
        let opts = EmitOptions {
            default_flow_style: Some(true),
            ..EmitOptions::default()
        };
        assert_eq!(emit(&docs, &opts).expect("emit"), "{a: {b: 1, c: 2}}\n");
    }
}
