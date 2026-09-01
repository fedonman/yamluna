//! Tests for the round-trip capabilities the `yamluna` fork adds on top of `saphyr-parser`:
//! comment events, collection style on the collection-start events, and anchor names.
//!
//! The upstream tests (and the 402 `yaml-test-suite` cases) cover the parser with the default
//! `keep_comments(false)`; these cover what turning it on produces, and the two new event fields.

use yamluna_scanner::{
    AnchorRef, Event, Parser, ScalarStyle, ScanError, Span, SpannedEventReceiver,
    StructureStyle::{self, Block, Flow},
};

// -------------------------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------------------------

fn anchor_str(anchor: &AnchorRef<'_>) -> String {
    if let Some(name) = &anchor.name {
        format!(" &{name}")
    } else {
        assert_eq!(anchor.id, 0, "an anchored node must carry its name");
        String::new()
    }
}

fn style_str(style: StructureStyle) -> &'static str {
    match style {
        Block => "block",
        Flow => "flow",
    }
}

fn scalar_char(style: ScalarStyle) -> char {
    match style {
        ScalarStyle::Plain => ':',
        ScalarStyle::SingleQuoted => '\'',
        ScalarStyle::DoubleQuoted => '"',
        ScalarStyle::Literal => '|',
        ScalarStyle::Folded => '>',
    }
}

/// Render an event compactly, keeping everything this fork adds.
fn render(ev: &Event<'_>) -> String {
    match ev {
        Event::Nothing => "?".into(),
        Event::StreamStart => "+STR".into(),
        Event::StreamEnd => "-STR".into(),
        Event::DocumentStart(true) => "+DOC ---".into(),
        Event::DocumentStart(false) => "+DOC".into(),
        Event::DocumentEnd => "-DOC".into(),
        Event::Alias(anchor) => format!(
            "=ALI *{}",
            anchor
                .name
                .as_deref()
                .expect("an alias must carry its name")
        ),
        Event::Scalar(value, style, anchor, _) => {
            format!(
                "=VAL{} {}{}",
                anchor_str(anchor),
                scalar_char(*style),
                value.escape_debug()
            )
        }
        Event::SequenceStart(anchor, _, style) => {
            format!("+SEQ{} {}", anchor_str(anchor), style_str(*style))
        }
        Event::SequenceEnd => "-SEQ".into(),
        Event::MappingStart(anchor, _, style) => {
            format!("+MAP{} {}", anchor_str(anchor), style_str(*style))
        }
        Event::MappingEnd => "-MAP".into(),
        Event::Comment(text) => format!("COM {text}"),
    }
}

/// Parse `source`, through both `StrInput` and `BufferedInput`, and render the events.
///
/// The two inputs implement the comment-capturing parts of `Input` differently (`StrInput`
/// overrides `skip_ws_to_eol`, `BufferedInput` uses the default), so running both is what keeps
/// the two implementations honest.
fn parse_with(source: &str, keep_comments: bool) -> Result<Vec<(Event<'_>, Span)>, ScanError> {
    let from_str: Result<Vec<_>, _> = Parser::new_from_str(source)
        .keep_comments(keep_comments)
        .collect();
    let from_iter: Result<Vec<_>, _> = Parser::new_from_iter(source.chars())
        .keep_comments(keep_comments)
        .collect();
    match (&from_str, &from_iter) {
        (Ok(a), Ok(b)) => assert_eq!(a, b, "StrInput and BufferedInput disagree"),
        (Err(a), Err(b)) => assert_eq!(a, b, "StrInput and BufferedInput disagree"),
        _ => panic!("one input errored and the other did not: {from_str:?} / {from_iter:?}"),
    }
    from_str
}

/// Render the event stream of `source`, comments included.
fn events(source: &str) -> Vec<String> {
    parse_with(source, true)
        .unwrap()
        .iter()
        .map(|(ev, _)| render(ev))
        .collect()
}

/// Render the event stream of `source` with comments off (the default).
fn events_without_comments(source: &str) -> Vec<String> {
    parse_with(source, false)
        .unwrap()
        .iter()
        .map(|(ev, _)| render(ev))
        .collect()
}

/// The comments of `source`, as `(text, line, col, start index, end index)`.
fn comments(source: &str) -> Vec<(String, usize, usize, usize, usize)> {
    parse_with(source, true)
        .unwrap()
        .into_iter()
        .filter_map(|(ev, span)| match ev {
            Event::Comment(text) => Some((
                text.into_owned(),
                span.start.line(),
                span.start.col(),
                span.start.index(),
                span.end.index(),
            )),
            _ => None,
        })
        .collect()
}

/// The comment texts of `source`, in source order.
fn comment_texts(source: &str) -> Vec<String> {
    comments(source).into_iter().map(|c| c.0).collect()
}

// -------------------------------------------------------------------------------------------
// Comments are off by default.
// -------------------------------------------------------------------------------------------

/// The default must be byte-for-byte the upstream behaviour: that is what keeps the 402
/// `yaml-test-suite` cases green.
#[test]
fn comments_are_off_by_default() {
    let with = "\
%YAML 1.2
# leading
--- # after ---
a: 1 # eol
# own line
b: [1, # in flow
    2] # after flow
c: |- # header
  text
... # after ...
";
    let without = "\
%YAML 1.2
---
a: 1
b: [1,
    2]
c: |-
  text
...
";
    assert!(
        !events_without_comments(with)
            .iter()
            .any(|e| e.starts_with("COM")),
        "comments must not be emitted unless asked for"
    );
    assert_eq!(
        events_without_comments(with),
        events_without_comments(without)
    );
    // With comments on, the *structure* is the same; only comment events are added.
    let structural: Vec<_> = events(with)
        .into_iter()
        .filter(|e| !e.starts_with("COM"))
        .collect();
    assert_eq!(structural, events_without_comments(with));
}

// -------------------------------------------------------------------------------------------
// Every position a comment can occur in.
// -------------------------------------------------------------------------------------------

#[test]
fn comment_at_top_of_file() {
    assert_eq!(
        events("# hello\na: 1\n"),
        [
            "+STR",
            "COM # hello",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "=VAL :1",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

#[test]
fn comment_only_file() {
    assert_eq!(
        events("# nothing else\n"),
        ["+STR", "COM # nothing else", "-STR"]
    );
    // No trailing newline either.
    assert_eq!(events("# no newline"), ["+STR", "COM # no newline", "-STR"]);
}

#[test]
fn eol_comment_after_plain_scalar() {
    assert_eq!(
        events("a: 1 # one\nb: 2 # two\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "=VAL :1",
            "COM # one",
            "=VAL :b",
            "=VAL :2",
            "COM # two",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

/// The classic hole in this patch: the `#` after a quoted scalar is eaten by
/// `Input::skip_ws_to_eol`, not by either of the two `#` arms in the scanner.
#[test]
fn eol_comment_after_quoted_scalar() {
    assert_eq!(
        comment_texts("a: \"dq\" # after double\nb: 'sq' # after single\n"),
        ["# after double", "# after single"]
    );
    assert_eq!(
        events("a: \"dq\" # after double\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "=VAL \"dq",
            "COM # after double",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

#[test]
fn comment_after_block_scalar() {
    assert_eq!(
        comment_texts("a: |\n  text\n# after literal\nb: >\n  folded\n# after folded\n"),
        ["# after literal", "# after folded"]
    );
}

/// A comment on the block scalar header line precedes the scalar's content, and is emitted there.
#[test]
fn comment_in_block_scalar_header() {
    assert_eq!(
        events("a: |- # header\n  text\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "COM # header",
            "=VAL |text",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
    assert_eq!(
        comment_texts("a: >2 # folded header\n   text\n"),
        ["# folded header"]
    );
}

#[test]
fn comments_between_block_sequence_items() {
    assert_eq!(
        events("- a # first\n# between\n- b\n"),
        [
            "+STR",
            "+DOC",
            "+SEQ block",
            "=VAL :a",
            "COM # first",
            "COM # between",
            "=VAL :b",
            "-SEQ",
            "-DOC",
            "-STR",
        ]
    );
    // A comment right after the `-` indicator, before the item.
    assert_eq!(comment_texts("- # dangling\n  a\n"), ["# dangling"]);
}

#[test]
fn comments_inside_flow_collections() {
    assert_eq!(
        comment_texts("[ # open\n  a, # first\n  b # second\n] # close\n"),
        ["# open", "# first", "# second", "# close"]
    );
    assert_eq!(
        comment_texts("{ # open\n  a: 1, # entry\n} # close\n"),
        ["# open", "# entry", "# close"]
    );
}

#[test]
fn comments_after_document_markers() {
    assert_eq!(
        events("--- # start\na\n... # end\n"),
        [
            "+STR",
            "+DOC ---",
            "COM # start",
            "=VAL :a",
            "-DOC",
            "COM # end",
            "-STR",
        ]
    );
}

#[test]
fn comment_after_directive() {
    assert_eq!(
        comment_texts("%YAML 1.2 # version\n%TAG !e! tag:e.com,2000: # tags\n--- a\n"),
        ["# version", "# tags"]
    );
    // The directive is still parsed.
    let mut parser = Parser::new_from_str("%YAML 1.2 # version\n--- a\n").keep_comments(true);
    while let Some(ev) = parser.next_event() {
        if matches!(ev.unwrap().0, Event::DocumentStart(_)) {
            break;
        }
    }
    assert_eq!(parser.version(), Some((1, 2)));
}

#[test]
fn comment_after_anchor() {
    assert_eq!(
        events("a: &anc # after anchor\n  b: 1\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "COM # after anchor",
            "+MAP &anc block",
            "=VAL :b",
            "=VAL :1",
            "-MAP",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
    assert_eq!(
        comment_texts("a: &anc 1 # after anchored scalar\n"),
        ["# after anchored scalar"]
    );
}

#[test]
fn comment_after_complex_key_indicator() {
    assert_eq!(comment_texts("? a # complex key\n: b\n"), ["# complex key"]);
}

#[test]
fn tab_indented_comment() {
    assert_eq!(
        comment_texts("a: 1\n\t# tab indented\nb: 2\n"),
        ["# tab indented"]
    );
    assert_eq!(
        comment_texts("a: 1 \t# tab before hash\n"),
        ["# tab before hash"]
    );
}

#[test]
fn comments_across_documents() {
    assert_eq!(
        comment_texts("# doc1 leading\na: 1\n# doc1 trailing\n---\n# doc2 leading\nb: 2\n"),
        ["# doc1 leading", "# doc1 trailing", "# doc2 leading"]
    );
}

/// Trailing whitespace inside a comment is content: the emitter reproduces it verbatim.
#[test]
fn comment_text_is_verbatim() {
    assert_eq!(
        comment_texts("a: 1 #  two spaces   \n"),
        ["#  two spaces   "]
    );
    assert_eq!(comment_texts("a: 1 #\n"), ["#"]);
    assert_eq!(
        comment_texts("a: 'q' #  after quote   \n"),
        ["#  after quote   "]
    );
    // A `\r\n` line break is not part of the comment either.
    assert_eq!(comment_texts("a: 1 # crlf\r\nb: 2\r\n"), ["# crlf"]);
}

/// Every comment's span must slice back to exactly the comment's text.
///
/// `Marker::index` counts characters rather than bytes, hence the `chars()` dance.
#[test]
fn comment_spans_slice_back_to_the_source() {
    let source = "\
# leading
héllo: 1 # unicode before me
list: # on the key
  - a # item
  - '你好' # after a quoted unicode scalar
flow: [1, 2] # after flow
block: |- # header
  text
# trailing
";
    let found = comments(source);
    assert_eq!(found.len(), 8, "{found:#?}");
    for (text, line, col, start, end) in found {
        let sliced: String = source.chars().skip(start).take(end - start).collect();
        assert_eq!(sliced, text, "span does not cover the comment text");
        let line_text = source.lines().nth(line - 1).unwrap();
        assert_eq!(
            line_text.chars().nth(col),
            Some('#'),
            "col {col} of line {line} ({line_text:?}) is not the `#`"
        );
    }
}

// -------------------------------------------------------------------------------------------
// `#` characters that are not comments.
// -------------------------------------------------------------------------------------------

#[test]
fn hashes_that_are_not_comments() {
    // Inside quoted scalars.
    assert_eq!(comment_texts("a: \"x # y\"\n"), Vec::<String>::new());
    assert_eq!(comment_texts("a: 'x # y'\n"), Vec::<String>::new());
    // A `#` not preceded by whitespace is part of a plain scalar.
    assert_eq!(comment_texts("a: plain#nothash\n"), Vec::<String>::new());
    assert_eq!(
        events("a: plain#nothash\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "=VAL :plain#nothash",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
    // Inside a tag URI (`#` is a valid URI character).
    assert_eq!(
        comment_texts("a: !<tag:e.com,2000:x#y> v\n"),
        Vec::<String>::new()
    );
    assert_eq!(
        comment_texts("%TAG !e! tag:e.com,2000:#\n--- !e!x v\n"),
        Vec::<String>::new()
    );
    // Inside a block scalar's content.
    assert_eq!(
        comment_texts("a: |\n  # not a comment\n"),
        Vec::<String>::new()
    );
    // And the value really did keep its `#`.
    assert_eq!(
        events("a: |\n  # not a comment\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "=VAL |# not a comment\\n",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

/// A `#` that follows a token without whitespace is still an error, as upstream.
#[test]
fn comment_must_be_preceded_by_whitespace() {
    let err = parse_with("a: [1]# oops\n", true).unwrap_err();
    assert!(err.info().contains("separated from other tokens"), "{err}");
    // ... and the same error with comments off.
    let err = parse_with("a: [1]# oops\n", false).unwrap_err();
    assert!(err.info().contains("separated from other tokens"), "{err}");
}

// -------------------------------------------------------------------------------------------
// Comments never enter the state machine.
// -------------------------------------------------------------------------------------------

#[derive(Default)]
struct Sink(Vec<String>);

impl<'input> SpannedEventReceiver<'input> for Sink {
    fn on_event(&mut self, ev: Event<'input>, _span: Span) {
        self.0.push(render(&ev));
    }
}

/// The push API (`Parser::load`) must forward comments to the receiver rather than choke on them.
#[test]
fn comments_reach_the_push_api() {
    let source = "# leading\na: 1 # eol\n";
    let mut sink = Sink::default();
    Parser::new_from_str(source)
        .keep_comments(true)
        .load(&mut sink, true)
        .unwrap();
    assert_eq!(
        sink.0,
        [
            "+STR",
            "COM # leading",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "=VAL :1",
            "COM # eol",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

/// `peek` and `next_event` must agree, comments included.
#[test]
fn peek_matches_next_with_comments() {
    let mut parser = Parser::new_from_str("# a\nk: [1] # b\n# c\n").keep_comments(true);
    loop {
        let peeked = parser.peek().unwrap().unwrap().clone();
        let taken = parser.next_event().unwrap().unwrap();
        assert_eq!(peeked, taken);
        if taken.0 == Event::StreamEnd {
            break;
        }
    }
}

// -------------------------------------------------------------------------------------------
// Collection style.
// -------------------------------------------------------------------------------------------

#[test]
fn nested_block_and_flow_styles() {
    assert_eq!(
        events("a:\n  - [1, {b: 2}]\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block", // a:
            "=VAL :a",
            "+SEQ block", // - ...
            "+SEQ flow",  // [1, ...]
            "=VAL :1",
            "+MAP flow", // {b: 2}
            "=VAL :b",
            "=VAL :2",
            "-MAP",
            "-SEQ",
            "-SEQ",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

/// The implicit flow mapping is the case that cannot be recovered downstream: its
/// `FlowMappingStart` token is synthetic and has an empty span.
#[test]
fn implicit_flow_mapping_is_flow() {
    assert_eq!(
        events("[a: 1]"),
        [
            "+STR",
            "+DOC",
            "+SEQ flow",
            "+MAP flow",
            "=VAL :a",
            "=VAL :1",
            "-MAP",
            "-SEQ",
            "-DOC",
            "-STR",
        ]
    );
    // Same shape, spelled out; same events but for nothing.
    assert_eq!(events("[{a: 1}]"), events("[a: 1]"));
    // The block spelling differs only in style.
    assert_eq!(
        events("- a: 1\n"),
        events("[a: 1]")
            .iter()
            .map(|e| e.replace("flow", "block"))
            .collect::<Vec<_>>()
    );
    // The empty-key form too.
    assert_eq!(
        events("[: 1]"),
        [
            "+STR",
            "+DOC",
            "+SEQ flow",
            "+MAP flow",
            "=VAL :~",
            "=VAL :1",
            "-MAP",
            "-SEQ",
            "-DOC",
            "-STR",
        ]
    );
}

/// An indentless sequence (a block sequence at the same indentation as its key) is block style.
#[test]
fn indentless_sequence_is_block() {
    assert_eq!(
        events("a:\n- 1\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :a",
            "+SEQ block",
            "=VAL :1",
            "-SEQ",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

/// A flow collection used as a block mapping key keeps its own style.
#[test]
fn flow_key_of_a_block_mapping() {
    assert_eq!(
        events("[a]: b\n"),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "+SEQ flow",
            "=VAL :a",
            "-SEQ",
            "=VAL :b",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

// -------------------------------------------------------------------------------------------
// Anchor names.
// -------------------------------------------------------------------------------------------

#[test]
fn anchor_names_on_all_four_event_kinds() {
    assert_eq!(
        events(
            "\
scalar: &s 1
seq: &q [1]
map: &m {k: v}
alias: *s
"
        ),
        [
            "+STR",
            "+DOC",
            "+MAP block",
            "=VAL :scalar",
            "=VAL &s :1",
            "=VAL :seq",
            "+SEQ &q flow",
            "=VAL :1",
            "-SEQ",
            "=VAL :map",
            "+MAP &m flow",
            "=VAL :k",
            "=VAL :v",
            "-MAP",
            "=VAL :alias",
            "=ALI *s",
            "-MAP",
            "-DOC",
            "-STR",
        ]
    );
}

/// `id == 0` means "no anchor"; `name` is `Some` exactly when `id != 0`.
#[test]
fn anchor_ids_and_names_agree() {
    let source = "a: &one 1\nb: &two [*one]\nc: 3\n";
    let mut anchors = Vec::new();
    for (ev, _) in parse_with(source, true).unwrap() {
        let (Event::Alias(anchor)
        | Event::Scalar(_, _, anchor, _)
        | Event::SequenceStart(anchor, _, _)
        | Event::MappingStart(anchor, _, _)) = ev
        else {
            continue;
        };
        assert_eq!(
            anchor.id == 0,
            anchor.name.is_none(),
            "id and name disagree: {anchor:?}"
        );
        if anchor.id != 0 {
            anchors.push((anchor.id, anchor.name.unwrap().into_owned()));
        }
    }
    // `&one`, `&two`, then the alias `*one` reporting the id it resolved to.
    assert_eq!(
        anchors,
        [
            (1, "one".to_string()),
            (2, "two".to_string()),
            (1, "one".to_string())
        ]
    );
}

/// An anchor reused later gets a fresh id, and each event reports the name as written.
#[test]
fn reused_anchor_name() {
    assert_eq!(
        events("- &a 1\n- &a 2\n- *a\n"),
        [
            "+STR",
            "+DOC",
            "+SEQ block",
            "=VAL &a :1",
            "=VAL &a :2",
            "=ALI *a",
            "-SEQ",
            "-DOC",
            "-STR",
        ]
    );
}

// -------------------------------------------------------------------------------------------
// Positions.
// -------------------------------------------------------------------------------------------

/// `Marker::index` is a char offset and `Marker::col` is 0-based, as the doc comments now say.
#[test]
fn markers_are_char_offsets_and_zero_based_columns() {
    let source = "a: 你好\nb: 2\n";
    let scalars: Vec<_> = parse_with(source, false)
        .unwrap()
        .into_iter()
        .filter_map(|(ev, span)| match ev {
            Event::Scalar(v, ..) => Some((v.into_owned(), span)),
            _ => None,
        })
        .collect();

    let (value, span) = &scalars[1];
    assert_eq!(value, "你好");
    // Char offsets: "a: " is 3 chars, and the scalar is 2 chars long.
    assert_eq!(span.start.index(), 3);
    assert_eq!(span.end.index(), 5);
    assert_eq!(span.start.col(), 3, "columns are 0-based");
    assert_eq!(span.start.line(), 1, "lines are 1-indexed");

    // The next line starts at char 6 (`\n` included), which is *byte* 10.
    let (value, span) = &scalars[2];
    assert_eq!(value, "b");
    assert_eq!(span.start.index(), 6);
    assert_eq!(span.start.col(), 0);
    assert_eq!(span.start.line(), 2);
}

/// A quoted scalar's span covers the lexeme, not the whitespace and comment that follow it.
///
/// Slicing the source by the span must give back what was written; that is what makes a byte-exact
/// round trip possible for the node.
#[test]
fn quoted_scalar_span_stops_at_the_closing_quote() {
    for source in [
        "a: \"dq\"   # comment\n",
        "a: 'sq'   # comment\n",
        "a: \"dq\"\n",
        "a: 'sq'   \n",
    ] {
        let (value, span) = parse_with(source, true)
            .unwrap()
            .into_iter()
            .find_map(|(ev, span)| match ev {
                Event::Scalar(v, ScalarStyle::DoubleQuoted | ScalarStyle::SingleQuoted, ..) => {
                    Some((v.into_owned(), span))
                }
                _ => None,
            })
            .unwrap();
        let sliced: String = source
            .chars()
            .skip(span.start.index())
            .take(span.end.index() - span.start.index())
            .collect();
        assert_eq!(sliced.len(), 4, "{source:?} -> {sliced:?}");
        assert!(sliced.contains(&value), "{source:?} -> {sliced:?}");
    }
}
