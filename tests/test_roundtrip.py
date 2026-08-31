"""DESIGN 6.2, through the whole stack: ``YAML().dump(YAML().load(text)) == text``.

This is the acceptance criterion of the project, measured end to end -- parse in Rust,
construct into ``CommentedMap``/``CommentedSeq``/scalar types, represent back to records,
emit in Rust -- rather than at any one seam.  ``tests/test_bindings.py`` measures what the
FFI records lose, ``crates/yamluna-core/tests/roundtrip.rs`` measures what the emitter
loses; what is left over is what the *Python object model* loses, and that is this file.

For reference, ``ruamel.yaml==0.19.1`` round-trips 3 of the 40 round-trippable corpus files
byte-identically and yamluna round-trips 38 (``python tests/differential.py`` prints both
columns; it scores ``key-duplicate`` on behaviour, since no `dict` can write two equal keys
back, while :data:`KNOWN_LOSSES` below still records it as the byte-level loss it is).

Every file that does not round-trip is in :data:`KNOWN_LOSSES` with the fact the object
model cannot carry.  The test fails if one of them starts passing, so a fix can never leave
a stale excuse behind.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

#: Corpus files that do not survive a `load -> dump`, and what is lost.
#:
#: Four kinds of entry, and the distinction matters:
#:
#: * *object model* -- Python has no object that can hold the fact.  ``None`` is not
#:   subclassable, so a null cannot remember whether it was written `~` or `NULL`; a `dict`
#:   cannot hold two equal keys; `load` returns the root, so an empty document is `None` and
#:   is indistinguishable from a document whose root is a null.
#: * *document model* -- the fact is not in `yamluna_core::Document` either, so the pure-Rust
#:   round trip loses it too.  These are also in `KNOWN_FAILURES` in
#:   `crates/yamluna-core/tests/roundtrip.rs`.
#: * *records* -- the core keeps the fact and the pure-Rust round trip reproduces it, but the
#:   flat record classes of `python/yamluna/_record.py` have no slot to carry it across the
#:   FFI.  These are also in `KNOWN_RECORD_GAPS` in `tests/test_bindings.py`, which names the
#:   field each one needs.
#: * *scanner* -- the file does not load at all.  Also in `KNOWN_SCANNER_DEFECTS` in
#:   `crates/yamluna-core/tests/corpus.rs`.
KNOWN_LOSSES: dict[str, str] = {
    'key-duplicate': (
        'object model: `CommentedMap` is a `dict`, so two entries with equal keys collapse '
        'into one (and the default `allow_duplicate_keys=False` refuses the file first)'
    ),
}



def test_the_known_losses_are_all_real_corpus_files() -> None:
    """A renamed or deleted corpus file must not leave an excuse behind."""
    stems = {p.stem for p in (Path(__file__).parent / 'corpus').glob('*.yaml')}
    assert set(KNOWN_LOSSES) <= stems, sorted(set(KNOWN_LOSSES) - stems)


def test_corpus_file_round_trips_byte_for_byte(
    corpus_text: str, corpus_path: Path, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """`load -> dump` reproduces the source exactly, for a document nothing touched."""
    if corpus_path.stem in KNOWN_LOSSES:
        pytest.skip(f'known loss: {KNOWN_LOSSES[corpus_path.stem]}')
    assert yamluna_roundtrip(corpus_text) == corpus_text


def test_a_known_loss_still_loses(
    corpus_text: str, corpus_path: Path, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """The other half: an entry in :data:`KNOWN_LOSSES` that starts passing is a bug here."""
    if corpus_path.stem not in KNOWN_LOSSES:
        pytest.skip('not a known loss')
    try:
        output: str | None = yamluna_roundtrip(corpus_text)
    except Exception:
        output = None
    assert output != corpus_text, (
        f'{corpus_path.stem} now round-trips: drop it from KNOWN_LOSSES '
        f'({KNOWN_LOSSES[corpus_path.stem]})'
    )


def test_dumping_twice_is_a_fixed_point(
    corpus_text: str, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """Whatever the dump writes, a load reads back and writes again unchanged.

    This holds even for the known losses: a round trip may lose a fact once, but it may
    never keep drifting -- which is what makes a file under version control settle.
    """
    try:
        once = yamluna_roundtrip(corpus_text)
    except Exception:
        pytest.skip('does not load')
    assert yamluna_roundtrip(once) == once


def test_no_comment_is_ever_lost(
    corpus_text: str, corpus_path: Path, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """Every `#` run of the source comes back, in source order, even in a lossy file.

    No exemptions: a file may lose its layout, its tags or its null spellings and still
    owe every comment back.  A document with no root object to hang them on is no
    exception -- `YAML._empty` is where those live.
    """
    try:
        output = yamluna_roundtrip(corpus_text)
    except Exception:
        pytest.skip('does not load')
    assert _comments(output) == _comments(corpus_text)


def _comments(text: str) -> list[str]:
    """The `#` runs of a text: crude, and deliberately independent of the trivia model."""
    return [line[line.index('#') :].rstrip() for line in text.splitlines() if '#' in line]


# -- the two divergences the corpus files above pin at full size ------------------------
# Minimal repros, so a regression names the mechanism rather than just a corpus file.


@pytest.mark.parametrize(
    'source',
    [
        'flow_map: {\n  # inside\n  x: 1,\n}\n',
        'flow_map: {\n  # leading\n  x: 1,     # after an entry\n  y: 2,\n}\n',
    ],
    ids=['inner', 'inner-and-eol'],
)
def test_a_comment_inside_a_flow_collection_stays_inside_it(
    source: str, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """DIVERGENCES B10: ruamel collapses these to `{x: 1}`, comment and all.

    The slot that decides it is `inner` vs `before`: promoting a flow collection's `inner`
    trivia to `before` would push the opening brace onto the next line, which is exactly
    what the bug did.

    Only the `inner` slot is asserted byte-for-byte here.  Whether a flow collection's
    *opening bracket* keeps its own line is a different fact -- it is in `Node.flow_seps`,
    the separation the source wrote in front of the first child -- and the sequence shapes
    that depend on it are pinned by `corpus/comment-flow.yaml`.
    """
    assert yamluna_roundtrip(source) == source


@pytest.mark.parametrize(
    'source',
    [
        'keep: |+2\n\n    body\nlast: end\n',  # the blank line is content `|+` keeps
        'keep: |\n\n  body\nlast: end\n',  # ruamel adds an indicator: `|2`
        'keep: |-2\n    body\nlast: end\n',  # ruamel reorders to `|2-`
        'keep: |+\n  body\n\n\nlast: end\n',  # trailing blank lines are content too
    ],
    ids=['keep-and-indent', 'bare-pipe', 'chomp-then-indent', 'trailing-blanks'],
)
def test_a_block_scalar_header_is_reproduced_as_written(
    source: str, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """DIVERGENCES B11: ruamel rebuilds the header from the parsed value.

    The blank lines between the header and the body are *content* -- the cooked value
    begins with them -- so they belong in the lexeme, not in a trivia slot as well.
    """
    assert yamluna_roundtrip(source) == source
