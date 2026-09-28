#!/usr/bin/env python
"""Compares yamluna with the popular Python YAML libraries on round trips and speed.

Prints a Markdown report with two tables. The first counts how many files of the
round-trip corpus in `tests/corpus/` each library writes back byte for byte after a load
and a dump with no edits. The second times a complete load and dump of the four inputs
`bench/bench.py` generates.

Run it with the benchmark dependencies installed:

```bash
uv pip install --group bench
.venv/bin/python bench/libraries.py
.venv/bin/python bench/libraries.py --quick   # fewer repeats, for a smoke run
```

A library that is not installed is left out of the report. Each is called the way its
documentation suggests for loading and saving a file, with the settings named in
`LIBRARIES` and everything else at its default:

- yamluna and ruamel.yaml: `YAML()` with `preserve_quotes = True`, as in
  `tests/differential.py`.
- PyYAML: `safe_load_all` and `safe_dump_all` with `sort_keys=False` and
  `allow_unicode=True`, using the libyaml `CSafeLoader` and `CSafeDumper` when PyYAML was
  built with them, and the pure-Python classes as a second row.
- py-yaml12: `parse_yaml` and `format_yaml` with `multi=True`, plus the final line break
  `format_yaml` leaves off.
- StrictYAML: `dirty_load(text, allow_flow_style=True).as_yaml()`, its round-trip API.
  It rejects tags, anchors and several documents in one stream by design, and a file it
  rejects counts as not reproduced.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import statistics
import sys
import timeit
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'python'))

# The inputs and the report header come from `bench/bench.py`, loaded by path because
# `bench` on its own names this directory.
_spec = importlib.util.spec_from_file_location('bench_inputs', ROOT / 'bench' / 'bench.py')
assert _spec is not None
assert _spec.loader is not None
_bench: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bench)
header, inputs = _bench.header, _bench.inputs

if TYPE_CHECKING:
    from collections.abc import Callable

REPEATS = 5
CORPUS = ROOT / 'tests' / 'corpus'
# A Python dict keeps one of two equal keys, so no library can write this file back.
SKIP = {'key-duplicate.yaml'}


def _yaml_class(module: str) -> Callable[[str], str]:
    yaml_class: Any = importlib.import_module(module).YAML

    def round_trip(text: str) -> str:
        yaml = yaml_class()
        yaml.preserve_quotes = True
        out = io.StringIO()
        yaml.dump_all(list(yaml.load_all(text)), out)
        return out.getvalue()

    return round_trip


def _pyyaml(*, c: bool) -> Callable[[str], str]:
    yaml: Any = importlib.import_module('yaml')
    loader = yaml.CSafeLoader if c else yaml.SafeLoader
    dumper = yaml.CSafeDumper if c else yaml.SafeDumper
    if c and not yaml.__with_libyaml__:
        msg = 'PyYAML was built without libyaml'
        raise ImportError(msg)

    def round_trip(text: str) -> str:
        docs = list(yaml.load_all(text, Loader=loader))
        return yaml.dump_all(docs, Dumper=dumper, sort_keys=False, allow_unicode=True)

    return round_trip


def _yaml12() -> Callable[[str], str]:
    yaml12: Any = importlib.import_module('yaml12')

    def round_trip(text: str) -> str:
        return yaml12.format_yaml(yaml12.parse_yaml(text, multi=True), multi=True) + '\n'

    return round_trip


def _strictyaml() -> Callable[[str], str]:
    strictyaml: Any = importlib.import_module('strictyaml')

    def round_trip(text: str) -> str:
        return strictyaml.dirty_load(text, allow_flow_style=True).as_yaml()

    return round_trip


LIBRARIES: dict[str, Callable[[], Callable[[str], str]]] = {
    'yamluna': lambda: _yaml_class('yamluna'),
    'ruamel.yaml (round-trip)': lambda: _yaml_class('ruamel.yaml'),
    'PyYAML (libyaml C)': lambda: _pyyaml(c=True),
    'PyYAML (pure Python)': lambda: _pyyaml(c=False),
    'py-yaml12': _yaml12,
    'StrictYAML': _strictyaml,
}


def available() -> dict[str, Callable[[str], str]]:
    """Return a round-trip function for every library that imports."""
    found = {}
    for name, make in LIBRARIES.items():
        with contextlib.suppress(ImportError):
            found[name] = make()
    return found


def reproduces(round_trip: Callable[[str], str], text: str) -> bool:
    """Whether a load and dump with no edits gives back `text` exactly."""
    try:
        return round_trip(text) == text
    except Exception:  # noqa: BLE001 - a rejected file is simply not reproduced
        return False


def measure(fn: Callable[[], object], *, quick: bool) -> float:
    """Return the median seconds per call of `fn`, as `bench/bench.py` measures it."""
    timer = timeit.Timer(fn)
    n, _ = timer.autorange()
    return statistics.median(t / n for t in timer.repeat(3 if quick else REPEATS, n))


def section_corpus(libs: dict[str, Callable[[str], str]]) -> None:
    files = sorted(p for p in CORPUS.glob('*.yaml') if p.name not in SKIP)
    texts = [p.read_bytes().decode('utf-8') for p in files]
    print(f'## Round trips: {len(files)} corpus files loaded and saved unchanged\n')
    print('| Library | Files reproduced exactly |')
    print('| --- | ---: |')
    for name, round_trip in libs.items():
        count = sum(reproduces(round_trip, t) for t in texts)
        print(f'| {name} | {count} / {len(files)} |')
    print()


def section_speed(libs: dict[str, Callable[[str], str]], *, quick: bool) -> None:
    docs = inputs()
    names = list(docs)
    print('## Speed: one load and dump, milliseconds (lower is faster)\n')
    print('| Library | ' + ' | '.join(names) + ' |')
    print('| --- |' + ' ---: |' * len(names))
    for name, round_trip in libs.items():
        cells = []
        for doc in names:
            text = docs[doc]
            try:
                round_trip(text)
            except Exception:  # noqa: BLE001 - the library rejects this input
                cells.append('rejected')
                continue
            cells.append(f'{measure(lambda t=text, f=round_trip: f(t), quick=quick) * 1000:.2f}')
        print(f'| {name} | ' + ' | '.join(cells) + ' |')
    print()


def main(argv: list[str]) -> int:
    # ruamel.yaml warns about a reused anchor in one corpus file; the report is the point.
    warnings.simplefilter('ignore')
    libs = available()
    header(inputs())
    section_corpus(libs)
    section_speed(libs, quick='--quick' in argv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
