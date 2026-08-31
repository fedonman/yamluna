use yamluna_scanner::{Event, Parser, ScanError};

fn collect_scalars(input: &str) -> Result<Vec<String>, ScanError> {
    let mut out = Vec::new();
    for item in Parser::new_from_str(input) {
        let ev = item?;
        if let Event::Scalar(s, ..) = ev.0 {
            out.push(s.into());
        }
    }
    Ok(out)
}

#[test]
fn tab_in_block_literal_body_is_allowed() {
    // A tab in the body of a literal block scalar (|) should be accepted.
    let yaml = "key: |\n  a\tb"; // 'a\tb' inside the block content
    let scalars = collect_scalars(yaml).expect("parser should accept tab inside block scalar body");
    // Literal style preserves newlines; a single content line ends with a trailing \n
    assert_eq!(scalars, vec!["key".to_string(), "a\tb\n".to_string()]);
}

#[test]
fn tab_in_block_folded_body_is_allowed() {
    // A tab in the body of a folded block scalar (>) should be accepted as content.
    let yaml = "key: >\n  a\tb";
    let scalars =
        collect_scalars(yaml).expect("parser should accept tab inside folded block scalar body");
    // For a single content line, folded and literal both end with a trailing \n
    assert_eq!(scalars, vec!["key".to_string(), "a\tb\n".to_string()]);
}

#[test]
fn tab_at_start_of_block_scalar_is_rejected() {
    // If the first content character of the block scalar is a tab, it must be rejected.
    // This means the content line starts with a tab instead of spaces for indentation.
    let yaml = "key: |\n\tvalue";

    let mut got_err: Option<ScanError> = None;
    for item in Parser::new_from_str(yaml) {
        match item {
            Ok(_) => continue,
            Err(e) => {
                got_err = Some(e);
                break;
            }
        }
    }

    let err =
        got_err.expect("expected a ScanError due to leading tab at start of block scalar content");
    // The scanner has a specific error for this case.
    assert!(
        err.info()
            .contains("a block scalar content cannot start with a tab"),
        "unexpected error message: {}",
        err.info()
    );
}

#[test]
fn tab_after_colon_in_flow_mapping_is_separation_whitespace() {
    // YAML 1.2.2 [148]: `:` in a flow mapping is followed by `s-separate(n,c)`, which for a flow
    // context is `s-separate-lines` -> `s-white+`, and [33] `s-white` includes TAB. There is no
    // indentation inside a flow collection, so nothing here can be a block collection.
    let scalars = collect_scalars("flow_map: {a:\tb, c:\td}\n")
        .expect("parser should accept a tab after ':' inside a flow mapping");
    assert_eq!(scalars, ["flow_map", "a", "b", "c", "d"]);
}

#[test]
fn tab_after_colon_in_block_context_is_still_rejected() {
    // yaml-test-suite Y79Y-08/Y79Y-10: in block context the value on the same line must be a flow
    // node; a nested block collection needs `s-indent`, which is spaces only.
    for yaml in ["? -\n:\t-\n", "? key:\n:\tkey:\n"] {
        let err = collect_scalars(yaml).expect_err("block context must still reject this");
        assert!(
            err.info()
                .contains("must be followed by a valid YAML whitespace"),
            "unexpected error for {yaml:?}: {}",
            err.info()
        );
    }
}
