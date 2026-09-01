"""Byte-for-byte round-tripping over `yaml-test-suite`, through the Python API.

`crates/yamluna-core/tests/proptest_roundtrip.rs` runs the 308 suite cases through parse
and emit inside Rust and scores them. This file runs the identical case list through
`YAML().load_all` and `YAML().dump_all`, the API a user actually holds, and scores it the
same way. The two numbers are directly comparable, so the difference between them is
exactly what the FFI seam and the Python object model lose.

That difference was once found by accident because nothing measured it: at commit
`8b05b39` the same 308 cases scored 302 in Rust and 202 here. This file is the gate that
makes a second accident impossible:

* every failing case is in `KNOWN_GAPS` with a one-line cause, and an unlisted failure
  fails the run;
* a listed case that starts passing also fails the run, so a fix cannot leave a stale
  excuse behind and progress cannot go unnoticed;
* the score is printed (`pytest -s`) and floored at `len(cases) - len(KNOWN_GAPS)`, so
  shrinking the list raises the bar by itself and no number is hardcoded.

Extraction is a port of `suite_cases()` in `proptest_roundtrip.rs`, down to the
visual-escape table and the id numbering, so "308 cases" means the same 308 sources in
both harnesses. `test_extraction_matches_the_rust_harness` guards that.

A raise counts as a failure rather than as a skip. A user who gets a traceback where
ruamel returns text has lost the round trip at least as thoroughly as one who gets other
bytes.

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

# The suite writes white space it wants you to see with visible stand-ins. Same table,
# same order, as `visual_to_raw` in `proptest_roundtrip.rs`, so both harnesses decode a
# case to the same bytes.
_VISUAL = (
    ('␣', ' '),
    ('»', '\t'),
    ('—', ''),  # part of the suite's tab line-continuation marker
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
    """Turns the suite's visible stand-ins back into the bytes they stand for.

    Args:
        yaml: A case's source as the suite file writes it, with stand-ins in place.

    Returns:
        The same source with every stand-in replaced by the character it names.
    """
    for pattern, replacement in _VISUAL:
        yaml = yaml.replace(pattern, replacement)
    return yaml


def suite_cases() -> list[SuiteCase]:
    """Reads every case in the suite that is expected to parse.

    A `fail: true` case and a `skip` case are dropped, and every field except `fail` is
    inherited from the previous case in the file, exactly as the Rust harness reads them.

    Returns:
        One `SuiteCase` per surviving case, in file order, ids numbered `stem-NN` when a
        file holds more than one case and `stem` when it holds one.

    Raises:
        AssertionError: The suite directory is empty, or a suite file does not hold a
            list of cases.
        YAMLError: A suite file is not loadable.
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


KNOWN_GAPS: dict[str, str] = {
    '2JQS': 'PERMANENT: an empty key is the null key, so `: a` above `: b` is two entries '
            'carrying the one key `None`, and a `dict` holds one of them; the suite tags '
            'the case `duplicate-key` itself.  Same shape as X38W with the alias taken '
            'away, so the same answer: `DuplicateKeyError` naming both positions.  Telling '
            'the two nulls apart needs the entry source position, and keying a `Mapping` on '
            'position would break `doc[None]` for every well-formed document to rescue an '
            'ill-formed one; special-casing the empty key alone would accept `a: 1` above '
            '`a: 2` next.  `allow_duplicate_keys=True` does not help either: the dump is '
            'written from the tree, which by then holds one entry.  ruamel raises the same '
            'error.  Pinned by '
            'test_constructor.py::test_two_empty_keys_are_one_null_key',
    'X38W': 'PERMANENT: an alias used as a key of the mapping its anchor is defined in '
            '(`{&a [x]: 1, *a : 2}`) is that key, one object reached twice, so the '
            'two entries carry one key and a `dict` holds one of them.  YAML requires a '
            "mapping's keys to be unique and an alias is the node it names, so the document "
            'is ill-formed; `DuplicateKeyError` is the answer rather than a workaround to '
            'find.  No wrapper helps: identity cannot separate an object from itself, and '
            'keying on source position instead would break `doc[key]` everywhere.  ruamel '
            'raises the same error; PyYAML never gets that far (`found unhashable key`).  '
            'Pinned by test_constructor.py::'
            'test_an_alias_to_a_key_of_its_own_mapping_is_a_duplicate.  The space before '
            'the `:` is load-bearing: `:` is a legal anchor character, so `*a:` scans as an '
            "alias named `a:`",
}
"""Suite cases that do not survive a `load_all` followed by a `dump_all`, and why.

Both are object model limits rather than defects, and both are marked `PERMANENT`: a
`CommentedMap` is a `dict`, so two keys that compare equal, or one key reached twice
through an alias, are one entry, and no record field or emitter change reaches that.

The Rust core round-trips both, and `KNOWN_GAPS` in
`crates/yamluna-core/tests/proptest_roundtrip.rs` is empty. Anything that appears here
without the `PERMANENT` shape is a real Python-side gap, a record slot that stopped
crossing the seam or a representer that reconstructs what it should echo, and is the one
to work from.
"""


def test_yaml_test_suite_round_trips_through_python(
    yamluna_roundtrip: Callable[[str], str],
) -> None:
    """The headline number, measured at the API a user holds."""
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
        # A raise is a failure to round-trip rather than a skip.
        except Exception as exc:
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

    assert not unexpected_pass, f'now round-trip, so drop from KNOWN_GAPS: {unexpected_pass}'
    assert not failures, '{} suite cases do not round-trip:\n{}'.format(
        len(failures), '\n'.join(failures)
    )
    # The floor is read off KNOWN_GAPS rather than hardcoded: every case that is not
    # excused has to pass, so shrinking the list raises the bar without touching this line.
    assert identical >= len(cases) - len(KNOWN_GAPS)


def test_known_gaps_are_real_cases() -> None:
    """A `KNOWN_GAPS` id that no longer names a suite case is a stale excuse."""
    stale = KNOWN_GAPS.keys() - {case.id for case in suite_cases()}
    assert not stale, f'not suite cases: {sorted(stale)}'
    assert all(KNOWN_GAPS.values()), 'every gap needs a one-line cause'


def test_extraction_matches_the_rust_harness() -> None:
    """The two harnesses must read the same cases, or their scores mean nothing.

    308 is what `yaml_test_suite_round_trips_byte_for_byte` reports for the same
    directory. When the vendored suite moves, both numbers move, and this is the one line
    to change.
    """
    cases = suite_cases()
    assert len(cases) == 308
    assert len({case.id for case in cases}) == len(cases), 'duplicate case ids'
    # Every stand-in is gone. A leftover marker means the table drifted from the Rust one.
    leftovers = {c for case in cases for c in case.yaml if c in '␣»←⇔↵∎'}
    assert not leftovers, f'un-decoded visual escapes: {leftovers}'


def test_the_record_seam_loses_nothing_over_the_suite() -> None:
    """Emitting the records Rust parsed matches parsing and emitting inside Rust, all 308.

    `test_bindings.py::test_the_record_path_matches_the_pure_rust_round_trip` asserts this
    per corpus file; this is the same assertion over the wider net, and it is what makes
    the subtraction above readable. While it holds, the difference between the Rust score
    and the Python one is the object model, the `CommentedMap` and `CommentedSeq` layer,
    and nothing else. The README claims that, so it needs a gate rather than a
    measurement. A field that stops crossing the seam breaks this before it reaches the
    headline number, and names the case.
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
        # A raise on one side only is itself a seam loss.
        except Exception as exc:
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
