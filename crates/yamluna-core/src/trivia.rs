//! Comments and blank lines, and the four slots a node hangs them in (DESIGN §2.1).

/// A comment or a run of blank lines.
///
/// Blank lines are first class rather than being smuggled into comment text as embedded newlines,
/// so "how many blank lines were there" has an answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Trivia {
    /// A `#` comment, from the `#` through the last character before the line break.
    Comment {
        /// The comment text, including the leading `#`, excluding the line break.
        text: String,
        /// `false` for an end-of-line comment (there is a non-space character before the `#` on
        /// the same line), `true` otherwise.
        own_line: bool,
        /// The 0-based column of the `#`, so the emitter can preserve alignment.
        col: u32,
    },
    /// A run of `n` consecutive empty lines. `n` is always ≥ 1.
    BlankLines(u32),
}

impl Trivia {
    /// The column of a comment; `None` for blank lines, which have no column.
    #[must_use]
    pub fn col(&self) -> Option<u32> {
        match self {
            Trivia::Comment { col, .. } => Some(*col),
            Trivia::BlankLines(_) => None,
        }
    }

    /// The comment text, or `None` for blank lines.
    #[must_use]
    pub fn text(&self) -> Option<&str> {
        match self {
            Trivia::Comment { text, .. } => Some(text),
            Trivia::BlankLines(_) => None,
        }
    }
}

/// The four ordered trivia slots of a node. Keyed by node identity, never by index.
///
/// Emission order for a scalar is `before`, the scalar, `eol`. For a collection it is `eol` (which
/// sits on the line that introduces the collection — the `key:` line, or the `|` header line of a
/// block scalar), then `before`, then `inner`, the children, and `after`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Trivia4 {
    /// Own-line trivia immediately preceding this node.
    pub before: Vec<Trivia>,
    /// The end-of-line comment on the node's own line.
    pub eol: Option<Trivia>,
    /// Trivia between a collection's start token and its first child.
    pub inner: Vec<Trivia>,
    /// Trailing trivia of a collection, before its parent continues.
    pub after: Vec<Trivia>,
}

impl Trivia4 {
    /// Whether every slot is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.before.is_empty()
            && self.eol.is_none()
            && self.inner.is_empty()
            && self.after.is_empty()
    }
}

/// The run of trivia seen since the last structural token, waiting to be given a slot.
///
/// Trivia are placed by [`Pending::take_from_col`] as collections close and by
/// [`Pending::take_all`] when the next node is created, which is what implements DESIGN §2.2
/// rule 2: a run of own-line comments that sits where several block collections end is split by
/// column, deepest collection first.
#[derive(Clone, Debug, Default)]
pub struct Pending(Vec<Trivia>);

impl Pending {
    /// Append one trivium to the run.
    pub fn push(&mut self, t: Trivia) {
        self.0.push(t);
    }

    /// Whether the run is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// Take the whole run.
    pub fn take_all(&mut self) -> Vec<Trivia> {
        std::mem::take(&mut self.0)
    }

    /// Take the leading part of the run that belongs *inside* a block collection whose content
    /// starts at column `col`.
    ///
    /// The run is cut at the first comment left of `col` — that one, and everything after it,
    /// belongs to a shallower slot. A blank-line run directly in front of the cut goes with what
    /// follows it (DESIGN §2.2 rule 3), so it is left behind too.
    pub fn take_from_col(&mut self, col: u32) -> Vec<Trivia> {
        let mut cut = self.0.len();
        for (i, t) in self.0.iter().enumerate() {
            if t.col().is_some_and(|c| c < col) {
                cut = i;
                break;
            }
        }
        while cut > 0 && matches!(self.0[cut - 1], Trivia::BlankLines(_)) {
            cut -= 1;
        }
        self.0.drain(..cut).collect()
    }

    /// Take the trailing part of the run that belongs *inside* a block collection that opens at
    /// column `col`.
    ///
    /// The mirror of [`Pending::take_from_col`]: there the run sits where collections *close* and
    /// is ordered deepest-first; here it sits in front of a collection that is *opening* and is
    /// ordered shallowest-first, so the cut goes after the last comment left of `col` and
    /// everything from there on is indented into the new collection (DESIGN §2.2 rule 2).
    pub fn take_to_col(&mut self, col: u32) -> Vec<Trivia> {
        let mut start = 0;
        for (i, t) in self.0.iter().enumerate() {
            if t.col().is_some_and(|c| c < col) {
                start = i + 1;
            }
        }
        // An end-of-line comment sits on the line of the indicator that introduces the collection.
        // Everything above that line is outside it, so only what follows the comment can be in.
        if let Some(i) = self.0.iter().rposition(|t| {
            matches!(
                t,
                Trivia::Comment {
                    own_line: false,
                    ..
                }
            )
        }) {
            start = start.max(i + 1);
        }
        // By rule 3 the blank lines leading up to whatever stays outside go outside with it.
        let mut end = self.0.len();
        while end > start && self.0[end - 1].col().is_none() {
            end -= 1;
        }
        self.0.drain(start..end).collect()
    }

    /// Take the run of blank lines directly above whatever comes next, if the pending run ends
    /// in one. Only that one run: a run before it is separated by a line with content on it.
    pub fn take_trailing_blanks(&mut self) -> Vec<Trivia> {
        match self.0.last() {
            Some(t) if t.col().is_none() => self.0.split_off(self.0.len() - 1),
            _ => Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Pending, Trivia};

    fn comment(col: u32) -> Trivia {
        Trivia::Comment {
            text: format!("# {col}"),
            own_line: true,
            col,
        }
    }

    #[test]
    fn take_from_col_cuts_at_the_first_shallower_comment() {
        let mut p = Pending::default();
        p.push(comment(4));
        p.push(comment(4));
        p.push(comment(2));
        p.push(comment(0));
        assert_eq!(p.take_from_col(4), vec![comment(4), comment(4)]);
        assert_eq!(p.take_from_col(2), vec![comment(2)]);
        assert_eq!(p.take_all(), vec![comment(0)]);
    }

    #[test]
    fn take_to_col_cuts_after_the_last_shallower_comment() {
        // `# 0` describes the entry; `# 2` and the blank line in front of it are indented
        // into the nested collection that is about to open at column 2.
        let mut p = Pending::default();
        p.push(comment(0));
        p.push(Trivia::BlankLines(1));
        p.push(comment(2));
        assert_eq!(p.take_to_col(2), vec![Trivia::BlankLines(1), comment(2)]);
        assert_eq!(p.take_all(), vec![comment(0)]);
    }

    #[test]
    fn take_to_col_takes_nothing_when_the_run_is_all_shallower() {
        let mut p = Pending::default();
        p.push(comment(0));
        p.push(comment(0));
        assert_eq!(p.take_to_col(2), vec![]);
        assert_eq!(p.take_all(), vec![comment(0), comment(0)]);
    }

    #[test]
    fn a_blank_run_goes_with_what_follows_it() {
        let mut p = Pending::default();
        p.push(comment(2));
        p.push(Trivia::BlankLines(1));
        p.push(comment(0));
        assert_eq!(p.take_from_col(2), vec![comment(2)]);
        assert_eq!(p.take_all(), vec![Trivia::BlankLines(1), comment(0)]);
    }

    #[test]
    fn a_trailing_blank_run_is_kept_for_the_next_slot() {
        let mut p = Pending::default();
        p.push(comment(2));
        p.push(Trivia::BlankLines(2));
        assert_eq!(p.take_from_col(2), vec![comment(2)]);
        assert_eq!(p.take_all(), vec![Trivia::BlankLines(2)]);
    }
}
