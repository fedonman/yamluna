"""`yaml-test-suite` through the Python API, interactively -- the companion to the gate.

`tests/test_suite_roundtrip.py` is the gate: it fails the suite when a case regresses or when
a `KNOWN_GAPS` entry starts passing.  This is the same measurement with a diff per case, for
working *on* a gap rather than gating on it, and it is the command the README quotes::

    python tests/suite_roundtrip.py            # the score, and every failing case
    python tests/suite_roundtrip.py --diff     # ... with in/out for each
    python tests/suite_roundtrip.py 26DV       # one case, always with its diff
    python tests/suite_roundtrip.py --rust     # ... and the Rust core's score, for the delta

The case list, the visual-escape table and the `KNOWN_GAPS` excuses all come from the gate,
so there is exactly one definition of "the 308 cases" and one list of what is excused.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_suite_roundtrip import KNOWN_GAPS, suite_cases

ROOT = Path(__file__).parents[1]


def round_trip(source: str) -> str:
    """`dump_all(load_all(source))` on one fresh `YAML`, configured as a user would."""
    from yamluna import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    text = yaml.dump_all(yaml.load_all(source))
    assert isinstance(text, str)
    return text


def rust_score() -> str:
    """The line the Rust harness prints for the same 308 cases."""
    proc = subprocess.run(
        ['cargo', 'test', '-p', 'yamluna-core', '--test', 'proptest_roundtrip',
         'yaml_test_suite_round_trips_byte_for_byte', '--', '--nocapture'],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    for line in proc.stdout.splitlines():
        if line.startswith('yaml-test-suite:'):
            return line
    return f'cargo failed:\n{proc.stdout}\n{proc.stderr}'


def main(argv: list[str]) -> int:
    show_diff = '--diff' in argv
    with_rust = '--rust' in argv
    only = [a for a in argv if not a.startswith('-')]

    cases = suite_cases()
    if only:
        cases = [c for c in cases if c.id in only]
        if not cases:
            print(f'no such case: {", ".join(only)}')
            return 1
        show_diff = True
    total = len(cases)  # after the filter, so `26DV` scores 1/1 rather than 1/308

    identical = 0
    failures: list[tuple[str, str, str]] = []
    for case in cases:
        try:
            got = round_trip(case.yaml)
        except Exception as exc:  # a raise is a failure to round-trip, not a skip
            failures.append((case.id, case.yaml, f'raised {type(exc).__name__}: {exc}'))
            continue
        if got == case.yaml:
            identical += 1
        else:
            failures.append((case.id, case.yaml, repr(got)))

    import yamluna

    print(f'yamluna {yamluna.__version__}, YAML().load_all -> .dump_all, preserve_quotes=True')
    print()
    print(f'python API : {identical}/{total} suite cases round-trip byte-identically')
    if with_rust:
        print(f'rust core  : {rust_score()}')
    print()
    for name, src, got in failures:
        print(f'  {name:<8} {KNOWN_GAPS.get(name, "")}')
        if show_diff:
            print(f'    in : {src!r}')
            print(f'    out: {got}')

    failing = {name for name, _, _ in failures}
    if only:
        return 1 if failing else 0
    unexpected = sorted(failing - set(KNOWN_GAPS))
    stale = sorted(set(KNOWN_GAPS) - failing)
    if unexpected:
        print(f'\nnot in KNOWN_GAPS, so this is a regression: {unexpected}')
    if stale:
        print(f'\nnow round-trip -- drop from KNOWN_GAPS: {stale}')
    return 1 if unexpected or stale else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
