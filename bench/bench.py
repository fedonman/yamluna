#!/usr/bin/env python
"""yamluna vs ruamel.yaml round-trip benchmark.

Run it::

    .venv/bin/python bench/bench.py              # the whole table
    .venv/bin/python bench/bench.py --quick      # fewer repeats, for a smoke run
    .venv/bin/python bench/bench.py --threads    # only the parallel-scaling section

Four inputs, each stressing a different part of the pipeline:

``config``
    A small hand-written config file -- the common case, where fixed overhead dominates.
``nested``
    A large, deeply nested document: many collections, few bytes per node.  This is where
    the Python object model (one ``CommentedMap``/``CommentedSeq`` per collection) costs
    the most.
``comments``
    A document that is mostly comments and blank lines -- trivia attachment, not parsing.
``scalars``
    A long flat run of scalars in every style.  Few collections, so the per-node Python
    cost is at its lowest and the scanner is at its most exposed.

The big three are generated here rather than committed, so the repository stays small and
the sizes are easy to change.

Both libraries get the same two lines of configuration, the ones the differential harness
uses (``tests/differential.py``)::

    yaml = YAML()
    yaml.preserve_quotes = True

Everything else is left at its default in both, ``width = 80`` included.

Timing is :mod:`timeit`: ``autorange`` picks a batch size that runs for ~0.2 s, then five
batches are taken and the *median* reported.  Median, not min: min flatters whichever
library has the twitchier allocator, and this is not a competition where that should help.
"""

from __future__ import annotations

import io
import os
import platform
import statistics
import sys
import textwrap
import timeit
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'python'))

import ruamel.yaml
import yamluna

REPEATS = 5


# ------------------------------------------------------------------------------------
# the four inputs
# ------------------------------------------------------------------------------------


CONFIG = textwrap.dedent("""\
    # Deployment settings for the staging cluster.
    # Owned by the platform team; see runbook/staging.md before editing.

    name: staging
    replicas: 3                     # bumped 2024-11 after the load test
    image: "registry.example.com/api:1.4.2"

    resources:
      # These are per-pod, not per-node.
      limits:
        cpu: "2"
        memory: 4Gi
      requests:
        cpu: 500m
        memory: 1Gi

    env:
      - name: LOG_LEVEL
        value: info
      - name: DATABASE_URL           # rotated by the secrets operator
        valueFrom:
          secretKeyRef:
            name: db
            key: url

    # Anything below here is only read by the canary job.
    canary:
      enabled: true
      weight: 0.05
      notes: |
        The canary shares the staging database.
        Do not enable it during a migration.
""")


def make_nested(depth: int = 6, breadth: int = 3, leaves: int = 4) -> str:
    """A balanced tree `depth` levels deep, `breadth` mappings wide, `leaves` scalars each."""
    out: list[str] = ['# A deeply nested document.', '']

    def rec(level: int, indent: str, tag: str) -> None:
        pad = indent + '  '
        if level == 0:
            for i in range(leaves):
                out.append(f'{pad}leaf_{tag}_{i}: value-{tag}-{i}')
            return
        for b in range(breadth):
            out.append(f'{pad}branch_{tag}_{b}:')
            rec(level - 1, pad, f'{tag}{b}')
        out.append(f'{pad}items_{tag}:')
        for i in range(leaves):
            out.append(f'{pad}  - id: {i}')
            out.append(f'{pad}    label: item-{tag}-{i}')

    out.append('root:')
    rec(depth, '', 'r')
    return '\n'.join(out) + '\n'


def make_comments(entries: int = 900) -> str:
    """A document where roughly three lines in four are trivia."""
    out: list[str] = [
        '# Top-of-file banner.',
        '# Two lines of it, so the document-leading slot has something to hold.',
        '',
    ]
    for i in range(entries):
        out.append(f'# ----- section {i} -----')
        out.append(f'# Why key_{i} is set the way it is, at some length,')
        out.append('# spilling over onto a second line of prose.')
        out.append(f'key_{i}: value_{i}    # and an end-of-line note')
        if i % 3 == 0:
            out.append('')
    out.append('# A trailing comment, after the last node.')
    return '\n'.join(out) + '\n'


def make_scalars(count: int = 1200) -> str:
    """A flat sequence of scalars in every style the emitter has to re-analyse."""
    out: list[str] = ['# Scalars, one style after another.', 'values:']
    styles: list[Callable[[int], str]] = [
        lambda i: f'plain scalar number {i}',
        lambda i: f"'single quoted {i}'",
        lambda i: f'"double \\"quoted\\" {i}\\n"',
        lambda i: str(i * 7919),
        lambda i: f'{i}.{i}e-3',
        lambda i: 'true' if i % 2 else 'false',
        lambda i: f'2024-0{i % 9 + 1}-15T0{i % 9 + 1}:00:00Z',
        lambda i: f'0x{i:04x}',
        lambda i: 'null',
        lambda i: f'a-plain-scalar-that-is-long-enough-to-matter-{i}-' + 'x' * 40,
    ]
    for i in range(count):
        out.append(f'  - {styles[i % len(styles)](i)}')
    out.append('block:')
    for i in range(count // 20):
        out.append(f'  text_{i}: |')
        out.append(f'    A literal block scalar, entry {i}.')
        out.append('    It has a second line so the folding logic is exercised.')
    return '\n'.join(out) + '\n'


def inputs() -> dict[str, str]:
    return {
        'config': CONFIG,
        'nested': make_nested(),
        'comments': make_comments(),
        'scalars': make_scalars(),
    }


# ------------------------------------------------------------------------------------
# the two libraries, configured identically
# ------------------------------------------------------------------------------------


def yamluna_rt() -> yamluna.YAML:
    y = yamluna.YAML()
    y.preserve_quotes = True
    return y


def ruamel_rt() -> ruamel.yaml.YAML:
    y = ruamel.yaml.YAML()
    y.preserve_quotes = True
    return y


def ops(make: Callable[[], object], text: str) -> dict[str, Callable[[], object]]:
    """The three timed operations for one library on one document.

    ``dump`` is timed against an object loaded once up front, so it measures emitting and
    not emitting-plus-loading; ``load+dump`` is the whole round trip.
    """
    yaml = make()
    loaded = yaml.load(text)

    def load() -> object:
        return make().load(text)

    def dump() -> object:
        return make().dump(loaded, io.StringIO())

    def both() -> object:
        y = make()
        return y.dump(y.load(text), io.StringIO())

    return {'load': load, 'dump': dump, 'load+dump': both}


def measure(fn: Callable[[], object], quick: bool) -> float:
    """Median seconds per call."""
    timer = timeit.Timer(fn)
    n, _ = timer.autorange()
    repeats = 3 if quick else REPEATS
    return statistics.median(t / n for t in timer.repeat(repeats, n))


def roundtrips(make: Callable[[], object], text: str) -> bool:
    yaml = make()
    out = io.StringIO()
    yaml.dump(yaml.load(text), out)
    return out.getvalue() == text


# ------------------------------------------------------------------------------------
# sections
# ------------------------------------------------------------------------------------


def section_compare(docs: dict[str, str], quick: bool) -> None:
    print('## load / dump / round trip\n')
    print('| document | size | operation | yamluna | ruamel | ratio |')
    print('| --- | ---: | --- | ---: | ---: | ---: |')
    for name, text in docs.items():
        mine = ops(yamluna_rt, text)
        theirs = ops(ruamel_rt, text)
        size = f'{len(text) / 1024:.0f} KiB'
        for op in ('load', 'dump', 'load+dump'):
            a = measure(mine[op], quick)
            b = measure(theirs[op], quick)
            ratio = b / a
            verdict = f'{ratio:.1f}x faster' if ratio >= 1 else f'{1 / ratio:.1f}x SLOWER'
            print(
                f'| `{name}` | {size} | {op} '
                f'| {a * 1000:.2f} ms | {b * 1000:.2f} ms | {verdict} |'
            )
            size = ''
    print()
    print('| document | yamluna byte-identical | ruamel byte-identical |')
    print('| --- | --- | --- |')
    for name, text in docs.items():
        print(
            f'| `{name}` | {"yes" if roundtrips(yamluna_rt, text) else "no"} '
            f'| {"yes" if roundtrips(ruamel_rt, text) else "no"} |'
        )
    print()


def section_layers(docs: dict[str, str], quick: bool) -> None:
    """Where the round-trip time actually goes, in three layers of the same operation.

    ``Rust only``
        ``_yamluna._roundtrip_in_rust`` -- the scanner, the loader, trivia attachment and
        the emitter, with no Python object ever built.  This is the floor.
    ``+ FFI records``
        ``emit(parse(text))`` -- the same work plus building the flat ``Node``/``Trivia``
        records on the way out and reading them back on the way in.
    ``+ object model``
        ``YAML.dump(YAML.load(text))`` -- the whole library: the constructor builds a
        ``CommentedMap``/``CommentedSeq`` per collection and a scalar subclass per scalar,
        and the representer takes them apart again.

    A criterion bench in ``crates/yamluna-core/benches/`` would measure the first column
    and nothing else; measuring it from here instead means all three columns come off the
    same input, in the same process, on the same clock, so the differences between them
    are the attribution rather than an apples-to-oranges comparison.
    """
    from yamluna import _yamluna

    opts = yamluna_rt()._emit_options()

    print('## where a yamluna round trip spends its time\n')
    print('| document | Rust only | + FFI records | + object model | object model share |')
    print('| --- | ---: | ---: | ---: | ---: |')
    for name, text in docs.items():
        rust = measure(lambda t=text: _yamluna._roundtrip_in_rust(t, opts), quick)
        records = measure(lambda t=text: _yamluna.emit(_yamluna.parse(t), opts), quick)
        full = measure(ops(yamluna_rt, text)['load+dump'], quick)
        print(
            f'| `{name}` | {rust * 1000:.2f} ms | {records * 1000:.2f} ms '
            f'| {full * 1000:.2f} ms | {(full - records) / full:.0%} |'
        )
    print()


def parallel(work: Callable[[], object], threads: int, total: int) -> float:
    """Wall seconds to run `work` `total` times spread over `threads` threads."""
    per = total // threads
    start = timeit.default_timer()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(lambda: [work() for _ in range(per)]) for _ in range(threads)]
        for f in futures:
            f.result()
    return timeit.default_timer() - start


def section_threads(docs: dict[str, str], quick: bool) -> None:
    """Parallel loads -- the thing ruamel cannot do at all.

    ``yamluna._yamluna.parse`` runs the scanner, the loader and the trivia attachment
    inside ``py.detach``, so those run genuinely in parallel.  Building the ``Node``
    records, and everything the constructor does on top, is Python object creation and
    holds the GIL.  So ``parse`` scales and ``YAML.load`` scales partly; ruamel is pure
    Python and scales not at all.  All three numbers are below, measured, including the
    one that does not flatter us.
    """
    from yamluna import _yamluna

    text = docs['nested']
    counts = [1, 2, 4, 8] if not quick else [1, 4]
    total = 4 * max(counts)

    workloads = {
        'yamluna `_yamluna.parse`': lambda: _yamluna.parse(text),
        'yamluna `YAML.load`': lambda: yamluna_rt().load(text),
        'ruamel `YAML.load`': lambda: ruamel_rt().load(text),
    }

    print(
        f'## parallel loads ({len(text) / 1024:.0f} KiB `nested`, '
        f'{total} loads spread over N threads)\n'
    )
    heads = ' | '.join(f'{n} thread{"s" * (n > 1)}' for n in counts)
    print(f'| workload | {heads} | speedup at {counts[-1]} |')
    print('| --- |' + ' ---: |' * (len(counts) + 1))
    for label, work in workloads.items():
        # Two passes, ascending then descending, and the faster of the two per thread count.
        # One pass measures whichever count runs first on a cold, un-throttled CPU and the
        # rest on a hot one, which moves the reported speedup by ~20% on a laptop -- in
        # whichever direction the ordering happens to favour.
        up = [parallel(work, n, total) for n in counts]
        down = [parallel(work, n, total) for n in reversed(counts)]
        times = [min(a, b) for a, b in zip(up, reversed(down), strict=True)]
        cells = ' | '.join(f'{t:.2f} s' for t in times)
        print(f'| {label} | {cells} | {times[0] / times[-1]:.2f}x |')
    print()


def _cpu_count() -> int:
    getaffinity = getattr(os, 'sched_getaffinity', None)
    return len(getaffinity(0)) if getaffinity else (os.cpu_count() or 1)


def header(docs: dict[str, str]) -> None:
    uname = platform.uname()
    print('# yamluna benchmark\n')
    print(f'- machine: {uname.system} {uname.release}, {uname.machine}, {_cpu_count()} hw threads')
    print(f'- cpu: {_cpu_model()}')
    print(f'- python: {platform.python_version()} ({platform.python_implementation()})')
    print(f'- yamluna: {yamluna.__version__}')
    print(f'- ruamel.yaml: {".".join(str(p) for p in ruamel.yaml.version_info[:3])}')
    sizes = ', '.join(f'{n} {len(t) / 1024:.0f} KiB' for n, t in docs.items())
    print(f'- inputs: {sizes}')
    print()


def _cpu_model() -> str:
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or 'unknown'


def main(argv: list[str]) -> int:
    quick = '--quick' in argv
    docs = inputs()
    header(docs)
    only_threads = '--threads' in argv
    if not only_threads:
        section_compare(docs, quick)
        section_layers(docs, quick)
    section_threads(docs, quick)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
