//! Writing comments and blank lines back where their author put them (DESIGN §2.4, step 7).
//!
//! The four slots of [`Trivia4`](crate::Trivia4) are written by the node emitter at four
//! different moments; what happens to an individual trivium is the same everywhere and lives
//! here:
//!
//! * an own-line comment gets a line of its own, at its recorded column;
//! * an end-of-line comment is appended to the line the cursor is already on;
//! * a blank-line run becomes that many empty lines.
//!
//! A comment's column is absolute and is honoured even when the rest of the document has stopped
//! matching its source, because an author aligning a column of trailing comments is the one thing
//! a re-indent must not silently undo.

use crate::trivia::Trivia;

use super::layout::Writer;

/// Write a slot's trivia, in order.
///
/// An end-of-line comment inside a run stays on the line the cursor is on — that is the comment
/// that follows a `---` or a `?` indicator, which the loader puts in `leading` / `before` rather
/// than in an `eol` slot because the node it precedes had not been created yet.
pub(super) fn run(w: &mut Writer, items: &[Trivia]) {
    for t in items {
        match t {
            Trivia::Comment {
                text,
                own_line,
                col,
            } => {
                if *own_line {
                    w.fresh_line();
                } else {
                    w.space();
                }
                w.pad_to(*col);
                w.comment(text);
            }
            Trivia::BlankLines(n) => w.blank_lines(*n),
        }
    }
}

/// Write the end-of-line comment of a node, if it has one.
pub(super) fn eol(w: &mut Writer, t: Option<&Trivia>) {
    if let Some(t) = t {
        run(w, std::slice::from_ref(t));
    }
}

/// Split a run of trivia at its first end-of-line comment.
///
/// Everything before the split gets a line of its own and belongs above whatever introduces the
/// node; the rest belongs on the line the indicator is written on. That is the `? # comment`
/// case: the loader has nowhere to hang it but the key's `before` run, and it is first there.
pub(super) fn split_own_line(items: &[Trivia]) -> (&[Trivia], &[Trivia]) {
    let at = items
        .iter()
        .position(|t| {
            matches!(
                t,
                Trivia::Comment {
                    own_line: false,
                    ..
                }
            )
        })
        .unwrap_or(items.len());
    items.split_at(at)
}

/// Split a document's `leading` run at its end-of-line comment — the one that follows `---` on
/// the same line, which the loader guarantees is last (and is the only one there can be).
pub(super) fn split_marker_comment(items: &[Trivia]) -> (&[Trivia], Option<&Trivia>) {
    match items.split_last() {
        Some((
            last @ Trivia::Comment {
                own_line: false, ..
            },
            rest,
        )) => (rest, Some(last)),
        _ => (items, None),
    }
}

#[cfg(test)]
mod tests {
    use super::{Trivia, Writer, run, split_marker_comment};

    fn own(text: &str, col: u32) -> Trivia {
        Trivia::Comment {
            text: text.to_owned(),
            own_line: true,
            col,
        }
    }

    fn eol_comment(text: &str, col: u32) -> Trivia {
        Trivia::Comment {
            text: text.to_owned(),
            own_line: false,
            col,
        }
    }

    #[test]
    fn an_own_line_comment_keeps_its_column() {
        let mut w = Writer::new("\n");
        w.push("  - a");
        run(&mut w, &[own("# indented past the items", 6)]);
        assert_eq!(w.finish(), "  - a\n      # indented past the items");
    }

    #[test]
    fn an_end_of_line_comment_keeps_its_alignment() {
        let mut w = Writer::new("\n");
        w.push("scalar: value");
        run(&mut w, &[eol_comment("# after a plain scalar value", 25)]);
        assert_eq!(
            w.finish(),
            "scalar: value            # after a plain scalar value"
        );
    }

    /// An end-of-line comment whose column has already gone past still gets its separating space,
    /// which is the difference between a comment and a syntax error.
    #[test]
    fn an_end_of_line_comment_always_gets_one_space() {
        let mut w = Writer::new("\n");
        w.push("a_rather_long_key: 1");
        run(&mut w, &[eol_comment("# c", 4)]);
        assert_eq!(w.finish(), "a_rather_long_key: 1 # c");
    }

    #[test]
    fn a_blank_run_is_that_many_empty_lines() {
        let mut w = Writer::new("\n");
        w.push("first: 1");
        run(&mut w, &[Trivia::BlankLines(2), own("# after the gap", 0)]);
        assert_eq!(w.finish(), "first: 1\n\n\n# after the gap");
    }

    #[test]
    fn a_run_splits_at_its_end_of_line_comment() {
        let run = vec![eol_comment("# after the ? indicator", 2)];
        let (lines, rest) = super::split_own_line(&run);
        assert!(lines.is_empty());
        assert_eq!(rest, &run[..]);

        let run = vec![own("# above", 0), Trivia::BlankLines(1)];
        let (lines, rest) = super::split_own_line(&run);
        assert_eq!(lines, &run[..]);
        assert!(rest.is_empty());
    }

    #[test]
    fn the_marker_comment_is_split_off_the_leading_run() {
        let lead = vec![own("# header", 0), eol_comment("# after ---", 4)];
        let (lines, marker) = split_marker_comment(&lead);
        assert_eq!(lines, &lead[..1]);
        assert_eq!(marker, Some(&lead[1]));

        let plain = vec![own("# header", 0)];
        let (lines, marker) = split_marker_comment(&plain);
        assert_eq!(lines, &plain[..]);
        assert_eq!(marker, None);
    }
}
