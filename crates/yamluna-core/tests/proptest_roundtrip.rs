//! Fuzzing for the DESIGN §6.2 invariant: **for any YAML that parses, `parse → emit` is
//! byte-identical to the input.**
//!
//! Two document sources, because they fail in different ways:
//!
//! 1. `yaml-test-suite` — 351 files, ~400 cases, adversarial and real. It already ships with the
//!    scanner fork; nothing here was written for us, which is the point.
//! 2. A proptest generator over *documents* (not bytes): nested block and flow collections, every
//!    scalar style, anchors, aliases, tags, comments in every slot, blank lines, multi-document
//!    streams, unicode, CRLF. Random bytes almost never parse; random documents almost always do,
//!    and only a document that parses can test the invariant.
//!
//! Every known counterexample is in `KNOWN_GAPS` with a minimal repro, and
//! `known_gaps_are_still_gaps` fails when one starts passing, so a fix cannot leave a stale
//! excuse behind. `tests/README.md` carries the same list in prose.

use std::{fmt::Write as _, path::PathBuf};

use proptest::prelude::*;
use yamluna_core::{EmitOptions, emit, parse};

/// Load and dump with the default options, as a user of the library would.
fn round_trip(src: &str) -> Result<String, String> {
    let docs = parse(src).map_err(|e| e.to_string())?;
    emit(&docs, &EmitOptions::default()).map_err(|e| e.to_string())
}

// =================================================================================================
// 1. yaml-test-suite
// =================================================================================================

/// A `yaml-test-suite` case: the id it is reported under and the source it holds.
struct SuiteCase {
    id: String,
    yaml: String,
}

fn suite_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../yamluna-scanner/tests/yaml-test-suite/src")
}

/// The suite writes white space it wants you to see with visible stand-ins. Same table as
/// `crates/yamluna-scanner/tests/yaml-test-suite.rs`, so both harnesses see the same bytes.
fn visual_to_raw(yaml: &str) -> String {
    let mut yaml = yaml.to_owned();
    for (pat, replacement) in [
        ("␣", " "),
        ("»", "\t"),
        ("—", ""), // tab line continuation ——»
        ("←", "\r"),
        ("⇔", "\u{feff}"),
        ("↵", ""), // trailing newline marker
        ("∎\n", ""),
    ] {
        yaml = yaml.replace(pat, replacement);
    }
    yaml
}

/// Every case in the suite that is *expected to parse*: `fail: true` and `skip` cases are dropped,
/// and fields other than `fail` are inherited from the previous case in the file, exactly as the
/// scanner's own harness reads them.
fn suite_cases() -> Vec<SuiteCase> {
    use saphyr::{LoadableYamlNode, Mapping, Scalar, Yaml};

    let mut out = Vec::new();
    let mut paths: Vec<PathBuf> = std::fs::read_dir(suite_dir())
        .expect("yaml-test-suite/src")
        .map(|e| e.expect("dir entry").path())
        .filter(|p| p.extension().is_some_and(|e| e == "yaml"))
        .collect();
    paths.sort();
    assert!(!paths.is_empty(), "yaml-test-suite is empty");

    for path in paths {
        let stem = path.file_stem().unwrap().to_string_lossy().to_string();
        let text = std::fs::read_to_string(&path).expect("read");
        let docs = Yaml::load_from_str(&text).unwrap_or_else(|e| panic!("{stem}: {e}"));
        let cases = docs[0].as_vec().expect("a list of cases");

        let mut current = Mapping::new();
        for (idx, case) in cases.iter().enumerate() {
            let id = if cases.len() > 1 {
                format!("{stem}-{idx:02}")
            } else {
                stem.clone()
            };
            current.remove(&Yaml::Value(Scalar::String("fail".into())));
            for (k, v) in case.as_mapping().expect("a case mapping").clone() {
                current.insert(k, v);
            }
            let merged = Yaml::Mapping(current.clone());
            if merged.contains_mapping_key("skip") {
                continue;
            }
            let fails = merged
                .as_mapping_get("fail")
                .is_some_and(|f| f.as_bool() == Some(true));
            if fails {
                continue;
            }
            out.push(SuiteCase {
                id,
                yaml: visual_to_raw(merged["yaml"].as_str().expect("a `yaml` field")),
            });
        }
    }
    out
}

/// Suite cases that do not round-trip byte-for-byte, and why.
///
/// Each entry is a real defect, minimised in `tests/README.md` under "Known gaps". The test below
/// fails if one starts passing, so fixing a gap forces the entry out.
const KNOWN_GAPS: &[(&str, &str)] = &[];

/// **The headline number.** Every suite case that the loader accepts must re-emit byte-identically.
#[test]
fn yaml_test_suite_round_trips_byte_for_byte() {
    let cases = suite_cases();
    let (mut parsed, mut identical, mut unparsed) = (0, 0, 0);
    let mut failures = String::new();
    let mut unexpected_pass = Vec::new();

    for case in &cases {
        let gap = KNOWN_GAPS.iter().find(|(id, _)| *id == case.id);
        let Ok(got) = round_trip(&case.yaml) else {
            unparsed += 1;
            continue;
        };
        parsed += 1;
        match (got == case.yaml, gap) {
            (true, None) => identical += 1,
            (true, Some(_)) => unexpected_pass.push(case.id.as_str()),
            (false, Some(_)) => {}
            (false, None) => {
                identical += 0;
                let _ = writeln!(
                    failures,
                    "  {}\n    in:  {}\n    out: {}",
                    case.id,
                    case.yaml.escape_debug(),
                    got.escape_debug(),
                );
            }
        }
    }

    println!(
        "yaml-test-suite: {identical}/{parsed} parsed cases round-trip byte-identically \
         ({unparsed} of {} did not parse, {} known gaps)",
        cases.len(),
        KNOWN_GAPS.len(),
    );
    assert!(
        unexpected_pass.is_empty(),
        "now round-trip — drop from KNOWN_GAPS: {unexpected_pass:?}"
    );
    assert!(
        failures.is_empty(),
        "{} suite cases do not round-trip:\n{failures}",
        failures.lines().filter(|l| !l.starts_with("    ")).count(),
    );
}

/// A case that does not round-trip must at least be a fixed point of the emitter: dumping what we
/// dumped changes nothing. A gap that fails this one is not a spelling difference, it is data loss.
#[test]
fn suite_emitting_is_idempotent() {
    for case in suite_cases() {
        let Ok(once) = round_trip(&case.yaml) else {
            continue;
        };
        let twice = round_trip(&once)
            .unwrap_or_else(|e| panic!("{}: re-parsing our own output failed: {e}", case.id));
        assert_eq!(twice, once, "{}: dump is not a fixed point", case.id);
    }
}

// =================================================================================================
// 2. the document generator
// =================================================================================================
//
// A generator over random *bytes* is useless here: essentially none of them parse, and only a
// document that parses can test the invariant. So this generates a small YAML *tree* and renders
// it, which gives documents that parse by construction and a tree proptest can shrink.
//
// The renderer deliberately stays inside the styles the emitter already handles, so a red run
// means a *new* bug. Everything it avoids is listed in `KNOWN_GAPS` / `tests/README.md` and is
// already covered by the suite above: inter-token padding, tabs, CRLF, `?`-explicit keys,
// empty block scalars, `+`-chomping, multi-line flow collections, comments inside flow, and
// blank lines before the first token of a stream.

/// A comment line or a run of blank lines, in a slot where the emitter accepts one.
#[derive(Debug, Clone)]
enum Triv {
    Comment(String),
    Blank(u8),
}

#[derive(Debug, Clone)]
enum Val {
    /// Plain scalar; the string is already plain-safe.
    Plain(String),
    /// `'` or `"` quoted; the string is already escaped for that quote.
    Quoted(char, String),
    /// `|` or `>` with a `` or `-` chomping indicator, and one or more body lines.
    Block(char, bool, Vec<String>),
    /// `*name`, resolved at render time against the anchors already written.
    Alias,
    Seq(bool, Vec<Node>),
    Map(bool, Vec<(String, Node)>),
}

#[derive(Debug, Clone)]
struct Node {
    anchor: bool,
    tag: Option<u8>,
    /// Own-line trivia before this node. Only rendered where the node starts its own line.
    before: Vec<Triv>,
    eol: Option<String>,
    val: Val,
}

#[derive(Debug, Clone)]
struct Doc {
    directives: bool,
    explicit_start: bool,
    explicit_end: bool,
    root: Node,
}

const TAGS: &[&str] = &["!!str", "!!map", "!!seq", "!foo", "!<tag:example.com,2000:bar>"];

// -------------------------------------------------------------------------------------------
// strategies
// -------------------------------------------------------------------------------------------

/// Plain-safe text: no indicator can start it, no `: ` or ` #` can appear inside it.
fn word() -> impl Strategy<Value = String> {
    prop_oneof![
        8 => "[a-z][a-z0-9_]{0,4}",
        1 => "[0-9]{1,3}",
        1 => Just("kéy".to_owned()),
        1 => Just("キー".to_owned()),
        1 => Just("Ωμέγα".to_owned()),
    ]
}

fn comment() -> impl Strategy<Value = String> {
    ("# ?", "[a-z0-9]{0,6}").prop_map(|(hash, text)| format!("{hash}{text}"))
}

fn trivia() -> impl Strategy<Value = Triv> {
    prop_oneof![
        3 => comment().prop_map(Triv::Comment),
        1 => (1u8..3).prop_map(Triv::Blank),
    ]
}

fn scalar_val() -> impl Strategy<Value = Val> {
    prop_oneof![
        6 => word().prop_map(Val::Plain),
        2 => word().prop_map(|w| Val::Quoted('\'', w)),
        2 => word().prop_map(|w| Val::Quoted('"', w)),
        1 => (prop_oneof![Just('|'), Just('>')], any::<bool>(), prop::collection::vec(word(), 1..3))
             .prop_map(|(h, strip, lines)| Val::Block(h, strip, lines)),
        1 => Just(Val::Alias),
    ]
}

fn node(val: impl Strategy<Value = Val>) -> impl Strategy<Value = Node> {
    (
        val,
        any::<bool>(),
        prop::option::of(0u8..TAGS.len() as u8),
        prop::collection::vec(trivia(), 0..2),
        prop::option::of(comment()),
    )
        .prop_map(|(val, anchor, tag, before, eol)| Node {
            anchor,
            tag,
            before,
            eol,
            val,
        })
}

fn tree() -> impl Strategy<Value = Node> {
    node(scalar_val()).prop_recursive(4, 32, 4, |inner| {
        let items = prop::collection::vec(inner.clone(), 0..4);
        let entries = prop::collection::vec((word(), inner), 0..4);
        node(prop_oneof![
            (any::<bool>(), items).prop_map(|(flow, i)| Val::Seq(flow, i)),
            (any::<bool>(), entries).prop_map(|(flow, e)| Val::Map(flow, e)),
        ])
    })
}

fn document() -> impl Strategy<Value = Doc> {
    (any::<bool>(), any::<bool>(), any::<bool>(), tree()).prop_map(
        |(directives, explicit_start, explicit_end, root)| Doc {
            directives,
            explicit_start,
            explicit_end,
            root,
        },
    )
}

// -------------------------------------------------------------------------------------------
// the renderer
// -------------------------------------------------------------------------------------------

#[derive(Default)]
struct Render {
    out: String,
    anchors: Vec<String>,
    next: usize,
}

impl Render {
    /// `&a1 !!str ` — the part that precedes a node's own text. Empty for an alias, which can
    /// carry neither.
    fn head(&mut self, n: &Node) -> String {
        if matches!(n.val, Val::Alias) {
            return String::new();
        }
        let mut s = String::new();
        if n.anchor {
            self.next += 1;
            let name = format!("a{}", self.next);
            s.push('&');
            s.push_str(&name);
            s.push(' ');
            self.anchors.push(name);
        }
        if let Some(t) = n.tag {
            // Gap `verbatim-tag-block-scalar`: `!<uri>` on a `|` scalar re-emits it as `>`.
            let verbatim = TAGS[t as usize].starts_with("!<");
            if !(verbatim && matches!(n.val, Val::Block(..))) {
                s.push_str(TAGS[t as usize]);
                s.push(' ');
            }
        }
        s
    }

    /// An alias to an anchor already written, or `None` when none exists yet.
    fn alias(&self) -> Option<String> {
        self.anchors.last().map(|a| format!("*{a}"))
    }

    fn pad(&mut self, indent: usize) {
        for _ in 0..indent {
            self.out.push(' ');
        }
    }

    fn trivia(&mut self, before: &[Triv], indent: usize) {
        for t in before {
            match t {
                Triv::Comment(c) => {
                    self.pad(indent);
                    self.out.push_str(c);
                    self.out.push('\n');
                }
                Triv::Blank(n) => {
                    for _ in 0..*n {
                        self.out.push('\n');
                    }
                }
            }
        }
    }

    fn eol(&mut self, n: &Node) {
        if let Some(c) = &n.eol {
            self.out.push(' ');
            self.out.push_str(c);
        }
    }

    /// One line of flow text for a node: `&a1 !!str [x, {k: v}]`.
    fn flow(&mut self, n: &Node) -> String {
        let head = self.head(n);
        let body = self.flow_body(n);
        format!("{head}{body}")
    }

    /// The flow text of a node without its anchor or tag. Block-only styles degrade, because
    /// block layout cannot appear inside a flow collection.
    fn flow_body(&mut self, n: &Node) -> String {
        match &n.val {
            Val::Plain(s) => s.clone(),
            Val::Quoted(q, s) => format!("{q}{s}{q}"),
            // A block scalar has no flow spelling; a plain one says the same thing.
            Val::Block(_, _, lines) => lines[0].clone(),
            Val::Alias => self.alias().unwrap_or_else(|| "x".to_owned()),
            Val::Seq(_, items) => {
                let parts: Vec<String> = items.iter().map(|i| self.flow(i)).collect();
                format!("[{}]", parts.join(", "))
            }
            Val::Map(_, entries) => {
                let parts: Vec<String> = entries
                    .iter()
                    .map(|(k, v)| format!("{k}: {}", self.flow(v)))
                    .collect();
                format!("{{{}}}", parts.join(", "))
            }
        }
    }

    /// Whether this node needs lines of its own below the `:` or `-` that introduces it.
    fn is_block(n: &Node) -> bool {
        match &n.val {
            Val::Seq(false, items) => !items.is_empty(),
            Val::Map(false, entries) => !entries.is_empty(),
            Val::Block(..) => true,
            _ => false,
        }
    }

    /// Everything after the `:` or `-` that introduces `n`, including the line break, with `n`'s
    /// children laid out at `child`.
    fn after_marker(&mut self, n: &Node, child: usize, map_value: bool) {
        // Gap `block-scalar-indent`: below the top level a block scalar swallows the *next*
        // line's indentation, so anything after it is mis-indented. Written flat instead; the
        // gap keeps its own minimal repro in `GAPS`.
        if matches!(n.val, Val::Block(..)) && child > 2 {
            self.out.push(' ');
            let text = self.flow(n);
            self.out.push_str(&text);
            self.eol(n);
            self.out.push('\n');
            return;
        }
        if !Self::is_block(n) {
            self.out.push(' ');
            let text = self.flow(n);
            self.out.push_str(&text);
            self.eol(n);
            self.out.push('\n');
            return;
        }
        let head = self.head(n);
        if !head.is_empty() {
            self.out.push(' ');
            self.out.push_str(head.trim_end());
        }
        match &n.val {
            Val::Block(h, strip, lines) => {
                self.out.push(' ');
                self.out.push(*h);
                if *strip {
                    self.out.push('-');
                }
                // Gap `block-header-comment`: an end-of-line comment on a block-scalar header
                // is re-emitted before the `-` that introduces the item, or swallows the header
                // outright at the document root. As a mapping value it is written correctly.
                if map_value {
                    self.eol(n);
                }
                self.out.push('\n');
                for l in lines {
                    self.pad(child);
                    self.out.push_str(l);
                    self.out.push('\n');
                }
            }
            _ => {
                self.eol(n);
                self.out.push('\n');
                self.collection(n, child);
            }
        }
    }

    /// The lines of a non-empty block collection, each starting at `indent`.
    fn collection(&mut self, n: &Node, indent: usize) {
        match &n.val {
            Val::Seq(_, items) => {
                for item in items {
                    self.trivia(&item.before, indent);
                    self.pad(indent);
                    self.out.push('-');
                    self.after_marker(item, indent + 2, false);
                }
            }
            Val::Map(_, entries) => {
                for (key, value) in entries {
                    self.trivia(&value.before, indent);
                    self.pad(indent);
                    self.out.push_str(key);
                    self.out.push(':');
                    self.after_marker(value, indent + 2, true);
                }
            }
            _ => unreachable!("collection() on a scalar"),
        }
    }

    fn document(&mut self, doc: &Doc, forced_start: bool) {
        if doc.directives {
            self.out.push_str("%YAML 1.2\n");
        }
        let block = Self::is_block(&doc.root);
        let head = self.head(&doc.root);
        // Gap `root-block-scalar-trivia`: a comment above a document root that is a block scalar
        // is glued onto the header line, swallowing the `|`/`>`.
        let root_trivia: Vec<Triv> = if matches!(doc.root.val, Val::Block(..)) {
            Vec::new()
        } else {
            doc.root.before.clone()
        };
        // Gap `anchor-own-line` / `tag-own-line`: `&a` or `!!str` on a line of its own above a
        // block collection is rewritten by the emitter. On the `---` line it is not, so a root
        // that carries one gets an explicit start.
        let explicit = doc.directives || doc.explicit_start || forced_start || (block && !head.is_empty());

        if explicit {
            self.out.push_str("---");
            if !head.is_empty() {
                self.out.push(' ');
                self.out.push_str(head.trim_end());
            }
            if matches!(doc.root.val, Val::Block(..)) {
                // A block scalar keeps its header on the `---` line: `--- !!str |`.
                self.out.push(' ');
                self.body(&doc.root, 0);
            } else if block {
                // Gap `marker-head-comment`: an anchor or tag on the `---` line is re-emitted
                // *after* an end-of-line comment there, i.e. inside it.
                if head.is_empty() {
                    self.eol(&doc.root);
                }
                self.out.push('\n');
                self.trivia(&root_trivia, 0);
                self.body(&doc.root, 0);
            } else {
                self.out.push(' ');
                let text = self.flow_body(&doc.root);
                self.out.push_str(&text);
                self.eol(&doc.root);
                self.out.push('\n');
            }
        } else {
            self.trivia(&root_trivia, 0);
            self.out.push_str(&head);
            if block {
                self.body(&doc.root, 0);
            } else {
                let text = self.flow_body(&doc.root);
                self.out.push_str(&text);
                self.eol(&doc.root);
                self.out.push('\n');
            }
        }
        if doc.explicit_end {
            self.out.push_str("...\n");
        }
    }

    /// A block-shaped node's own lines, at `indent`, with its anchor and tag already written.
    fn body(&mut self, n: &Node, indent: usize) {
        if let Val::Block(h, strip, lines) = &n.val {
            self.out.push(*h);
            if *strip {
                self.out.push('-');
            }
            // Gap `block-header-comment`, at the document root.
            self.out.push('\n');
            for l in lines {
                self.pad(indent + 2);
                self.out.push_str(l);
                self.out.push('\n');
            }
        } else {
            self.collection(n, indent);
        }
    }
}

fn render(docs: &[Doc]) -> String {
    let mut r = Render::default();
    for (i, doc) in docs.iter().enumerate() {
        r.document(doc, i > 0);
    }
    r.out
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 2048, max_shrink_iters: 8192, ..ProptestConfig::default() })]

    /// The invariant, over generated documents: what parses must re-emit byte-for-byte.
    #[test]
    fn generated_documents_round_trip(docs in prop::collection::vec(document(), 1..3)) {
        let src = render(&docs);
        // A generated document that does not parse is a bug in the *generator*, not the library,
        // and saying so here is what keeps a broken generator from passing silently.
        let got = round_trip(&src)
            .map_err(|e| TestCaseError::fail(format!("generated source does not parse: {e}\n{src:?}")))?;
        prop_assert_eq!(&got, &src, "\n--- got ---\n{}\n--- want ---\n{}", got.escape_debug(), src.escape_debug());
    }

    /// Emitting is a fixed point even where the round trip is not byte-exact.
    #[test]
    fn generated_documents_emit_idempotently(docs in prop::collection::vec(document(), 1..3)) {
        let src = render(&docs);
        if let Ok(once) = round_trip(&src) {
            let twice = round_trip(&once).map_err(|e| TestCaseError::fail(e.to_string()))?;
            prop_assert_eq!(twice, once);
        }
    }
}
