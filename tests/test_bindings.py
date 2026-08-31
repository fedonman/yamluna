"""The PyO3 boundary: `yamluna._yamluna.parse` over the corpus, positions, and errors.

What this file is for is the half of DESIGN 3 that no pure-Python test can reach: that the
records Rust builds are the ones `_record.py` describes, that positions stay **char**
offsets all the way across, and that a `ParseError` arrives as the right exception class
rather than as a string someone has to re-parse.

Run: PYTHONPATH=python .venv/bin/pytest tests/test_bindings.py -q
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

import pytest
from yamluna._record import (
    KIND_ALIAS,
    KIND_MAPPING,
    KIND_NAMES,
    KIND_SCALAR,
    KIND_SEQUENCE,
    STYLE_BLOCK,
    STYLE_FLOW,
    STYLE_FOLDED,
    STYLE_LITERAL,
    STYLE_PLAIN,
    Doc,
    EmitOptions,
    Node,
    Trivia,
)
from yamluna.error import MarkedYAMLError, ScannerError

_yamluna = pytest.importorskip(
    'yamluna._yamluna', reason='extension not built yet: maturin develop --uv'
)
parse = _yamluna.parse

#: 'é' is 2 bytes, '☕' 3, '😀' 4.  Any byte-offset arithmetic goes wrong from line 0 on.
UNICODE = 'café: ☕\nemoji: 😀 tail\nlast: [1, 2\n'

#: Corpus files the *scanner* cannot load yet, mirroring `KNOWN_SCANNER_DEFECTS` in
#: `crates/yamluna-core/tests/corpus.rs`.  `test_the_known_scanner_defect_still_fails`
#: below is what makes the entry disappear when the fork is fixed.
KNOWN_SCANNER_DEFECTS = {'text-tabs': "':' must be followed by a valid YAML whitespace"}

#: Corpus files the *record classes* cannot carry, with the field that is missing.  These are
#: gaps in `python/yamluna/_record.py`, not in this crate: `yamluna_core` records each fact and
#: the emitter uses it, but there is no slot to put it in on the way through Python.
#: `test_the_record_path_matches_the_pure_rust_round_trip` asserts each one still fails, so the
#: entry disappears when the slot is added.
#: Corpus files the record path cannot carry, and the record field that would carry them.
#:
#: Empty: `Node.explicit`, `Doc.bom` and `Doc.final_line_break` closed the last three.  The
#: test below fails if an entry starts passing, so a closed gap cannot leave a stale excuse
#: behind -- and an entry added here has to name the field it needs.
KNOWN_RECORD_GAPS: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _skip_known_defects(request: pytest.FixtureRequest) -> None:
    """Corpus-parametrised tests skip the files the scanner cannot load."""
    params = getattr(request.node, 'callspec', None)
    path = params.params.get('corpus_path') if params is not None else None
    if path is not None and path.stem in KNOWN_SCANNER_DEFECTS:
        pytest.skip(f'known scanner defect: {path.stem}')


# -- structural sanity over the corpus ----------------------------------------------------


def _walk(doc: Doc) -> list[int]:
    """Every node id reachable from the root, in pre-order.  Duplicates are kept: the
    loader builds a tree, and a repeat would mean two parents share a node."""
    seen: list[int] = []
    stack = [] if doc.root is None else [doc.root]
    while stack:
        i = stack.pop()
        seen.append(i)
        stack.extend(reversed(doc.nodes[i].children))
    return seen


def _check_trivia(t: Any, where: str) -> None:
    assert isinstance(t, Trivia), where
    if t.blank_lines:
        assert t.text is None, f'{where}: a blank run carries no text'
        assert t.blank_lines > 0, where
    else:
        assert isinstance(t.text, str) and t.text.startswith('#'), f'{where}: {t.text!r}'
        assert '\n' not in t.text and '\r' not in t.text, f'{where}: break inside a comment'
        assert t.col >= 0, where


def test_every_corpus_file_parses_into_sane_records(corpus_text: str, corpus_path: Path) -> None:
    docs = parse(corpus_text)
    assert isinstance(docs, list) and docs, corpus_path.name
    for d, doc in enumerate(docs):
        assert isinstance(doc, Doc)
        where = f'{corpus_path.name} doc {d}'
        assert all(isinstance(n, Node) for n in doc.nodes), where

        # The arena is a tree: every node is reachable from the root, exactly once.
        reachable = _walk(doc)
        assert len(reachable) == len(set(reachable)), f'{where}: a node has two parents'
        assert set(reachable) == set(range(len(doc.nodes))), f'{where}: unreachable nodes'
        assert doc.root is None or 0 <= doc.root < len(doc.nodes), where

        for i, n in enumerate(doc.nodes):
            at = f'{where} node {i}'
            assert 0 <= n.kind < len(KIND_NAMES), at
            assert all(0 <= c < len(doc.nodes) for c in n.children), at
            assert n.line >= 0 and n.col >= 0, at
            if n.kind == KIND_SCALAR:
                assert STYLE_PLAIN <= n.style <= STYLE_FOLDED, at
                assert not n.children, at
                # A *loaded* scalar always has its lexeme; that is the round-trip invariant.
                assert n.raw is not None, at
                assert n.value is not None, at
            elif n.kind == KIND_ALIAS:
                assert not n.children and n.anchor, at
            else:
                assert n.style in (STYLE_BLOCK, STYLE_FLOW), at
                assert n.raw is None and n.value is None, at
            if n.kind == KIND_MAPPING:
                assert len(n.children) % 2 == 0, at
                assert all(m % 2 == 0 and m < len(n.children) for m in n.merge), at
            else:
                assert not n.merge, at

            for slot in ('before', 'inner', 'after'):
                for t in getattr(n, slot):
                    _check_trivia(t, f'{at} {slot}')
            if n.eol is not None:
                _check_trivia(n.eol, f'{at} eol')
                assert n.eol.text is not None, at

        for slot in ('leading', 'trailing'):
            for t in getattr(doc, slot):
                _check_trivia(t, f'{where} {slot}')


def test_every_comment_in_the_corpus_survives(corpus_text: str, corpus_path: Path) -> None:
    """A comment is not silently dropped: as many `Trivia` comments as source `#` lines.

    Counted loosely -- a `#` inside a quoted scalar is not a comment -- so the assertion is
    only that a file with comments produces some, and that no file invents any.
    """
    docs = parse(corpus_text)
    got = [
        t
        for doc in docs
        for t in (
            doc.leading
            + doc.trailing
            + [x for n in doc.nodes for x in n.before + n.inner + n.after]
            + [n.eol for n in doc.nodes if n.eol is not None]
        )
        if t.text is not None
    ]
    hashes = sum(line.lstrip().startswith('#') for line in corpus_text.splitlines())
    if hashes:
        assert got, f'{corpus_path.name} has {hashes} own-line comments and no Trivia'
    assert len(got) <= corpus_text.count('#'), corpus_path.name


def test_a_scalar_lexeme_sits_where_the_node_says_it_does(corpus_text: str) -> None:
    """`raw` at `(line, col)` is the byte-exact round trip in miniature -- and it only
    holds if `col` is a char column, which is the whole point of DESIGN 1.5."""
    lines = corpus_text.split('\n')
    for doc in parse(corpus_text):
        for n in doc.nodes:
            if n.kind != KIND_SCALAR or not n.raw:
                continue
            if n.style in (STYLE_LITERAL, STYLE_FOLDED):
                # A block scalar's `pos` is its *body*, not the `|`/`>` header, which sits on
                # the line that introduces it.  `raw` starts at the header, so there is
                # nothing to compare against here.
                continue
            head = n.raw.split('\n', 1)[0]
            assert lines[n.line][n.col : n.col + len(head)] == head, (n.line, n.col, head)


def test_the_record_path_matches_the_pure_rust_round_trip(
    corpus_text: str, corpus_path: Path
) -> None:
    """`emit(parse(text))` through the records == `parse` then `emit` inside Rust.

    This is the boundary's own acceptance criterion, and the reason
    `_roundtrip_in_rust` exists: it holds the emitter fixed, so the only thing it can
    measure is what a trip through the `_record` classes loses.  DESIGN 6.2 (the output
    equals the *source*) is the emitter's to pass; this is the half that is ours.
    """
    opts = EmitOptions()
    reference = _yamluna._roundtrip_in_rust(corpus_text, opts)
    through_records = _yamluna.emit(parse(corpus_text), opts)
    if corpus_path.stem in KNOWN_RECORD_GAPS:
        assert through_records != reference, (
            f'{corpus_path.stem} now survives the records: '
            f'drop it from KNOWN_RECORD_GAPS ({KNOWN_RECORD_GAPS[corpus_path.stem]})'
        )
    else:
        assert through_records == reference


# -- positions are char offsets, all the way to `Mark` ------------------------------------


def test_positions_are_char_columns_not_byte_columns() -> None:
    # 'k: 😀 é' -- the value is at char column 3, byte column 3, but 'é' after it makes the
    # next line's arithmetic diverge, and the alias is at char column 5 / byte column 8.
    docs = parse('k: 😀 é\nlist: [a]\n')
    line0 = 'k: 😀 é'
    for n in docs[0].nodes:
        if n.line == 0 and n.raw:
            assert line0[n.col : n.col + len(n.raw)] == n.raw


def test_a_mark_on_a_unicode_line_points_at_the_right_character() -> None:
    with pytest.raises(ScannerError) as excinfo:
        parse(UNICODE)
    mark = excinfo.value.problem_mark
    # The flow sequence is never closed, so the error lands at the end of the stream.
    assert mark.index == len(UNICODE)  # chars, not the 39 bytes of UTF-8
    assert mark.buffer is UNICODE or mark.buffer == UNICODE
    assert mark.get_snippet() is not None


def test_the_mark_index_is_a_char_offset_into_the_source() -> None:
    src = 'k: 😀 é\nbad: *\n'  # `*` with no name: a scanner error, mid-file
    with pytest.raises(ScannerError) as excinfo:
        parse(src)
    mark = excinfo.value.problem_mark
    assert src[mark.index] == '*'
    assert mark.line == 1
    assert mark.column == 5
    assert '^' in (mark.get_snippet() or '')


def test_the_bom_is_stripped_and_does_not_shift_positions() -> None:
    docs = parse('﻿a: 1\n')
    root = docs[0].nodes[docs[0].root]
    key = docs[0].nodes[root.children[0]]
    assert (key.line, key.col, key.raw) == (0, 0, 'a')


def test_the_known_scanner_defect_still_fails_the_same_way() -> None:
    """The mirror of `known_scanner_defects_still_fail_the_same_way` in yamluna-core: when
    the fork learns to accept a tab after `:` in flow context, this test says so."""
    for stem, message in KNOWN_SCANNER_DEFECTS.items():
        text = (Path(__file__).parent / 'corpus' / f'{stem}.yaml').read_bytes().decode('utf-8')
        with pytest.raises(ScannerError) as excinfo:
            parse(text)
        assert excinfo.value.problem == message, f'{stem}: different failure -- update the list'


# -- errors cross as data -----------------------------------------------------------------


def test_a_scanner_error_arrives_as_scannererror_with_a_mark() -> None:
    with pytest.raises(ScannerError) as excinfo:
        parse('a: [1, 2\n')
    err = excinfo.value
    assert isinstance(err, MarkedYAMLError)
    assert err.problem and 'flow sequence' in err.problem
    assert err.problem_mark.name == '<unicode string>'
    assert 'line 2' in str(err)  # printed 1-based


def test_the_stream_name_reaches_the_mark() -> None:
    with pytest.raises(ScannerError) as excinfo:
        parse('a: [1, 2\n', name='conf.yaml')
    assert excinfo.value.problem_mark.name == 'conf.yaml'
    assert 'conf.yaml' in str(excinfo.value)


def test_a_hand_built_record_is_rejected_rather_than_crashing_the_emitter() -> None:
    bad = Doc(root=0, nodes=[Node(kind=KIND_SEQUENCE, style=STYLE_BLOCK, children=[7])])
    with pytest.raises(ValueError, match='node 7'):
        _yamluna.emit([bad], EmitOptions())


# -- the GIL is actually released ---------------------------------------------------------


def test_parse_runs_from_several_threads() -> None:
    src = 'a: 1\nb: [2, 3]\n# tail\n'
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = [f.result() for f in [pool.submit(parse, src) for _ in range(64)]]
    assert all(r == results[0] for r in results)
