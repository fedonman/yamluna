//! The acceptance criterion: for every file in `tests/corpus/`, loading and dumping with
//! nothing mutated gives back the input byte for byte.
//!
//! This is the headline test of the project, and the corpus is its hand-written half: 41 files,
//! one concern each, chosen for the bytes that go missing in a round trip. A failure here is a
//! fact the source carried and the document model or the emitter did not.
//!
//! For reference, `ruamel.yaml==0.19.1` round-trips 3 of these 41 files byte-identically, as
//! scored by `.venv/bin/python tests/differential.py`.

use std::path::{Path, PathBuf};

use yamluna_core::{EmitOptions, emit, parse};

/// Corpus files that cannot round-trip, and why.
///
/// Empty: all 41 files come back byte-identical. An entry here would be a fact the source
/// carries and the document model does not, leaving the emitter to pick a spelling while the
/// file uses the other one. The test below fails if a listed entry starts passing, so a fix to
/// the model never leaves a stale excuse behind.
const KNOWN_FAILURES: &[(&str, &str)] = &[];

fn corpus_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus")
}

fn corpus_files() -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(corpus_dir())
        .expect("corpus directory")
        .map(|e| e.expect("dir entry").path())
        .filter(|p| p.extension().is_some_and(|e| e == "yaml"))
        .collect();
    files.sort();
    assert!(!files.is_empty(), "corpus is empty");
    files
}

fn known_failure(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();
    KNOWN_FAILURES.iter().any(|(f, _)| *f == name)
}

/// Load and dump with the default options, as a user of the library would.
fn round_trip(src: &str) -> Result<String, String> {
    let docs = parse(src).map_err(|e| e.to_string())?;
    emit(&docs, &EmitOptions::default()).map_err(|e| e.to_string())
}

/// The acceptance criterion. Nothing is mutated, so nothing may change.
#[test]
fn every_corpus_file_round_trips_byte_for_byte() {
    let mut passed = 0;
    for path in corpus_files() {
        if known_failure(&path) {
            continue;
        }
        let src = std::fs::read_to_string(&path).expect("read");
        let got = round_trip(&src).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        assert_eq!(
            got,
            src,
            "{} does not round-trip\n--- got ---\n{}\n--- want ---\n{}\n{}",
            path.display(),
            got.escape_debug(),
            src.escape_debug(),
            first_difference(&got, &src),
        );
        passed += 1;
    }
    assert_eq!(
        passed,
        corpus_files().len() - KNOWN_FAILURES.len(),
        "corpus size changed"
    );
}

/// A file listed as a known failure still fails, and for a reason that is still true.
#[test]
fn known_failures_still_fail() {
    for (name, why) in KNOWN_FAILURES {
        let path = corpus_dir().join(name);
        let src = std::fs::read_to_string(&path).expect("read");
        assert_ne!(
            round_trip(&src).ok().as_deref(),
            Some(src.as_str()),
            "{name} now round-trips: drop it from KNOWN_FAILURES ({why})"
        );
    }
}

/// Emitting twice gives the same bytes as emitting once: whatever the emitter writes, it can
/// read back and write again. This is the one property a known failure still has to satisfy.
#[test]
fn emitting_is_idempotent() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let Ok(once) = round_trip(&src) else {
            continue; // does not load; `tests/corpus.rs` owns that
        };
        let twice = round_trip(&once).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        assert_eq!(twice, once, "{}: dump is not a fixed point", path.display());
    }
}

/// Every comment of the source survives the round trip, in source order, even in a file that is
/// not byte-identical.
#[test]
fn no_comment_is_ever_lost() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let Ok(out) = round_trip(&src) else {
            continue;
        };
        assert_eq!(
            comments(&out),
            comments(&src),
            "{}: comments changed",
            path.display()
        );
    }
}

/// The `#` runs of a text, as a crude but independent check: any line whose first non-space
/// character is a `#`, plus the tail of any line that has one after other text.
fn comments(src: &str) -> Vec<String> {
    src.lines()
        .filter_map(|l| l.find('#').map(|i| l[i..].trim_end().to_owned()))
        .collect()
}

fn first_difference(got: &str, want: &str) -> String {
    let (mut line, mut col) = (1, 1);
    for (g, w) in got.chars().zip(want.chars()) {
        if g != w {
            return format!("first difference at line {line} column {col}: {g:?} vs {w:?}");
        }
        if g == '\n' {
            line += 1;
            col = 1;
        } else {
            col += 1;
        }
    }
    format!(
        "one is a prefix of the other ({} vs {} bytes)",
        got.len(),
        want.len()
    )
}

// ---------------------------------------------------------------------------------------------
// the layout path
// ---------------------------------------------------------------------------------------------

/// Every corpus file, rebuilt: each node stripped of its lexeme and its position, exactly as a
/// document the user constructed node by node arrives.
///
/// Nothing about the text is asserted, so the layout is free to differ. What has to hold is
/// that the text is still YAML and still says the same thing. This is the check that catches a
/// layout running two constructs into one line.
#[test]
fn a_rebuilt_document_says_the_same_thing() {
    for path in corpus_files() {
        let src = std::fs::read_to_string(&path).expect("read");
        let Ok(docs) = parse(&src) else {
            continue; // does not load; `tests/corpus.rs` owns that
        };
        let want: Vec<String> = docs.iter().map(shape).collect();
        let rebuilt: Vec<yamluna_core::Document> = docs.into_iter().map(strip).collect();
        let text = emit(&rebuilt, &EmitOptions::default())
            .unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        let back = parse(&text).unwrap_or_else(|e| {
            panic!(
                "{}: the laid-out text does not parse: {e}\n{text}",
                path.display()
            )
        });
        let got: Vec<String> = back.iter().map(shape).collect();
        assert_eq!(got, want, "{}: rebuilt differently\n{text}", path.display());
    }
}

/// A document as the user would hand one back: no lexemes, no positions.
fn strip(mut doc: yamluna_core::Document) -> yamluna_core::Document {
    for node in &mut doc.nodes {
        node.raw = None;
        node.pos = yamluna_core::Position::default();
    }
    doc
}

/// What a document says, with nothing about how it was written.
fn shape(doc: &yamluna_core::Document) -> String {
    fn node(doc: &yamluna_core::Document, id: u32, out: &mut String) {
        use std::fmt::Write;

        use yamluna_core::NodeKind;
        let n = doc.node(id);
        if let Some(a) = &n.anchor {
            write!(out, "&{a} ").expect("string");
        }
        if let Some(t) = &n.tag {
            write!(out, "<{}> ", t.resolved).expect("string");
        }
        match &n.kind {
            NodeKind::Scalar => {
                write!(out, "{:?}", n.value.as_deref().unwrap_or("")).expect("string");
            }
            NodeKind::Alias { anchor } => write!(out, "*{anchor}").expect("string"),
            NodeKind::Sequence { items } => {
                out.push('[');
                for i in items {
                    node(doc, *i, out);
                    out.push(',');
                }
                out.push(']');
            }
            NodeKind::Mapping { entries } => {
                out.push('{');
                for e in entries {
                    node(doc, e.key, out);
                    out.push(':');
                    node(doc, e.value, out);
                    out.push(',');
                }
                out.push('}');
            }
        }
    }
    let mut out = String::new();
    if let Some(root) = doc.root {
        node(doc, root, &mut out);
    }
    out
}
