//! The emitter (DESIGN §2.4): documents in, YAML text out.
//!
//! # Two paths, kept apart
//!
//! **The round-trip path.** A node loaded and not touched carries the lexeme it was written as
//! (`raw`), the line and column it was written at (`pos`), and its comments and blank lines in
//! source order. Emitting it is then bookkeeping rather than judgement: write the lexeme
//! verbatim, at the recorded column, with the trivia back in their slots. Nothing in this path
//! consults a style rule, a width or an indent option, which is what makes `load` → `dump`
//! byte-identical (DESIGN §6.2) — a file that is never asked about is never reformatted.
//!
//! **The layout path.** A node the user built or modified has no `raw` and no useful position.
//! Only then do [`EmitOptions`] and [`mod@scalar`] decide anything: which quoting style a value
//! can take, which column a key lands in, whether a flow collection is too wide for one line.
//!
//! The switch between them is one rule, applied per construct: *a recorded position is used only
//! while the cursor is still on the line it names.* A model that matches its source never leaves
//! that line, so the round trip is exact; a model that has been edited falls off it at the edit
//! and the layout path takes over from there. The cursor also never jumps more than one line
//! ([`layout`]), so a deleted entry closes up instead of leaving a hole.
//!
//! # What the model cannot say
//!
//! A handful of source details are simply not in the document model, and the emitter has to
//! choose. Each choice below is the common spelling, and each is listed in the round-trip test's
//! `KNOWN_FAILURES` where a corpus file exercises the other one:
//!
//! * `&anchor !tag value` — the model records both but not their order.
//! * `%YAML` before `%TAG` — the model records both but not their order.
//! * flow separators are written `, `, and a trailing comma appears only when the closing bracket
//!   goes on a line of its own.

mod layout;
mod scalar;
mod trivia;

use crate::node::{Document, Entry, Node, NodeId, NodeKind, NodeTag, Position, ScalarStyle, Style};
use crate::trivia::Trivia;
use layout::{Place, Writer, child_col, dash_col, placed};

pub use scalar::{EmitError, ScalarAnalysis, ScalarContext, analyze, choose_style};

/// The line break an emitted stream is written with.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum LineBreak {
    /// Take it from the documents: `\r\n` if any lexeme spans lines with it, `\n` otherwise.
    ///
    /// The break between two lines is not a fact the model records, so this can only see the
    /// breaks *inside* a lexeme — a block scalar, a folded quoted scalar, a multi-line plain
    /// scalar. A CRLF file with no multi-line scalar in it therefore comes back with `\n`; set
    /// [`LineBreak::CrLf`] explicitly for those.
    // ponytail: inference from lexemes only, exact if the loader ever records the document's own
    // break.
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
/// the user built, and leave nodes the user did not touch exactly as they were found. The two
/// exceptions are deliberate overrides — [`EmitOptions::explicit_start`],
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

/// Write a stream of documents.
///
/// For documents loaded and not modified this reproduces the source byte for byte, including its
/// comments, blank lines, indentation, quoting, directives and byte-order mark (DESIGN §6.2).
///
/// # Errors
/// [`EmitError`] if a scalar the user built cannot be written in the style it asks for.
pub fn emit(docs: &[Document], opts: &EmitOptions) -> Result<String, EmitError> {
    let brk = opts.line_break.resolve(docs);
    let mut e = Emitter {
        w: Writer::new(brk),
        brk,
        map_ind: step(opts.map_indent, 2),
        seq_ind: step(opts.seq_indent, 2),
        offset: step(opts.seq_offset, 0),
        o: opts,
    };
    if docs.first().is_some_and(|d| d.bom) {
        e.w.bom();
    }
    let mut open = false;
    for doc in docs {
        e.document(doc, open)?;
        open = !opts.explicit_end.unwrap_or(doc.explicit_end);
    }
    if docs.last().is_some_and(|d| d.final_line_break) {
        e.w.fresh_line();
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
    /// The column this node's own lines begin at: a block sequence's `-`, a block mapping's keys.
    /// Only a fallback for a node whose position the source still steers.
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
    /// Whatever the caller just wrote — a `:`, a `[`, a `,`.
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

struct Emitter<'a> {
    w: Writer,
    o: &'a EmitOptions,
    /// The resolved line break, which a scalar the emitter renders has to use as well.
    brk: &'static str,
    map_ind: u32,
    seq_ind: u32,
    offset: u32,
}

impl Emitter<'_> {
    // ------------------------------------------------------------------ documents

    fn document(&mut self, d: &Document, force_start: bool) -> Result<(), EmitError> {
        let (lead_lines, marker_comment) = trivia::split_marker_comment(&d.leading);
        trivia::run(&mut self.w, lead_lines);
        if let Some((major, minor)) = d.version {
            self.w.fresh_line();
            self.w.push(&format!("%YAML {major}.{minor}"));
        }
        for t in &d.tag_directives {
            self.w.fresh_line();
            self.w.push(&format!("%TAG {} {}", t.handle, t.prefix));
        }
        // A directive line, or a second document that the one before it did not close, has to be
        // introduced: `---` is not decoration there, it is what keeps the stream parseable.
        let start = self.o.explicit_start.unwrap_or(d.explicit_start)
            || force_start
            || d.version.is_some()
            || !d.tag_directives.is_empty();
        if start {
            self.w.fresh_line();
            self.w.push("---");
            if !d.explicit_start {
                // A marker the source did not have shifts everything below it.
                self.w.desync();
            }
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

    /// Write one node. Returns whether its end-of-line comment has been written, which only a
    /// caller that passed `defer_eol` needs to know.
    fn node(&mut self, d: &Document, id: NodeId, site: Site) -> Result<bool, EmitError> {
        let n = d.node(id);
        let flow = self.flow_style(n);
        // Forcing a loaded collection into the other style invalidates every position under it.
        let echo = site.echo && !(n.is_collection() && flow != (n.style == Style::Flow));
        let block_coll = n.is_collection() && !flow;
        let block_scalar = matches!(
            n.style,
            Style::Scalar(ScalarStyle::Literal | ScalarStyle::Folded)
        ) && n.raw.is_some();
        // A node the source never wrote stands at whatever token came next, so its position must
        // not drag an anchor or a tag down a line with it.
        let synthetic = is_empty_scalar(n);
        let is_value = matches!(site.lead, Lead::Follows { value: true, .. });
        let follows = matches!(site.lead, Lead::Follows { .. });
        // A node with neither a lexeme nor a recorded position is one the user built. It is also
        // the only way to tell a constructed document from a loaded one at line 0 column 0, where
        // the two coincide.
        if echo && !n.is_collection() && n.raw.is_none() && !placed(n.pos) {
            self.w.desync();
        }
        // A collection introduced by a `-` or a `?` may put its first child on that same line
        // (`- key: value`); one introduced by a `key:` may not.
        let compact = matches!(site.lead, Lead::Line { mark: Some(_), .. });

        let place = match site.lead {
            Lead::Follows { sep, at, .. } => Place::Same { sep, fallback: at },
            // `space()` does nothing at the start of a line, so asking for separation costs
            // nothing there and is exactly right after a `-` the node is sharing a line with.
            Lead::Line { col, mark, .. } => Place::Same {
                sep: true,
                fallback: if mark.is_some() { col + 2 } else { col },
            },
        };
        // The end-of-line comment of a node whose content starts further down belongs to the line
        // the cursor is on now — the `key:` line. A block scalar's belongs on its header line,
        // which `scalar` writes, because the `|` has to come first.
        let opening = |w: &Writer| {
            !block_scalar
                && (block_coll || (is_value && echo && w.synced() && n.pos.line > w.line()))
        };

        let mut eol_written = false;
        let mut headed = false;
        if follows {
            // A block collection's anchor and tag stay up here on the `key:` line, ahead of the
            // comment; everything else travels down with the node.
            if block_coll {
                headed |= self.head(n, place, echo, true);
            }
            if opening(&self.w) {
                trivia::eol(&mut self.w, n.trivia.eol.as_ref());
                eol_written = true;
            }
        }
        let (above, beside) = trivia::split_own_line(&n.trivia.before);
        let inside = follows && block_scalar;
        if !inside {
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
                trivia::run(&mut self.w, beside);
                if block_coll {
                    headed |= self.head(n, place, echo, true);
                }
                if opening(&self.w) {
                    trivia::eol(&mut self.w, n.trivia.eol.as_ref());
                    eol_written = true;
                }
            }
        }
        if !block_coll {
            headed |= self.head(n, place, echo, block_scalar || synthetic);
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
                    self.flow(d, n, &n.children(), site.ind, echo)?;
                } else {
                    self.sequence(d, n, items, site.ind, echo, compact)?;
                }
            }
            NodeKind::Mapping { entries } => {
                if flow || entries.is_empty() {
                    self.w.place(n.pos, place, echo);
                    self.flow(d, n, &n.children(), site.ind, echo)?;
                } else {
                    self.mapping(d, n, entries, site.ind, echo, compact)?;
                }
            }
        }

        if !eol_written && !site.defer_eol {
            trivia::eol(&mut self.w, n.trivia.eol.as_ref());
            eol_written = true;
        }
        Ok(eol_written)
    }

    /// The `&anchor` and the tag, which sit on the node's line ahead of it.
    fn head(&mut self, n: &Node, place: Place, echo: bool, stay: bool) -> bool {
        if n.anchor.is_none() && n.tag.is_none() {
            return false;
        }
        // Properties travel down to the node's own line — except where the node's first line is
        // the one the cursor is on already: a block collection opens on the `key:` line, and a
        // block scalar's `|` header is about to be written here.
        if !stay && (self.w.commented() || (echo && self.w.synced() && n.pos.line > self.w.line()))
        {
            let Place::Same { fallback, .. } = place;
            self.w.fresh_line();
            self.w.pad_to(fallback);
        }
        if let Place::Same { sep: true, .. } = place {
            self.w.space();
        }
        if let Some(a) = &n.anchor {
            self.w.push(&format!("&{a}"));
        }
        if let Some(t) = &n.tag {
            if n.anchor.is_some() {
                self.w.space();
            }
            self.w.push(&render_tag(t));
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
            // `raw` is the header, the break that followed it and the body from column zero: the
            // header goes where the cursor is, the body carries its own indentation.
            self.block_scalar(n, raw, leading_blanks);
            return Ok(true);
        }
        self.w.place(n.pos, place, echo);
        self.w.push(raw);
        Ok(false)
    }

    /// A block scalar, from its own lexeme or from freshly rendered text. Its end-of-line comment
    /// belongs on the header line, which is why this is not just a `push`.
    fn block_scalar(&mut self, n: &Node, text: &str, leading_blanks: &[Trivia]) {
        let (header, body) = split_first_break(text);
        self.w.space();
        self.w.push(header);
        trivia::eol(&mut self.w, n.trivia.eol.as_ref());
        // Empty lines between the header and the first content line are the scalar's own leading
        // content; the loader sees only the lines the span covers and files them as trivia, so
        // they go back inside here.
        for t in leading_blanks {
            if let Trivia::BlankLines(k) = t {
                for _ in 0..*k {
                    self.w.hard_break();
                }
            }
        }
        self.w.push(body);
    }

    /// A scalar the user built: the only place a style is chosen (DESIGN §2.4).
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
        // A block style is what the value *is* (`LiteralScalarString`), not how it is quoted, so
        // it is asked for either way; a quoting style is only asked for under `preserve_quotes`.
        let requested = match n.style {
            Style::Scalar(s @ (ScalarStyle::Literal | ScalarStyle::Folded)) => Some(s),
            Style::Scalar(s) if self.o.preserve_quotes => Some(s),
            _ => None,
        };
        // An explicit plain style is a statement about the value's *type*: a node holding the
        // integer 1 asks for `1`, not `'1'`. `choose_style` cannot know that — it refuses a
        // plain `1` because for a string that would read back as an integer — so an asked-for
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
            self.block_scalar(n, &text, &[]);
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
            // node stands where the *next* token does, so writing one would invent a line.
            let v = d.node(e.value);
            if is_absent(v) {
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
        self.colon(d.node(e.key));
        self.node(
            d,
            e.value,
            value_site(Lead::Follows {
                at: value_ind,
                sep: true,
                value: true,
            }),
        )?;
        Ok(())
    }

    /// The `:` of a simple entry. An alias key needs the space: `*a:` would scan the `:` as part
    /// of the anchor name.
    fn colon(&mut self, key: &Node) {
        if self.w.commented() {
            self.w.fresh_line();
        }
        if matches!(key.kind, NodeKind::Alias { .. }) {
            self.w.space();
        }
        self.w.push_char(':');
    }

    // ------------------------------------------------------------------ flow collections

    fn flow(
        &mut self,
        d: &Document,
        n: &Node,
        children: &[NodeId],
        ind: u32,
        echo: bool,
    ) -> Result<(), EmitError> {
        // One line first. If the source was steering after all, that *is* the source's layout
        // and there is nothing to reconsider; otherwise `width` gets a say.
        let mark = self.w.mark();
        self.flow_items(d, n, children, ind, echo, false)?;
        let over = !(echo && self.w.synced()) && {
            let text = self.w.since(&mark);
            !text.contains(['\n', '\r'])
                && mark.col() as usize + text.chars().count() > self.o.width
        };
        if over {
            self.w.rewind(&mark);
            self.flow_items(d, n, children, ind, echo, true)?;
        }
        Ok(())
    }

    fn flow_items(
        &mut self,
        d: &Document,
        n: &Node,
        children: &[NodeId],
        ind: u32,
        echo: bool,
        spread: bool,
    ) -> Result<(), EmitError> {
        let map = matches!(n.kind, NodeKind::Mapping { .. });
        // `[a: 1]`: a single pair written without braces. The parser synthesises the mapping with
        // the span of its key, which is how it is told apart from a real `{a: 1}`.
        let braces = !map
            || !echo
            || !placed(n.pos)
            || children.first().is_none_or(|k| n.pos != d.node(*k).pos);
        if braces {
            self.w.push_char(if map { '{' } else { '[' });
        }
        let home = self.w.home();
        let open = self.w.line();
        let content = ind.max(home + self.map_ind);
        trivia::run(&mut self.w, &n.trivia.inner);

        let mut prev: Option<(NodeId, bool)> = None;
        for pair in children.chunks(if map { 2 } else { 1 }) {
            if let Some((p, written)) = prev {
                self.w.push_char(',');
                if !written {
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
                    sep: prev.is_some(),
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
            if map {
                let key = pair[0];
                let written = self.node(d, key, site(lead, true, pair.len() == 1))?;
                if pair.len() == 1 {
                    prev = Some((key, written));
                } else {
                    self.colon(d.node(key));
                    let written = self.node(
                        d,
                        last,
                        site(
                            Lead::Follows {
                                at: content,
                                sep: true,
                                value: true,
                            },
                            false,
                            true,
                        ),
                    )?;
                    prev = Some((last, written));
                }
            } else {
                let written = self.node(d, last, site(lead, false, true))?;
                prev = Some((last, written));
            }
        }

        if let Some((p, written)) = prev {
            // A closing bracket on a line of its own takes a trailing comma with it.
            if self.w.line() > open {
                self.w.push_char(',');
            }
            if !written {
                trivia::eol(&mut self.w, d.node(p).trivia.eol.as_ref());
            }
        }
        trivia::run(&mut self.w, &n.trivia.after);
        if self.w.line() > open || self.w.commented() {
            self.w.fresh_line();
            self.w.pad_to(home);
        }
        if braces {
            self.w.push_char(if map { '}' } else { ']' });
        }
        Ok(())
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
        (handle, suffix) => format!("{handle}{suffix}"),
    }
}

/// A scalar with no text: `key:` with nothing after it. The parser gives it the span of whatever
/// token came next, so its position says where the *document* goes on, not where it goes.
fn is_empty_scalar(n: &Node) -> bool {
    matches!(n.kind, NodeKind::Scalar) && n.raw.as_deref() == Some("")
}

/// A node that stands for something the source did not write at all: the value of a `? key` with
/// no `: value` line under it. An anchor or a tag means the source did write something.
fn is_absent(n: &Node) -> bool {
    is_empty_scalar(n) && n.anchor.is_none() && n.tag.is_none()
}

/// Split a block scalar's lexeme into its header and everything from the break onwards.
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

    /// Load and dump with the defaults.
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

    #[test]
    fn flow_collections() {
        exact("a: [1, 2, 3]\n");
        exact("a: {x: 1, y: 2}\n");
        exact("a: [{b: 1}, [c], {d: [e]}]\n");
        exact("a: [\n  one,\n  two,\n]\n");
    }

    #[test]
    fn anchors_aliases_and_tags() {
        exact("anchor: &s plain value\nalias: *s\n");
        exact("anchor_map: &m\n  a: 1\nalias: *m\n");
        exact("anchor: &s v\nalias_as_key:\n  *s : value\n");
        exact("tagged: !!str 123\nlocal: !mytag value\nverbatim: !<tag:x,2000:t> v\n");
        exact("base: &base\n  a: 1\nmerge:\n  <<: *base\n  b: 2\n");
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
