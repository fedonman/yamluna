"""DESIGN 6.2, through the whole stack: ``YAML().dump(YAML().load(text)) == text``.

This is the acceptance criterion of the project, measured end to end -- parse in Rust,
construct into ``CommentedMap``/``CommentedSeq``/scalar types, represent back to records,
emit in Rust -- rather than at any one seam.  ``tests/test_bindings.py`` measures what the
FFI records lose, ``crates/yamluna-core/tests/roundtrip.rs`` measures what the emitter
loses; what is left over is what the *Python object model* loses, and that is this file.

For reference, ``ruamel.yaml==0.19.1`` round-trips 3 of the 41 corpus files byte-identically
and yamluna round-trips 29 (``python tests/differential.py`` prints both columns).

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
#: Three kinds of entry, and the distinction matters:
#:
#: * *object model* -- Python has no object that can hold the fact.  ``None`` is not
#:   subclassable, so a null cannot remember whether it was written `~` or `NULL`; a `dict`
#:   cannot hold two equal keys; `load` returns the root, so an empty document is `None` and
#:   is indistinguishable from a document whose root is a null.
#: * *document model* -- the fact is not in `yamluna_core::Document` either, so the pure-Rust
#:   round trip loses it too.  These are also in `KNOWN_FAILURES` in
#:   `crates/yamluna-core/tests/roundtrip.rs`.
#: * *scanner* -- the file does not load at all.  Also in `KNOWN_SCANNER_DEFECTS` in
#:   `crates/yamluna-core/tests/corpus.rs`.
KNOWN_LOSSES: dict[str, str] = {
    'anchors-aliases': (
        'document model: a node records an anchor and a tag but not which was written '
        "first -- and `anchor_on_empty: &empty` anchors a null, which `None` cannot hold"
    ),
    'block-scalar-indent': (
        'object model: `.ca` gives a scalar value one slot for the trivia before it and '
        'the trivia after it, so the blank first line of a `|+2` body comes back after '
        'the scalar instead of inside it'
    ),
    'comment-flow': (
        'object model: `.ca.comment[1]` is one list holding what the records keep in two '
        'slots, so an own-line comment *inside* a flow collection is indistinguishable '
        'from one *above* it and comes back above the opening brace'
    ),
    'comment-only': (
        'object model: a document with no root loads as `None`, which carries nothing -- '
        'the stream-level comments have nowhere to ride'
    ),
    'directive-per-document': (
        'document model: a document records `%YAML` and `%TAG` but not their order on the '
        'page'
    ),
    'flow-forms': (
        'document model: a flow collection records where its items start, not its '
        'separators, so ` , ` and a trailing comma cannot come back'
    ),
    'key-duplicate': (
        'object model: `CommentedMap` is a `dict`, so two entries with equal keys collapse '
        'into one (and the default `allow_duplicate_keys=False` refuses the file first)'
    ),
    'scalar-binary': (
        'object model: `!!binary` constructs `bytes`, which cannot hold the `|` block form '
        'the payload was written in'
    ),
    'scalar-core-schema': (
        'object model: `None` is not subclassable, so a null cannot remember whether it '
        'was written `~`, `null`, `NULL` or `Null`'
    ),
    'struct-empty': (
        'object model: an empty document loads as `None` (see `comment-only`), and a null '
        'value cannot remember its spelling (see `scalar-core-schema`)'
    ),
    'tag-local-global': (
        'object model: a standard tag on a *scalar* (`!!str 123`) is applied and dropped -- '
        'the value becomes a plain `str`/`int`, which has nowhere to keep `.tag`'
    ),
    'text-tabs': (
        "scanner: the fork's `:`-in-flow check only accepts a space, so `{a:<TAB>b}` does "
        'not load'
    ),
}


#: The only files where a *comment* is lost, and it is the same cause in both: a document
#: with no root loads as `None`, so the comments that were the whole document have nothing
#: to ride on.  Everything else keeps every comment, even where it does not round-trip.
COMMENTS_LOST = frozenset({'comment-only', 'struct-empty'})


def test_the_known_losses_are_all_real_corpus_files() -> None:
    """A renamed or deleted corpus file must not leave an excuse behind."""
    stems = {p.stem for p in (Path(__file__).parent / 'corpus').glob('*.yaml')}
    assert set(KNOWN_LOSSES) <= stems, sorted(set(KNOWN_LOSSES) - stems)
    assert COMMENTS_LOST <= set(KNOWN_LOSSES), sorted(COMMENTS_LOST - set(KNOWN_LOSSES))


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
    except Exception:  # noqa: BLE001 - refusing the file is one of the failure modes
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
    except Exception:  # noqa: BLE001 - the file does not load; owned by the tests above
        pytest.skip('does not load')
    assert yamluna_roundtrip(once) == once


def test_no_comment_is_ever_lost(
    corpus_text: str, corpus_path: Path, yamluna_roundtrip: Callable[[str], str]
) -> None:
    """Every `#` run of the source comes back, in source order, even in a lossy file.

    The two files in :data:`COMMENTS_LOST` are the exception, and they are checked the
    other way round below so the exemption cannot quietly grow.
    """
    try:
        output = yamluna_roundtrip(corpus_text)
    except Exception:  # noqa: BLE001 - owned by the tests above
        pytest.skip('does not load')
    if corpus_path.stem in COMMENTS_LOST:
        assert _comments(output) != _comments(corpus_text), (
            f'{corpus_path.stem} now keeps its comments: drop it from COMMENTS_LOST'
        )
    else:
        assert _comments(output) == _comments(corpus_text)


def _comments(text: str) -> list[str]:
    """The `#` runs of a text: crude, and deliberately independent of the trivia model."""
    return [line[line.index('#') :].rstrip() for line in text.splitlines() if '#' in line]
