//! The structural invariant of DESIGN §2.2, run over `tests/corpus/*.yaml`.
//!
//! There is no emitter yet, so `load → dump` cannot be checked. What *can* be checked is the
//! property every attachment bug breaks: each comment of the source appears exactly once in the
//! tree, in source order, and every blank-line run is a real run. Comment texts come from a second,
//! independent pass over the raw event stream, so the two sides cannot agree by construction.

use std::path::{Path, PathBuf};

use yamluna_core::{Document, Trivia};
use yamluna_scanner::{Event, Parser, ScalarStyle};

fn corpus_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus")
}

/// Corpus files the *scanner* cannot load yet. Each entry is a defect in the fork, not in the
/// loader, and is asserted to still fail exactly this way so the entry disappears when it is fixed.
const KNOWN_SCANNER_DEFECTS: &[(&str, &str)] = &[];

fn known_defect(path: &Path) -> Option<&'static str> {
    let name = path.file_name()?.to_str()?;
    KNOWN_SCANNER_DEFECTS
        .iter()
        .find(|(f, _)| *f == name)
        .map(|(_, msg)| *msg)
}

fn corpus_files() -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(corpus_dir())
        .expect("corpus directory")
        .map(|e| e.expect("dir entry").path())
        .filter(|p| p.extension().is_some_and(|e| e == "yaml"))
        .filter(|p| known_defect(p).is_none())
        .collect();
    files.sort();
    assert!(!files.is_empty(), "corpus is empty");
    files
}

/// Every comment in the source, in source order, straight off the event stream.
fn comments_in_source(src: &str) -> Vec<String> {
    let src = src.strip_prefix('\u{feff}').unwrap_or(src);
    let mut out = Vec::new();
    let mut parser = Parser::new_from_str(src).keep_comments(true);
    while let Some(ev) = parser.next_event() {
        match ev.expect("corpus file parses") {
            (Event::Comment(text), _) => out.push(text.into_owned()),
            (Event::StreamEnd, _) => break,
            _ => {}
        }
    }
    out
}

/// Every comment in the tree, in the order an emitter would write them.
fn comments_in_tree(docs: &[Document]) -> Vec<String> {
    docs.iter()
        .flat_map(|d| d.trivia_in_order())
        .filter_map(|t| t.text().map(str::to_owned))
        .collect()
}

fn blank_runs(docs: &[Document]) -> Vec<u32> {
    docs.iter()
        .flat_map(|d| d.trivia_in_order())
        .filter_map(|t| match t {
            Trivia::BlankLines(n) => Some(*n),
            Trivia::Comment { .. } => None,
        })
        .collect()
}

#[test]
fn every_corpus_file_loads() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let docs = yamluna_core::parse(&src).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        assert!(!docs.is_empty(), "{}: no documents", path.display());
    }
}

#[test]
fn every_comment_appears_exactly_once_in_source_order() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let docs = yamluna_core::parse(&src).expect("loads");
        let expected = comments_in_source(&src);
        let got = comments_in_tree(&docs);
        assert_eq!(
            got,
            expected,
            "{}: comments in the tree do not match the source",
            path.display()
        );
        assert!(!expected.is_empty(), "{}: no comments?", path.display());
    }
}

#[test]
fn blank_line_runs_are_never_empty() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let docs = yamluna_core::parse(&src).expect("loads");
        for n in blank_runs(&docs) {
            assert!(n > 0, "{}: BlankLines(0)", path.display());
        }
    }
}

/// Every blank line of the source that is not inside a scalar is accounted for, exactly once.
///
/// Which lines are inside a scalar comes from an independent pass over the raw event stream, so
/// this does not just restate what the loader believes.
#[test]
fn blank_line_totals_match_the_source() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let docs = yamluna_core::parse(&src).expect("loads");
        let in_source = free_blank_lines(&src);
        let in_tree: u32 = blank_runs(&docs).iter().sum();
        assert_eq!(
            in_tree as usize,
            in_source,
            "{}: blank lines in tree vs source",
            path.display()
        );
    }
}

/// Blank source lines that no scalar covers, i.e. the ones that are trivia.
fn free_blank_lines(src: &str) -> usize {
    let src = src.strip_prefix('\u{feff}').unwrap_or(src);
    let lines: Vec<&str> = src.lines().collect();
    let mut inside = vec![false; src.lines().count() + 2];
    let mut parser = Parser::new_from_str(src).keep_comments(true);
    while let Some(ev) = parser.next_event() {
        match ev.expect("corpus file parses") {
            (Event::Scalar(_, style, ..), span) => {
                // A block scalar's span starts at its *body*, but its lexeme starts at the
                // `|`/`>` header, and every blank line in between is content — `|+` keeps it,
                // and the cooked value begins with it. Walk back over those so they are not
                // counted as trivia the tree failed to record.
                let mut start = span.start.line();
                if matches!(style, ScalarStyle::Literal | ScalarStyle::Folded) {
                    while start > 1 && lines[start - 2].trim().is_empty() {
                        start -= 1;
                    }
                }
                for l in start..=span.end.line() {
                    if let Some(slot) = inside.get_mut(l) {
                        *slot = true;
                    }
                }
            }
            (Event::StreamEnd, _) => break,
            _ => {}
        }
    }
    src.lines()
        .enumerate()
        .filter(|(i, l)| l.trim().is_empty() && !inside[i + 1])
        .count()
}

/// Comments and blank lines only ever land in a slot that belongs to a node of the same document.
#[test]
fn every_node_is_reachable_from_the_root() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let docs = yamluna_core::parse(&src).expect("loads");
        for doc in &docs {
            let mut seen = vec![false; doc.nodes.len()];
            let mut stack: Vec<u32> = doc.root.into_iter().collect();
            while let Some(id) = stack.pop() {
                assert!(
                    !seen[id as usize],
                    "{}: node {id} visited twice",
                    path.display()
                );
                seen[id as usize] = true;
                stack.extend(doc.node(id).children());
            }
            assert!(
                seen.iter().all(|s| *s),
                "{}: orphan nodes in the arena",
                path.display()
            );
        }
    }
}

/// The scanner defects above are still exactly that, and nothing else.
#[test]
fn known_scanner_defects_still_fail_the_same_way() {
    for (file, message) in KNOWN_SCANNER_DEFECTS {
        let path = corpus_dir().join(file);
        let src = std::fs::read_to_string(&path).expect("read");
        match yamluna_core::parse(&src) {
            Ok(_) => panic!("{file}: now loads — drop it from KNOWN_SCANNER_DEFECTS"),
            Err(e) => assert_eq!(&e.message, message, "{file}: different failure"),
        }
    }
}

/// The shape `Document::leading` and `Document::trailing` promise the emitter: an end-of-line
/// comment in `leading` sits on the `---` line and is therefore last; one in `trailing` sits on the
/// `...` line and is therefore first.
#[test]
fn document_trivia_keep_their_marker_comments_at_the_ends() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        for doc in yamluna_core::parse(&src).expect("loads") {
            let eol = |t: &Trivia| {
                matches!(
                    t,
                    Trivia::Comment {
                        own_line: false,
                        ..
                    }
                )
            };
            let leading: Vec<bool> = doc.leading.iter().map(&eol).collect();
            assert!(
                leading.iter().filter(|e| **e).count() <= 1
                    && leading
                        .iter()
                        .position(|e| *e)
                        .is_none_or(|i| i + 1 == leading.len()),
                "{}: leading = {:?}",
                path.display(),
                doc.leading
            );
            let trailing: Vec<bool> = doc.trailing.iter().map(&eol).collect();
            assert!(
                trailing.iter().filter(|e| **e).count() <= 1
                    && trailing.iter().position(|e| *e).is_none_or(|i| i == 0),
                "{}: trailing = {:?}",
                path.display(),
                doc.trailing
            );
        }
    }
}
