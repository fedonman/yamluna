//! One test per attachment rule, plus the ways the rules interact.
//!
//! The attachment rules decide which node a comment or a blank-line run hangs off, and that
//! choice is what puts it back where it was on a dump. Each `rN_` test pins one rule:
//! end-of-line comments (1), own-line comments and the column that picks the collection they
//! close (2), blank-line runs (3), document leading and trailing trivia (4), anchors and
//! aliases (5). The `x_` tests pin the cases where two rules meet. A failure here is a comment
//! that will move in a dump, and the assertion names the slot it moved to.

use yamluna_core::{Document, Node, NodeKind, Style, Trivia};

// ---------------------------------------------------------------- helpers

fn docs(src: &str) -> Vec<Document> {
    yamluna_core::parse(src).unwrap_or_else(|e| panic!("{src:?}: {e}"))
}

fn one(src: &str) -> Document {
    let mut d = docs(src);
    assert_eq!(d.len(), 1, "expected one document from {src:?}");
    d.remove(0)
}

fn root(d: &Document) -> &Node {
    d.node(d.root.expect("a root node"))
}

fn key<'a>(d: &'a Document, n: &Node, i: usize) -> &'a Node {
    let NodeKind::Mapping { entries } = &n.kind else {
        panic!("not a mapping")
    };
    d.node(entries[i].key)
}

fn val<'a>(d: &'a Document, n: &Node, i: usize) -> &'a Node {
    let NodeKind::Mapping { entries } = &n.kind else {
        panic!("not a mapping")
    };
    d.node(entries[i].value)
}

fn item<'a>(d: &'a Document, n: &Node, i: usize) -> &'a Node {
    let NodeKind::Sequence { items } = &n.kind else {
        panic!("not a sequence")
    };
    d.node(items[i])
}

/// `own@4:# text`, `eol@4:# text` or `blank:2`, short enough to write out in an assertion.
fn tv(t: &Trivia) -> String {
    match t {
        Trivia::Comment {
            text,
            own_line,
            col,
        } => {
            format!("{}@{col}:{text}", if *own_line { "own" } else { "eol" })
        }
        Trivia::BlankLines(n) => format!("blank:{n}"),
    }
}

fn slot(v: &[Trivia]) -> Vec<String> {
    v.iter().map(tv).collect()
}

fn eol(n: &Node) -> Option<String> {
    n.trivia.eol.as_ref().map(tv)
}

// ---------------------------------------------------------------- rule 1: end-of-line comments

#[test]
fn r1_an_eol_comment_belongs_to_the_value_not_the_key() {
    let d = one("k: v  # c\n");
    let r = root(&d);
    assert_eq!(eol(key(&d, r, 0)), None);
    assert_eq!(eol(val(&d, r, 0)).as_deref(), Some("eol@6:# c"));
}

#[test]
fn r1_an_eol_comment_after_a_key_whose_value_is_a_block_collection_is_the_values() {
    // The comment follows the `:`, so it belongs to the value even though the value's first
    // child is on the next line. That is what lets the emitter write `k:`, then the comment,
    // then the block.
    let d = one("k:  # c\n  a: 1\n");
    let r = root(&d);
    assert_eq!(eol(key(&d, r, 0)), None);
    let v = val(&d, r, 0);
    assert_eq!(v.style, Style::Block);
    assert_eq!(eol(v).as_deref(), Some("eol@4:# c"));
}

#[test]
fn r1_a_comment_between_the_key_and_the_colon_is_the_keys() {
    let d = one("? k  # c\n: v\n");
    let r = root(&d);
    assert_eq!(eol(key(&d, r, 0)).as_deref(), Some("eol@5:# c"));
    assert_eq!(eol(val(&d, r, 0)), None);
}

#[test]
fn r1_an_eol_comment_on_an_empty_value() {
    let d = one("k:  # c\nnext: 1\n");
    let r = root(&d);
    let v = val(&d, r, 0);
    assert_eq!(v.raw.as_deref(), Some(""));
    assert_eq!(eol(v).as_deref(), Some("eol@4:# c"));
}

#[test]
fn r1_an_eol_comment_on_a_block_scalar_header() {
    // The only place a comment may follow a `|`. It is not part of the scalar.
    let d = one("k: | # c\n  body\n");
    let v = val(&d, root(&d), 0);
    assert_eq!(v.value.as_deref(), Some("body\n"));
    assert_eq!(v.raw.as_deref(), Some("|\n  body"));
    assert_eq!(eol(v).as_deref(), Some("eol@5:# c"));
}

#[test]
fn r1_an_eol_comment_on_a_sequence_item_and_on_the_sequence() {
    let d = one("k:   # s\n  - a  # a\n  - b\n");
    let r = root(&d);
    let seq = val(&d, r, 0);
    assert_eq!(eol(seq).as_deref(), Some("eol@5:# s"));
    assert_eq!(eol(item(&d, seq, 0)).as_deref(), Some("eol@7:# a"));
    assert_eq!(eol(item(&d, seq, 1)), None);
}

#[test]
fn r1_an_eol_comment_after_a_flow_collection() {
    let d = one("k: [a, b]  # c\n");
    let v = val(&d, root(&d), 0);
    assert_eq!(v.style, Style::Flow);
    assert_eq!(eol(v).as_deref(), Some("eol@11:# c"));
}

// ---------------------------------------------------------------- rule 2: own-line comments

#[test]
fn r2_an_own_line_comment_leads_the_next_sibling() {
    let d = one("a: 1\n# c\nb: 2\n");
    let r = root(&d);
    assert_eq!(slot(&key(&d, r, 1).trivia.before), ["own@0:# c"]);
    assert!(key(&d, r, 0).trivia.is_empty());
}

#[test]
fn r2_a_comment_before_a_collection_leads_the_collection() {
    let d = one("k:\n  # c\n  - a\n");
    let seq = val(&d, root(&d), 0);
    // The comment lands in the collection's `inner` slot rather than the first item's
    // `before`, where rule 2 puts it. The emitter writes the two slots one after the other, so
    // the bytes match either way; the difference is pinned by the `take_before()` xfails in
    // `tests/test_mutation.py`.
    assert_eq!(slot(&seq.trivia.inner), ["own@2:# c"]);
    assert!(seq.trivia.before.is_empty());
    assert!(item(&d, seq, 0).trivia.is_empty());
}

#[test]
fn r2_a_trailing_comment_of_a_block_is_the_collections_after() {
    // Indented to the sequence's items, so it belongs to the sequence, not to `next`.
    let d = one("k:\n  - a\n  # c\nnext: 1\n");
    let r = root(&d);
    let seq = val(&d, r, 0);
    assert_eq!(slot(&seq.trivia.after), ["own@2:# c"]);
    assert!(key(&d, r, 1).trivia.is_empty());
}

#[test]
fn r2_a_comment_at_the_next_siblings_column_leads_the_sibling() {
    // Same source as above but one column left: now it is the leading comment of `next`.
    let d = one("k:\n  - a\n# c\nnext: 1\n");
    let r = root(&d);
    assert!(val(&d, r, 0).trivia.after.is_empty());
    assert_eq!(slot(&key(&d, r, 1).trivia.before), ["own@0:# c"]);
}

/// The enclosing-collection test of rule 2: one run of comments, three columns, three slots.
#[test]
fn r2_a_run_splits_by_column_across_every_collection_it_closes() {
    let src = "\
outer:
  inner:
    - a
    # deepest
  # middle
# shallowest
next: 1
";
    let d = one(src);
    let r = root(&d);
    let inner_map = val(&d, r, 0);
    let seq = val(&d, inner_map, 0);
    assert_eq!(slot(&seq.trivia.after), ["own@4:# deepest"]);
    assert_eq!(slot(&inner_map.trivia.after), ["own@2:# middle"]);
    assert_eq!(slot(&key(&d, r, 1).trivia.before), ["own@0:# shallowest"]);
}

#[test]
fn r2_a_comment_indented_past_the_deepest_collection_still_lands_in_it() {
    let d = one("k:\n  a: 1\n      # c\nnext: 2\n");
    let r = root(&d);
    assert_eq!(slot(&val(&d, r, 0).trivia.after), ["own@6:# c"]);
}

#[test]
fn r2_a_flow_collection_takes_everything_up_to_its_bracket() {
    // Column says nothing inside a flow collection: the bracket delimits it.
    let d = one("k: [\n  a,\n  # c\n]\n");
    let seq = val(&d, root(&d), 0);
    assert_eq!(slot(&seq.trivia.after), ["own@2:# c"]);
}

#[test]
fn r2_trivia_between_a_flow_start_and_its_first_child_is_inner() {
    let d = one("k: [\n  # c\n  a,\n]\n");
    let seq = val(&d, root(&d), 0);
    assert_eq!(slot(&seq.trivia.inner), ["own@2:# c"]);
    assert!(item(&d, seq, 0).trivia.is_empty());
}

// ---------------------------------------------------------------- rule 3: blank lines

#[test]
fn r3_a_blank_run_is_counted_not_smuggled_into_comment_text() {
    let d = one("a: 1\n\n\n\nb: 2\n");
    let r = root(&d);
    assert_eq!(slot(&key(&d, r, 1).trivia.before), ["blank:3"]);
}

#[test]
fn r3_a_blank_run_takes_the_slot_of_the_trivia_that_follows_it() {
    let d = one("a: 1\n\n# c\nb: 2\n");
    let r = root(&d);
    assert_eq!(slot(&key(&d, r, 1).trivia.before), ["blank:1", "own@0:# c"]);
}

#[test]
fn r3_a_blank_run_between_two_comments_keeps_both_apart() {
    let d = one("a: 1\n# c1\n\n\n# c2\nb: 2\n");
    let r = root(&d);
    assert_eq!(
        slot(&key(&d, r, 1).trivia.before),
        ["own@0:# c1", "blank:2", "own@0:# c2"]
    );
}

#[test]
fn r3_blank_lines_at_end_of_file_are_document_trailing() {
    let d = one("a: 1\n\n\n");
    assert_eq!(slot(&d.trailing), ["blank:2"]);
}

#[test]
fn r3_a_blank_run_inside_a_block_scalar_is_content_not_trivia() {
    let d = one("k: |\n  one\n\n  two\nnext: 1\n");
    let r = root(&d);
    let v = val(&d, r, 0);
    assert_eq!(v.value.as_deref(), Some("one\n\ntwo\n"));
    assert_eq!(v.raw.as_deref(), Some("|\n  one\n\n  two"));
    assert!(key(&d, r, 1).trivia.is_empty());
}

// ---------------------------------------------------------------- rule 4: document trivia

#[test]
fn r4_comments_before_the_first_token_are_document_leading() {
    let d = one("# c1\n# c2\nfirst: 1\n");
    assert_eq!(slot(&d.leading), ["own@0:# c1", "own@0:# c2"]);
    assert!(root(&d).trivia.is_empty());
}

#[test]
fn r4_a_comment_on_the_document_start_marker_is_leading_and_marked_eol() {
    let d = one("--- # c\na: 1\n");
    assert!(d.explicit_start);
    assert_eq!(slot(&d.leading), ["eol@4:# c"]);
}

#[test]
fn r4_a_comment_on_the_document_end_marker_is_trailing_and_marked_eol() {
    let d = one("a: 1\n... # c\n");
    assert!(d.explicit_end);
    assert_eq!(slot(&d.trailing), ["eol@4:# c"]);
}

#[test]
fn r4_comments_after_the_last_node_are_document_trailing() {
    let d = one("a: 1\n# c\n");
    assert_eq!(slot(&d.trailing), ["own@0:# c"]);
}

#[test]
fn r4_comments_between_two_documents_lead_the_second() {
    let d = docs("a: 1\n...\n# c\n---\nb: 2\n");
    assert_eq!(d.len(), 2);
    assert!(d[0].trailing.is_empty());
    assert_eq!(slot(&d[1].leading), ["own@0:# c"]);
}

#[test]
fn r4_a_stream_of_only_trivia_yields_one_rootless_document() {
    let d = one("# c1\n\n# c2\n");
    assert!(
        d.root.is_none(),
        "the round trip must not invent a document node"
    );
    assert!(d.nodes.is_empty());
    assert_eq!(slot(&d.trailing), ["own@0:# c1", "blank:1", "own@0:# c2"]);
}

// ---------------------------------------------------------------- rule 5: anchors and aliases

#[test]
fn r5_a_comment_inside_an_anchored_subtree_stays_at_the_anchor() {
    let d = one("base: &b\n  # c\n  a: 1\nuse: *b\n");
    let r = root(&d);
    let anchored = val(&d, r, 0);
    assert_eq!(anchored.anchor.as_deref(), Some("b"));
    // `inner` (see `r2_a_comment_before_a_collection_leads_the_collection`).
    assert_eq!(slot(&anchored.trivia.inner), ["own@2:# c"]);
    assert!(anchored.trivia.before.is_empty());
    let alias = val(&d, r, 1);
    assert_eq!(alias.kind, NodeKind::Alias { anchor: "b".into() });
    assert!(
        alias.trivia.is_empty(),
        "an alias site re-emits `*name` and nothing else"
    );
}

#[test]
fn r5_an_alias_is_a_node_not_a_clone_of_its_target() {
    let d = one("base: &b\n  a: 1\nuse: *b\n");
    let r = root(&d);
    assert!(matches!(val(&d, r, 0).kind, NodeKind::Mapping { .. }));
    assert!(matches!(val(&d, r, 1).kind, NodeKind::Alias { .. }));
}

#[test]
fn r5_a_recursive_anchor_loads() {
    let d = one("self: &s\n  name: n\n  next: *s\n");
    let inner = val(&d, root(&d), 0);
    assert_eq!(inner.anchor.as_deref(), Some("s"));
    assert_eq!(
        val(&d, inner, 1).kind,
        NodeKind::Alias { anchor: "s".into() }
    );
}

// ---------------------------------------------------------------- rules interacting

#[test]
fn x_an_eol_comment_and_an_own_line_run_on_the_same_dedent() {
    let src = "\
k:
  - a  # eol
  # after the sequence
next: 1
";
    let d = one(src);
    let r = root(&d);
    let seq = val(&d, r, 0);
    assert_eq!(eol(item(&d, seq, 0)).as_deref(), Some("eol@7:# eol"));
    assert_eq!(slot(&seq.trivia.after), ["own@2:# after the sequence"]);
}

#[test]
fn x_a_blank_run_inside_a_split_run_stays_with_what_follows_it() {
    let src = "\
k:
  - a
  # deep

# shallow
next: 1
";
    let d = one(src);
    let r = root(&d);
    assert_eq!(slot(&val(&d, r, 0).trivia.after), ["own@2:# deep"]);
    assert_eq!(
        slot(&key(&d, r, 1).trivia.before),
        ["blank:1", "own@0:# shallow"]
    );
}

#[test]
fn x_leading_trivia_keeps_its_blank_runs_and_its_marker_comment() {
    let d = one("# header\n\n# more\n--- # on the marker\na: 1\n");
    assert_eq!(
        slot(&d.leading),
        [
            "own@0:# header",
            "blank:1",
            "own@0:# more",
            "eol@4:# on the marker"
        ]
    );
}

#[test]
fn x_a_comment_that_ends_an_anchored_block_is_the_anchored_nodes_after() {
    let src = "\
base: &b
  a: 1
  # end of the anchored mapping
use: *b
";
    let d = one(src);
    let r = root(&d);
    let anchored = val(&d, r, 0);
    assert_eq!(
        slot(&anchored.trivia.after),
        ["own@2:# end of the anchored mapping"]
    );
    assert!(val(&d, r, 1).trivia.is_empty());
}

#[test]
fn x_every_slot_of_one_node_at_once() {
    let src = "\
k:
  # before the sequence
  - a
  # after the sequence
next: 1
";
    let d = one(src);
    let seq = val(&d, root(&d), 0);
    // `inner` (see `r2_a_comment_before_a_collection_leads_the_collection`).
    assert_eq!(slot(&seq.trivia.inner), ["own@2:# before the sequence"]);
    assert!(seq.trivia.before.is_empty());
    assert_eq!(slot(&seq.trivia.after), ["own@2:# after the sequence"]);
}

#[test]
fn x_trivia_of_a_flow_collection_in_every_slot() {
    let src = "\
k: [   # eol
  # inner
  a,
  # after
]
";
    let d = one(src);
    let seq = val(&d, root(&d), 0);
    assert_eq!(eol(seq).as_deref(), Some("eol@7:# eol"));
    assert_eq!(slot(&seq.trivia.inner), ["own@2:# inner"]);
    assert_eq!(slot(&seq.trivia.after), ["own@2:# after"]);
}
