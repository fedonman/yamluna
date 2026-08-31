//! The loader guarantees of DESIGN §2.3, and the model invariants of §2.

use yamluna_core::{ErrorKind, NodeKind, ScalarStyle, Style};

fn one(src: &str) -> yamluna_core::Document {
    let mut d = yamluna_core::parse(src).unwrap_or_else(|e| panic!("{src:?}: {e}"));
    assert_eq!(d.len(), 1);
    d.remove(0)
}

fn entries(d: &yamluna_core::Document) -> Vec<yamluna_core::Entry> {
    let NodeKind::Mapping { entries } = &d.node(d.root.unwrap()).kind else {
        panic!("not a mapping")
    };
    entries.clone()
}

#[test]
fn the_bom_is_stripped_and_recorded() {
    let d = one("\u{feff}key: café\n");
    assert!(d.bom, "the emitter has to write it back");
    let e = entries(&d);
    assert_eq!(
        d.node(e[0].key).pos.col,
        0,
        "positions are relative to the stripped source"
    );
    assert_eq!(d.node(e[0].value).raw.as_deref(), Some("café"));
}

/// Every marker is a char offset (DESIGN §1.5); without the char→byte table these slices are
/// garbage or a panic on a char boundary.
#[test]
fn raw_lexemes_survive_astral_and_combining_characters() {
    let d = one("emoji: 🌍 🚀\ncombining: e\u{301}\u{302}\n🔑: an emoji key\nlast: end\n");
    let e = entries(&d);
    assert_eq!(d.node(e[0].value).raw.as_deref(), Some("🌍 🚀"));
    assert_eq!(d.node(e[1].value).raw.as_deref(), Some("e\u{301}\u{302}"));
    assert_eq!(d.node(e[2].key).raw.as_deref(), Some("🔑"));
    assert_eq!(d.node(e[3].value).raw.as_deref(), Some("end"));
    // Columns are in characters, so the emoji key is one column wide.
    assert_eq!(d.node(e[2].value).pos.col, 3);
}

#[test]
fn duplicate_keys_are_recorded_never_last_wins() {
    let d = one("a: 1\nb: 2\na: 3\n");
    assert_eq!(entries(&d).len(), 3, "both `a` entries are kept");
    assert_eq!(d.duplicate_keys.len(), 1);
    let dup = &d.duplicate_keys[0];
    assert_eq!(dup.key, "a");
    assert_eq!((dup.first.line, dup.again.line), (0, 2));
}

#[test]
fn keys_that_differ_only_in_quoting_are_the_same_key() {
    let d = one("\"a\": 1\na: 2\n");
    assert_eq!(d.duplicate_keys.len(), 1);
    assert_eq!(
        d.node(entries(&d)[0].key).style,
        Style::Scalar(ScalarStyle::DoubleQuoted)
    );
}

#[test]
fn duplicate_keys_of_sibling_mappings_are_not_duplicates() {
    let d = one("- dup: 1\n- dup: 2\n");
    assert!(d.duplicate_keys.is_empty());
}

#[test]
fn merge_keys_are_recorded_not_expanded() {
    let d = one("base: &b\n  a: 1\nuse:\n  <<: *b\n  b: 2\n");
    let e = entries(&d);
    let NodeKind::Mapping { entries: inner } = &d.node(e[1].value).kind else {
        panic!()
    };
    assert!(inner[0].merge, "`<<` is an entry, not an expansion");
    assert!(!inner[1].merge);
    assert_eq!(inner.len(), 2, "the merged mapping is not spliced in");
    assert_eq!(
        d.node(inner[0].value).kind,
        NodeKind::Alias { anchor: "b".into() }
    );
}

#[test]
fn a_quoted_merge_key_is_not_a_merge() {
    let d = one("\"<<\": 1\n");
    assert!(!entries(&d)[0].merge);
}

#[test]
fn explicit_keys_are_marked() {
    let d = one("? [a, b]\n: v\nsimple: w\n");
    let e = entries(&d);
    assert!(e[0].explicit);
    assert!(!e[1].explicit);
}

#[test]
fn scalar_styles_and_raw_lexemes() {
    let src = "plain: a b\nsingle: 'a''b'\ndouble: \"a\\tb\"\nliteral: |-\n  x\nfolded: >-\n  y\n";
    let d = one(src);
    let e = entries(&d);
    let expect = [
        (ScalarStyle::Plain, "a b", "a b"),
        (ScalarStyle::SingleQuoted, "a'b", "'a''b'"),
        (ScalarStyle::DoubleQuoted, "a\tb", "\"a\\tb\""),
        (ScalarStyle::Literal, "x", "|-\n  x"),
        (ScalarStyle::Folded, "y", ">-\n  y"),
    ];
    for (i, (style, value, raw)) in expect.into_iter().enumerate() {
        let n = d.node(e[i].value);
        assert_eq!(n.style, Style::Scalar(style), "entry {i}");
        assert_eq!(n.value.as_deref(), Some(value), "entry {i} value");
        assert_eq!(n.raw.as_deref(), Some(raw), "entry {i} raw");
    }
}

#[test]
fn collection_styles_are_carried_through() {
    let d = one("block:\n  - a\nflow: [a]\nimplicit_flow: [a: 1]\n");
    let e = entries(&d);
    assert_eq!(d.node(e[0].value).style, Style::Block);
    assert_eq!(d.node(e[1].value).style, Style::Flow);
    let NodeKind::Sequence { items } = &d.node(e[2].value).kind else {
        panic!()
    };
    assert_eq!(
        d.node(items[0]).style,
        Style::Flow,
        "an implicit flow mapping cannot be recovered from spans"
    );
}

#[test]
fn anchors_keep_their_names() {
    let d = one("a: &name 1\nb: *name\n");
    let e = entries(&d);
    assert_eq!(d.node(e[0].value).anchor.as_deref(), Some("name"));
    assert_eq!(
        d.node(e[1].value).kind,
        NodeKind::Alias {
            anchor: "name".into()
        }
    );
}

#[test]
fn directives_and_tags_are_recoverable_as_written() {
    let src = "%YAML 1.2\n%TAG ! tag:libx/\n%TAG !g! tag:libx.gates/\n---\na: !Circuit {}\nb: !g!Gate {}\nc: !!str x\nd: !<tag:libx/Circuit> {}\ne: !Local {}\n";
    let d = one(src);
    assert_eq!(d.version, Some((1, 2)));
    assert_eq!(d.tag_directives.len(), 2);
    assert_eq!(d.tag_directives[0].handle, "!");
    assert_eq!(d.tag_directives[1].prefix, "tag:libx.gates/");
    let e = entries(&d);
    let tag = |i: usize| {
        let t = d.node(e[i].value).tag.clone().expect("a tag");
        (t.handle, t.suffix, t.resolved)
    };
    assert_eq!(
        tag(0),
        ("!".into(), "Circuit".into(), "tag:libx/Circuit".into())
    );
    assert_eq!(
        tag(1),
        ("!g!".into(), "Gate".into(), "tag:libx.gates/Gate".into())
    );
    assert_eq!(
        tag(2),
        ("!!".into(), "str".into(), "tag:yaml.org,2002:str".into())
    );
    // A verbatim tag has no handle: it is written `!<uri>`.
    assert_eq!(
        tag(3),
        (
            String::new(),
            "tag:libx/Circuit".into(),
            "tag:libx/Circuit".into()
        )
    );
    // `!` is remapped by the directive, so a "local" tag resolves through it too.
    assert_eq!(
        tag(4),
        ("!".into(), "Local".into(), "tag:libx/Local".into())
    );
}

#[test]
fn a_local_tag_with_no_directive_stays_local() {
    let d = one("a: !Thing {}\n");
    let t = d.node(entries(&d)[0].value).tag.clone().unwrap();
    assert_eq!(
        (t.handle.as_str(), t.suffix.as_str(), t.resolved.as_str()),
        ("!", "Thing", "!Thing")
    );
}

#[test]
fn document_markers_and_the_final_line_break() {
    let d = yamluna_core::parse("a: 1\n---\nb: 2\n...\n").expect("parses");
    assert_eq!(d.len(), 2);
    assert!(!d[0].explicit_start && !d[0].explicit_end);
    assert!(d[1].explicit_start && d[1].explicit_end);
    assert!(d[1].final_line_break);
    let d = yamluna_core::parse("a: 1").expect("parses");
    assert!(
        !d[0].final_line_break,
        "a dump must not add a newline the input did not have"
    );
}

#[test]
fn directives_do_not_carry_between_documents() {
    let d = yamluna_core::parse("%TAG ! tag:first/\n---\na: !T {}\n...\n---\nb: !T {}\n")
        .expect("parses");
    assert_eq!(d[0].tag_directives.len(), 1);
    assert!(d[1].tag_directives.is_empty());
    let resolved = |doc: &yamluna_core::Document| {
        let NodeKind::Mapping { entries } = &doc.node(doc.root.unwrap()).kind else {
            panic!()
        };
        doc.node(entries[0].value).tag.clone().unwrap().resolved
    };
    assert_eq!(resolved(&d[0]), "tag:first/T");
    assert_eq!(resolved(&d[1]), "!T");
}

#[test]
fn an_implicit_empty_node_has_an_empty_lexeme() {
    let d = one("k:\nseq:\n  -\n");
    let e = entries(&d);
    assert_eq!(d.node(e[0].value).raw.as_deref(), Some(""));
    let NodeKind::Sequence { items } = &d.node(e[1].value).kind else {
        panic!()
    };
    assert_eq!(d.node(items[0]).raw.as_deref(), Some(""));
    // A written `~` is not the same thing.
    let d = one("k: ~\n");
    assert_eq!(d.node(entries(&d)[0].value).raw.as_deref(), Some("~"));
}

#[test]
fn positions_are_zero_based_lines_and_columns() {
    let d = one("a: 1\nb: 22\n");
    let e = entries(&d);
    assert_eq!(
        (d.node(e[0].key).pos.line, d.node(e[0].key).pos.col),
        (0, 0)
    );
    assert_eq!(
        (d.node(e[1].value).pos.line, d.node(e[1].value).pos.col),
        (1, 3)
    );
}

#[test]
fn a_scan_error_carries_a_structured_position() {
    let err = yamluna_core::parse("a: [1, 2\n").expect_err("unterminated flow sequence");
    assert_eq!(err.kind, ErrorKind::Scanner);
    assert!(!err.message.is_empty());
    assert_eq!(err.line, 1, "0-based");
}

#[test]
fn crlf_input_loads_and_keeps_its_lexemes() {
    let d = one("a: 1\r\nb: 'x'  # c\r\n");
    let e = entries(&d);
    assert_eq!(d.node(e[1].value).raw.as_deref(), Some("'x'"));
    let Some(yamluna_core::Trivia::Comment { text, .. }) = &d.node(e[1].value).trivia.eol else {
        panic!("no eol comment")
    };
    assert_eq!(
        text, "# c",
        "the CR belongs to the line break, not the comment"
    );
}

#[test]
fn degenerate_inputs_load_without_inventing_anything() {
    for src in ["", "\n", "\n\n\n", "\u{feff}", "---\n", "...\n"] {
        let docs = yamluna_core::parse(src).unwrap_or_else(|e| panic!("{src:?}: {e}"));
        assert_eq!(docs.len(), 1, "{src:?}");
    }
    assert!(yamluna_core::parse("").unwrap()[0].root.is_none());
    assert!(yamluna_core::parse("\n\n").unwrap()[0].root.is_none());
    // Three breaks are three empty lines, and emitting three is what reproduces the source.
    assert_eq!(
        yamluna_core::parse("\n\n\n").unwrap()[0].trailing,
        vec![yamluna_core::Trivia::BlankLines(3)]
    );
    assert_eq!(
        yamluna_core::emit(
            &yamluna_core::parse("\n\n\n").unwrap(),
            &yamluna_core::EmitOptions::default()
        )
        .unwrap(),
        "\n\n\n"
    );
    // An explicitly started but empty document does have a (null) root.
    let d = &yamluna_core::parse("---\n").unwrap()[0];
    assert!(d.explicit_start);
    assert_eq!(d.node(d.root.unwrap()).raw.as_deref(), Some(""));
}

#[test]
fn a_deeply_nested_document_loads() {
    let mut src = String::new();
    for i in 0..200 {
        src.push_str(&"  ".repeat(i));
        src.push_str("k:\n");
    }
    let d = yamluna_core::parse(&src).expect("parses");
    // 200 keys, 200 mappings, and the implicit empty value of the innermost one.
    assert_eq!(d[0].nodes.len(), 401);
}
