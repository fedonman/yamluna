"""DESIGN 6.2 over `yaml-test-suite`, through the **Python** API.

`crates/yamluna-core/tests/proptest_roundtrip.rs` runs the 308 suite cases through
`parse -> emit` inside Rust and scores them.  This file runs the identical case list through
``YAML().load_all -> YAML().dump_all`` -- the API a user actually holds -- and scores it the
same way, so the two numbers are directly comparable and the *difference* between them is
exactly what the FFI seam and the Python object model lose.

That difference was once found by accident because nothing measured it: at `8b05b39` the same
308 cases scored 302 in Rust and 202 here.  This file is the gate that makes a second accident
impossible:

* every failing case is in :data:`KNOWN_GAPS` with a one-line cause, and an unlisted failure
  fails the run;
* a listed case that starts passing *also* fails the run, so a fix cannot leave a stale
  excuse behind and progress cannot go unnoticed;
* the score is printed (``pytest -s``) and floored at ``len(cases) - len(KNOWN_GAPS)``, so
  shrinking the list raises the bar by itself and no number is hardcoded.

Extraction is a port of `suite_cases()` in `proptest_roundtrip.rs`, down to the
visual-escape table and the id numbering, so "308 cases" means the same 308 sources in both
harnesses; :func:`test_extraction_matches_the_rust_harness` guards that.

A raise counts as a failure, not as a skip.  A user who gets a traceback where ruamel
returns text has lost the round trip at least as thoroughly as one who gets other bytes.

`tests/suite_roundtrip.py` is the interactive companion: it prints the same score with a
diff per case, for working on a gap rather than gating on it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from ruamel.yaml import YAML as _RuamelYAML

SUITE_DIR = (
    Path(__file__).parents[1] / 'crates/yamluna-scanner/tests/yaml-test-suite/src'
)

#: The suite writes white space it wants you to see with visible stand-ins.  Same table,
#: same order, as `visual_to_raw` in `proptest_roundtrip.rs`.
_VISUAL = (
    ('␣', ' '),
    ('»', '\t'),
    ('—', ''),  # tab line continuation ——»
    ('←', '\r'),
    ('⇔', '﻿'),
    ('↵', ''),  # trailing newline marker
    ('∎\n', ''),
)


class SuiteCase(NamedTuple):
    """A `yaml-test-suite` case: the id it is reported under and the source it holds."""

    id: str
    yaml: str


def visual_to_raw(yaml: str) -> str:
    """The suite's visible stand-ins turned back into the bytes they stand for."""
    for pattern, replacement in _VISUAL:
        yaml = yaml.replace(pattern, replacement)
    return yaml


def suite_cases() -> list[SuiteCase]:
    """Every case in the suite that is *expected to parse*.

    ``fail: true`` and ``skip`` cases are dropped, and every field except ``fail`` is
    inherited from the previous case in the file -- exactly as the Rust harness reads them.
    """
    meta = _RuamelYAML(typ='safe')
    meta.allow_duplicate_keys = True
    out: list[SuiteCase] = []
    paths = sorted(SUITE_DIR.glob('*.yaml'))
    assert paths, f'yaml-test-suite is empty: {SUITE_DIR}'

    for path in paths:
        cases = meta.load(path.read_text(encoding='utf-8'))
        assert isinstance(cases, list), f'{path.stem}: expected a list of cases'
        current: dict[str, Any] = {}
        for index, case in enumerate(cases):
            name = f'{path.stem}-{index:02}' if len(cases) > 1 else path.stem
            current.pop('fail', None)
            current.update(case)
            if 'skip' in current or current.get('fail') is True:
                continue
            out.append(SuiteCase(name, visual_to_raw(current['yaml'])))
    return out


#: Suite cases that do not survive ``load_all -> dump_all``, and why.
#:
#: ``(core)`` marks a case the *Rust* round trip loses too -- the same entry is in
#: `KNOWN_GAPS` in `crates/yamluna-core/tests/proptest_roundtrip.rs`, so it is a core defect
#: and not a Python one.  The three without it are *object model* limits rather than defects:
#: a `dict` cannot hold one key twice and `None` is not subclassable, so no record field or
#: emitter change could rescue them.  A future entry with neither shape is a real Python-side
#: gap, and is the one to work from.
KNOWN_GAPS: dict[str, str] = {
    '6HB6': '(core) an end-of-line comment inside a flow collection is written from a trivia '
            'slot, so the separation run around it cannot be echoed',
    '7TMG': '(core) a `,` the source wrote after an own-line comment inside a flow collection '
            'is re-emitted before it',
    'CN3R': '(core) an anchored single-pair mapping inside a flow sequence (`&c c: d`) is '
            're-emitted with braces the source did not write',
    'CT4Q': '(core) an explicit `? key` inside a flow collection loses its `?`',
    'M5C3': '(core) a block-scalar header the source put on a line of its own below the '
            "node's tag is pulled up onto the tag's line",
    'M7A3': '(core) a `...` that ends a document with no content at all has no document of '
            'its own to hang on',
    '2JQS': 'two entries with an empty key are two entries with the *same* key, so the '
            'constructor raises `DuplicateKeyError` where the core keeps both',
    'X38W': 'an alias used as a key of the mapping its anchor is defined in (`{&a [x]: 1, '
            '*a : 2}`) is the same object as that key, so it is a duplicate key and raises. '
            'The space before the `:` is load-bearing: `:` is a legal anchor character, so '
            '`*a:` scans as an alias named `a:`',
    '6KGN': 'an anchor on a null (`a: &anchor`) survives on the parent, but an *alias* to it '
            'constructs to the one `None` singleton, which carries no identity to alias on',
}


def test_yaml_test_suite_round_trips_through_python(
    yamluna_roundtrip: Callable[[str], str],
) -> None:
    """**The headline number**, measured at the API a user holds."""
    cases = suite_cases()
    identical = 0
    failures: list[str] = []
    unexpected_pass: list[str] = []

    for case in cases:
        known = case.id in KNOWN_GAPS
        try:
            got = yamluna_roundtrip(case.yaml)
            ok = got == case.yaml
            detail = f'    out: {got!r}'
        except Exception as exc:  # a raise is a failure to round-trip, not a skip
            ok = False
            detail = f'    raised: {type(exc).__name__}: {exc}'
        if ok and known:
            unexpected_pass.append(case.id)
        elif ok:
            identical += 1
        elif not known:
            failures.append(f'  {case.id}\n    in:  {case.yaml!r}\n{detail}')

    print(
        f'\nyaml-test-suite through the Python API: {identical}/{len(cases)} cases '
        f'round-trip byte-identically ({len(KNOWN_GAPS)} known gaps)'
    )

    assert not unexpected_pass, f'now round-trip -- drop from KNOWN_GAPS: {unexpected_pass}'
    assert not failures, '{} suite cases do not round-trip:\n{}'.format(
        len(failures), '\n'.join(failures)
    )
    # The floor, read off KNOWN_GAPS rather than hardcoded: every case that is not excused
    # has to pass, so shrinking the list raises the bar without touching this line.
    assert identical >= len(cases) - len(KNOWN_GAPS)


def test_known_gaps_are_real_cases() -> None:
    """A `KNOWN_GAPS` id that no longer names a suite case is a stale excuse."""
    stale = KNOWN_GAPS.keys() - {case.id for case in suite_cases()}
    assert not stale, f'not suite cases: {sorted(stale)}'
    assert all(KNOWN_GAPS.values()), 'every gap needs a one-line cause'


def test_extraction_matches_the_rust_harness() -> None:
    """The two harnesses must read the same cases, or their scores mean nothing.

    308 is what `yaml_test_suite_round_trips_byte_for_byte` reports for the same directory;
    when the vendored suite moves, both numbers move and this is the one line to change.
    """
    cases = suite_cases()
    assert len(cases) == 308
    assert len({case.id for case in cases}) == len(cases), 'duplicate case ids'
    # Every stand-in is gone; a leftover marker means the table drifted from the Rust one.
    leftovers = {c for case in cases for c in case.yaml if c in '␣»←⇔↵∎'}
    assert not leftovers, f'un-decoded visual escapes: {leftovers}'


def test_the_record_seam_loses_nothing_over_the_suite() -> None:
    """`emit(parse(x))` through the records == `parse`-then-`emit` inside Rust, all 308.

    `test_bindings.py::test_the_record_path_matches_the_pure_rust_round_trip` asserts this
    per corpus file; this is the same assertion over the wider net, and it is what makes the
    subtraction above readable.  While it holds, the difference between the Rust score and
    the Python one is the *object model* (§4.1) and nothing else -- which is what the README
    claims, so it needs a gate rather than a measurement.  A field that stops crossing the
    seam breaks this before it reaches the headline number, and names the case.
    """
    yamluna = pytest.importorskip('yamluna')
    ext = pytest.importorskip(
        'yamluna._yamluna', reason='extension not built yet: maturin develop'
    )
    opts = yamluna._record.EmitOptions(preserve_quotes=True)

    lost = []
    for case in suite_cases():
        try:
            reference = ext._roundtrip_in_rust(case.yaml, opts)
            through_records = ext.emit(ext.parse(case.yaml, allow_duplicate_keys=True), opts)
        except Exception as exc:  # a raise on one side only is itself a seam loss
            lost.append(f'  {case.id}: {type(exc).__name__}: {exc}')
            continue
        if through_records != reference:
            lost.append(
                f'  {case.id}\n    rust:    {reference!r}\n'
                f'    records: {through_records!r}'
            )

    assert not lost, 'the record seam loses {} of 308 cases:\n{}'.format(
        len(lost), '\n'.join(lost)
    )
