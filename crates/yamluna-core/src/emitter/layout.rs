//! The output cursor, and the block-layout arithmetic that decides a column when the source
//! cannot.
//!
//! [`Writer`] knows where it is on the page and can only move forward. The free functions below
//! answer "which column" for the two constructs whose indicator has no recorded position of its
//! own: a block sequence's `-`, and a nested collection's content.
//!
//! The cursor never jumps more than one line. Every blank line of the source is recorded as
//! [`Trivia::BlankLines`](crate::Trivia::BlankLines), so in the round-trip path the trivia
//! already move the cursor down; a node's recorded line then either matches, and its recorded
//! column is used verbatim, or does not, and the computed column is used. That is what keeps a
//! deleted entry from leaving a blank line behind, and why a stale position can never open a
//! hole in the output.

use std::collections::HashMap;

use crate::node::Position;

/// Whether `pos` is a position a node actually carries, rather than the default one a
/// constructed node has.
#[must_use]
pub(super) fn placed(pos: Position) -> bool {
    // `Position::default()` is a real coordinate, the first character of a document, but a node
    // sitting there is the only node whose layout it cannot mis-drive, so counting it as
    // unplaced costs nothing.
    pos != Position::default()
}

/// Returns the column a block sequence's `-` indicators sit at.
///
/// That is the sequence's own recorded column when it has one distinct from its first item's,
/// and `fallback` otherwise.
///
/// # Arguments
///
/// * `seq`: the position recorded for the sequence node.
/// * `first`: the position recorded for its first item.
/// * `fallback`: the column to use when the source records nothing usable.
#[must_use]
pub(super) fn dash_col(seq: Position, first: Position, fallback: u32) -> u32 {
    // The scanner reports a block sequence at its first `-` except when the sequence sits at
    // the indentation of the mapping that holds it (`key:` on one line, `- item` at column 0 on
    // the next). No new indentation level is opened there, so the position reported for the
    // sequence is the first item's, which is exactly the case where the dash belongs at the
    // enclosing indent.
    if placed(seq) && seq != first {
        seq.col
    } else {
        fallback
    }
}

/// Returns the column the children of a block collection are laid out at when the source cannot
/// say.
#[must_use]
pub(super) fn child_col(first_child: Position, fallback: u32) -> u32 {
    if placed(first_child) {
        first_child.col
    } else {
        fallback
    }
}

/// Where a node goes once whatever introduces it has been written.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Place {
    /// On the current line, unless the node's recorded line is further down.
    ///
    /// `sep` requires at least one space of separation, which is what a `:` or a `-` needs and
    /// what a `[` or a `,` does not.
    Same {
        /// Whether a space must separate the node from what precedes it.
        sep: bool,
        /// The column to use when the recorded line is not the line we are on.
        fallback: u32,
    },
}

impl Place {
    /// Returns the same place, with separation required when `yes` is true.
    ///
    /// An anchor or a tag written just before the node is what leaves separation owed.
    #[must_use]
    pub(super) fn separated(self, yes: bool) -> Self {
        let Self::Same { sep, fallback } = self;
        Self::Same {
            sep: sep || yes,
            fallback,
        }
    }
}

/// The output buffer, and where on the page it is.
///
/// Columns are counted in characters, like [`Position`], so an emoji is one column wide.
pub(super) struct Writer {
    out: String,
    line: u32,
    col: u32,
    /// Spaces owed to `col` but not yet written, so a run of padding that is never followed by
    /// content cannot leave trailing white space behind.
    pending: u32,
    /// Whether this line has content. A line the cursor merely padded is still empty.
    dirty: bool,
    /// The column this line's first content went in, i.e. its indentation.
    home: u32,
    /// Whether the output is still landing where the model says it should. See
    /// [`Writer::desync`].
    synced: bool,
    /// Whether a comment has been written on this line, which makes the rest of it unusable.
    commented: bool,
    /// The source lines whose white space cannot be reproduced from a column alone. See
    /// `Document::line_space`.
    space: HashMap<u32, String>,
    /// The line break written for every new line.
    brk: &'static str,
}

impl Writer {
    pub(super) fn new(brk: &'static str) -> Self {
        Self {
            out: String::new(),
            line: 0,
            col: 0,
            pending: 0,
            dirty: false,
            home: 0,
            synced: true,
            commented: false,
            space: HashMap::new(),
            brk,
        }
    }

    /// Records the source's own white space, so the round-trip path can put it back.
    pub(super) fn keep_line_space(&mut self, space: HashMap<u32, String>) {
        self.space = space;
    }

    /// The source's own text for columns `a..b` of the line the cursor is on, when the model
    /// still matches the page and that text is nothing but white space.
    ///
    /// `None` for every line the writer can reproduce itself, which is almost all of them: only
    /// the lines holding a TAB or a trailing run are recorded at all.
    fn source_space(&self, a: u32, b: u32) -> Option<String> {
        if !self.synced || b <= a {
            return None;
        }
        let want = (b - a) as usize;
        let text: String = self
            .space
            .get(&self.line)?
            .chars()
            .skip(a as usize)
            .take(want)
            .collect();
        // The all-white-space test is what keeps this honest: a range the emitter has already
        // put its own content in never matches.
        (text.chars().count() == want && text.chars().all(|c| c == ' ' || c == '\t'))
            .then_some(text)
    }

    /// Writes the padding owed to `col`: the source's own characters where they are recorded,
    /// and spaces otherwise.
    fn flush_pending(&mut self) {
        match self.source_space(self.col - self.pending, self.col) {
            Some(text) => self.out.push_str(&text),
            None => {
                for _ in 0..self.pending {
                    self.out.push(' ');
                }
            }
        }
        self.pending = 0;
    }

    /// Writes back the white space this source line ended with.
    ///
    /// Padding that no content followed is dropped, as always: the line's own tail replaces it.
    fn line_tail(&mut self) {
        let from = self.col - self.pending;
        let Some(line) = self.space.get(&self.line) else {
            return;
        };
        if !self.synced {
            return;
        }
        let tail: String = line.chars().skip(from as usize).collect();
        // A block scalar's lines carry their tails inside the lexeme, and a line whose content
        // the emitter has changed ends somewhere else. Neither leaves an all-white-space
        // remainder here, so neither collects a tail.
        if tail.is_empty() || !tail.chars().all(|c| c == ' ' || c == '\t') {
            return;
        }
        self.col = from + u32::try_from(tail.chars().count()).unwrap_or(0);
        self.pending = 0;
        self.out.push_str(&tail);
    }

    pub(super) fn line(&self) -> u32 {
        self.line
    }

    /// Whether a comment owns the rest of this line, so nothing else may be written on it.
    pub(super) fn commented(&self) -> bool {
        self.commented
    }

    /// Appends a comment, which takes the rest of the line with it.
    pub(super) fn comment(&mut self, text: &str) {
        self.push(text);
        self.commented = true;
    }

    /// Whether recorded lines are still worth believing.
    pub(super) fn synced(&self) -> bool {
        self.synced
    }

    /// Stops believing recorded lines, for good.
    ///
    /// The first construct that does not land on the line the model gives it proves the model
    /// no longer describes this text: an entry was inserted, deleted or rebuilt. Recorded
    /// columns stay useful, since they are the file's indentation and it did not move, but
    /// recorded lines would from here on open holes, so the layout decides where lines break
    /// from this point on.
    pub(super) fn desync(&mut self) {
        self.synced = false;
    }

    /// Returns the indentation of the line the cursor is on: where its first content went.
    ///
    /// This is what a flow collection's closing bracket lines up with: the start of the line
    /// that opened it, not the bracket's own column.
    pub(super) fn home(&self) -> u32 {
        self.home
    }

    pub(super) fn finish(self) -> String {
        self.out
    }

    /// Writes a byte-order mark. It is not part of any line, so the cursor does not move.
    pub(super) fn bom(&mut self) {
        self.out.push('\u{feff}');
    }

    /// Appends text and follows the cursor through it.
    pub(super) fn push(&mut self, s: &str) {
        if s.is_empty() {
            return;
        }
        self.flush_pending();
        if !self.dirty {
            self.home = self.col;
        }
        self.dirty = true;
        self.out.push_str(s);
        let mut chars = s.chars().peekable();
        while let Some(c) = chars.next() {
            match c {
                '\n' => {
                    self.line += 1;
                    self.col = 0;
                }
                '\r' => {
                    if chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    self.line += 1;
                    self.col = 0;
                }
                _ => self.col += 1,
            }
        }
    }

    pub(super) fn push_char(&mut self, c: char) {
        self.push(c.encode_utf8(&mut [0; 4]));
    }

    /// Pads forward to `col`. Never moves backwards, and never writes the padding on its own.
    pub(super) fn pad_to(&mut self, col: u32) {
        if col > self.col {
            self.pending += col - self.col;
            self.col = col;
        }
    }

    /// Leaves at least one space of separation from whatever is already on this line.
    pub(super) fn space(&mut self) {
        if self.dirty {
            self.pad_to(self.col + 1);
        }
    }

    /// Starts a line, unless this one is still empty.
    pub(super) fn fresh_line(&mut self) {
        if self.dirty {
            self.line_tail();
            self.out.push_str(self.brk);
            self.line += 1;
            self.dirty = false;
            self.commented = false;
        }
        self.pending = 0;
        self.col = 0;
        self.home = 0;
    }

    /// Writes a line break whether or not this line has content.
    pub(super) fn hard_break(&mut self) {
        self.break_with(self.brk);
    }

    fn break_with(&mut self, brk: &str) {
        self.line_tail();
        self.out.push_str(brk);
        self.line += 1;
        self.col = 0;
        self.pending = 0;
        self.dirty = false;
        self.home = 0;
        self.commented = false;
    }

    /// Appends recorded separation, the white space and punctuation the source wrote between
    /// two lexemes, following the cursor through it the way writing it a piece at a time would.
    ///
    /// [`Writer::push`] treats its argument as one lexeme, so a `|+` block scalar ending in a
    /// break still owns the line below it. Separation behaves the other way round: a break in
    /// it ends the line for good, leaving the one below empty, free, and no longer spoken for
    /// by a comment.
    pub(super) fn push_separation(&mut self, s: &str) {
        let mut rest = s;
        while let Some(i) = rest.find(['\n', '\r']) {
            self.push(&rest[..i]);
            let brk = match &rest[i..] {
                r if r.starts_with("\r\n") => "\r\n",
                r => &r[..1],
            };
            self.break_with(brk);
            rest = &rest[i + brk.len()..];
        }
        self.push(rest);
    }

    /// Writes `n` empty lines.
    pub(super) fn blank_lines(&mut self, n: u32) {
        self.fresh_line();
        for _ in 0..n {
            self.hard_break();
        }
    }

    /// Moves to where a node goes.
    ///
    /// The recorded column is honoured only when the cursor is on the recorded line, which is
    /// the definition of "the model still matches its source" used throughout the emitter.
    ///
    /// # Arguments
    ///
    /// * `pos`: the position recorded for the node.
    /// * `place`: the separation the syntax needs, and the column to fall back on.
    /// * `echo`: whether recorded positions are believed at all. The layout path passes `false`
    ///   and gets the computed column every time.
    pub(super) fn place(&mut self, pos: Position, place: Place, echo: bool) {
        let Place::Same { sep, fallback } = place;
        if self.commented || (echo && self.synced && pos.line > self.line) {
            self.fresh_line();
        } else {
            self.at(Some(pos), sep, echo);
        }
        self.column(pos, fallback, echo);
    }

    /// Puts the cursor where a recorded lexeme was written: the gap before a `:`, before an
    /// `&anchor`, before a node.
    ///
    /// The white space is echoed rather than reconstructed. A recorded column is believed only
    /// while the cursor is still on the recorded line; `sep` is the separation the syntax needs
    /// when it is not, and is `false` only where the source itself wrote none (`"a":b`).
    pub(super) fn at(&mut self, pos: Option<Position>, sep: bool, echo: bool) {
        if sep {
            self.space();
        }
        if let Some(p) = pos.filter(|p| echo && self.synced && p.line == self.line) {
            self.pad_to(p.col);
        }
    }

    /// Puts the cursor in the node's column: the recorded one while the model still matches the
    /// page, the computed one otherwise.
    ///
    /// The computed column is applied only at the start of a line, because a column is where a
    /// line puts a node and not something to pad to mid-line.
    fn column(&mut self, pos: Position, fallback: u32, echo: bool) {
        if echo {
            if self.line == pos.line {
                self.pad_to(pos.col);
                return;
            }
            self.desync();
        }
        if !self.dirty {
            self.pad_to(fallback);
        }
    }

    /// Returns a mark of where the output ends, for [`Writer::rewind`].
    pub(super) fn mark(&mut self) -> Mark {
        Mark {
            len: self.out.len(),
            line: self.line,
            col: self.col,
            pending: self.pending,
            dirty: self.dirty,
            home: self.home,
            commented: self.commented,
        }
    }

    /// Returns everything written since `mark`.
    ///
    /// # Panics
    ///
    /// Panics if `mark` came from another writer, or if the output has already been rewound to
    /// before it.
    pub(super) fn since(&self, mark: &Mark) -> &str {
        &self.out[mark.len..]
    }

    /// Throws away everything written since `mark`.
    pub(super) fn rewind(&mut self, mark: &Mark) {
        self.out.truncate(mark.len);
        self.line = mark.line;
        self.col = mark.col;
        self.pending = mark.pending;
        self.dirty = mark.dirty;
        self.home = mark.home;
        self.commented = mark.commented;
    }
}

/// A saved cursor. See [`Writer::mark`].
pub(super) struct Mark {
    len: usize,
    line: u32,
    col: u32,
    pending: u32,
    dirty: bool,
    home: u32,
    commented: bool,
}

impl Mark {
    /// Returns the column the cursor was in when the mark was taken.
    pub(super) fn col(&self) -> u32 {
        self.col
    }
}

#[cfg(test)]
mod tests {
    use super::{Place, Position, Writer, dash_col};

    fn pos(line: u32, col: u32) -> Position {
        Position { line, col }
    }

    #[test]
    fn padding_that_is_never_used_is_not_written() {
        let mut w = Writer::new("\n");
        w.push("-");
        w.pad_to(8);
        w.fresh_line();
        w.push("x");
        assert_eq!(w.finish(), "-\nx");
    }

    #[test]
    fn the_cursor_follows_a_multi_line_lexeme() {
        let mut w = Writer::new("\n");
        w.push("key: ");
        w.push("|\n  one\n  two");
        assert_eq!(w.line(), 2);
        w.fresh_line();
        w.push("next");
        assert_eq!(w.finish(), "key: |\n  one\n  two\nnext");
    }

    /// A lexeme that ends in a break still leaves the line it lands on spoken for: the blank
    /// lines a `|+` keeps are content, and the next key belongs below them.
    #[test]
    fn a_lexeme_ending_in_a_break_still_occupies_its_line() {
        let mut w = Writer::new("\n");
        w.push("a: |+\n  text\n\n");
        w.fresh_line();
        w.push("b: 1");
        assert_eq!(w.finish(), "a: |+\n  text\n\n\nb: 1");
    }

    #[test]
    fn blank_lines_are_empty_lines() {
        let mut w = Writer::new("\n");
        w.push("a: 1");
        w.blank_lines(2);
        w.push("b: 2");
        assert_eq!(w.finish(), "a: 1\n\n\nb: 2");
        // The run left the cursor on a fresh line, so nothing adds a fourth break.
    }

    #[test]
    fn crlf_is_one_line() {
        let mut w = Writer::new("\r\n");
        w.push("a: |\r\n  x");
        w.fresh_line();
        assert_eq!(w.line(), 2);
        assert_eq!(w.finish(), "a: |\r\n  x\r\n");
    }

    #[test]
    fn a_recorded_column_is_used_only_on_its_own_line() {
        let mut w = Writer::new("\n");
        w.push("key:");
        // Same line as recorded: the source's extra spaces come back.
        w.place(
            pos(0, 7),
            Place::Same {
                sep: true,
                fallback: 2,
            },
            true,
        );
        w.push("v");
        assert_eq!(w.finish(), "key:   v");

        let mut w = Writer::new("\n");
        w.push("key:");
        // A stale line: the computed column wins, and recorded lines stop being believed.
        w.place(
            pos(9, 40),
            Place::Same {
                sep: true,
                fallback: 2,
            },
            true,
        );
        w.push("v");
        assert!(!w.synced());
        assert_eq!(w.finish(), "key:\n  v");
    }

    #[test]
    fn layout_mode_ignores_recorded_positions() {
        let mut w = Writer::new("\n");
        w.push("key:");
        w.place(
            pos(0, 7),
            Place::Same {
                sep: true,
                fallback: 2,
            },
            false,
        );
        w.push("v");
        assert_eq!(w.finish(), "key: v");
    }

    #[test]
    fn a_sequence_at_its_holders_indent_puts_the_dash_there() {
        // `key:` / `- one`: the scanner reports both the sequence and the item at the item.
        assert_eq!(dash_col(pos(1, 2), pos(1, 2), 0), 0);
        // `key:` / `  - one`: the sequence is reported at the dash.
        assert_eq!(dash_col(pos(1, 2), pos(1, 4), 0), 2);
        // Nothing recorded: the option decides.
        assert_eq!(dash_col(pos(0, 0), pos(0, 0), 6), 6);
    }

    /// Nothing may share a line with a comment: whatever comes next starts a line of its own,
    /// even when the layout would otherwise keep it where it is.
    #[test]
    fn a_comment_takes_the_rest_of_its_line() {
        let mut w = Writer::new("\n");
        w.push("a:");
        w.space();
        w.comment("# why");
        w.place(
            pos(0, 3),
            Place::Same {
                sep: true,
                fallback: 2,
            },
            false,
        );
        w.push("1");
        assert_eq!(w.finish(), "a: # why\n  1");
    }

    #[test]
    fn rewind_restores_the_cursor() {
        let mut w = Writer::new("\n");
        w.push("a: ");
        let m = w.mark();
        w.push("[1,\n  2]");
        assert_eq!(w.since(&m), "[1,\n  2]");
        w.rewind(&m);
        w.push("[1, 2]");
        assert_eq!(w.finish(), "a: [1, 2]");
    }
}
