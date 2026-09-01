#!/usr/bin/env python
"""Times a yamluna round trip against the same round trip in ruamel.yaml.

Prints a Markdown report: a load, dump and round-trip table for each document, a
byte-identical check, an attribution of yamluna's own time across its three layers, and a
parallel-load scaling table.

Run it:

```bash
.venv/bin/python bench/bench.py              # the whole report
.venv/bin/python bench/bench.py --quick      # fewer repeats, for a smoke run
.venv/bin/python bench/bench.py --threads    # only the parallel-scaling section
```

Four inputs, each stressing a different part of the pipeline:

- `config`: a small hand-written config file, the common case, where fixed overhead
  dominates.
- `nested`: a large, deeply nested document, many collections and few bytes per node.
  This is where the Python object model, one `CommentedMap` or `CommentedSeq` per
  collection, costs the most.
- `comments`: a document that is mostly comments and blank lines, so it measures trivia
  attachment rather than parsing.
- `scalars`: a long flat run of scalars in every style. Few collections, so the per-node
  Python cost is at its lowest and the scanner is at its most exposed.

The big three are generated at run time and not committed, so the repository stays small
and the sizes are easy to change.

Both libraries get the same two lines of configuration, the ones the differential harness
in `tests/differential.py` uses:

```python
yaml = YAML()
yaml.preserve_quotes = True
```

Everything else is left at its default in both, `width = 80` included.

Timing goes through `timeit`: `autorange` picks a batch size that runs for about 0.2 s,
then five batches are taken and the median of them is reported.
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
    """Builds the `nested` input: a balanced tree of mappings.

    Args:
        depth: How many levels of nested mappings to emit.
        breadth: How many child mappings each level holds.
        leaves: How many scalar entries sit at the bottom of each mapping.

    Returns:
        The YAML text of the tree.
    """
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
    """Builds the `comments` input, where roughly three lines in four are trivia.

    Args:
        entries: How many commented key and value entries to emit.

    Returns:
        The YAML text of the document.
    """
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
    """Builds the `scalars` input: a flat sequence of scalars in every style.

    The styles are the ones the emitter has to re-analyse before it can write a scalar
    back out plain.

    Args:
        count: How many sequence entries to emit. One literal block scalar is added
            for every 20 of them.

    Returns:
        The YAML text of the document.
    """
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
    """Returns the four benchmark documents, keyed by name."""
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
    """Returns a fresh yamluna round-trip loader with `preserve_quotes` on."""
    y = yamluna.YAML()
    y.preserve_quotes = True
    return y


def ruamel_rt() -> ruamel.yaml.YAML:
    """Returns a fresh ruamel.yaml round-trip loader with `preserve_quotes` on."""
    y = ruamel.yaml.YAML()
    y.preserve_quotes = True
    return y


def ops(make: Callable[[], object], text: str) -> dict[str, Callable[[], object]]:
    """Builds the three timed operations for one library on one document.

    `dump` is timed against an object loaded once up front, so it measures emitting on
    its own rather than emitting plus loading. `load+dump` is the whole round trip.

    Args:
        make: A factory returning a configured loader, `yamluna_rt` or `ruamel_rt`.
        text: The document to load and dump.

    Returns:
        A mapping from operation name (`load`, `dump`, `load+dump`) to a callable that
        performs that operation once and takes no arguments.
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
    """Times `fn` and returns the median seconds per call.

    Args:
        fn: The operation to time. It takes no arguments.
        quick: Take three batches instead of five.

    Returns:
        The median per-call time, in seconds.
    """
    timer = timeit.Timer(fn)
    n, _ = timer.autorange()
    repeats = 3 if quick else REPEATS
    # The median rather than the minimum: the minimum flatters whichever library has
    # the twitchier allocator, and this is not a competition where that should help.
    return statistics.median(t / n for t in timer.repeat(repeats, n))


def roundtrips(make: Callable[[], object], text: str) -> bool:
    """Reports whether `make()` dumps `text` back byte for byte after loading it."""
    yaml = make()
    out = io.StringIO()
    yaml.dump(yaml.load(text), out)
    return out.getvalue() == text


# ------------------------------------------------------------------------------------
# sections
# ------------------------------------------------------------------------------------


def section_compare(docs: dict[str, str], quick: bool) -> None:
    """Prints the load, dump and round-trip table, then a byte-identical check.

    Args:
        docs: The benchmark documents, keyed by name.
        quick: Take fewer timing batches.
    """
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
    """Prints where a yamluna round trip spends its time, in three layers.

    Each column is the same work with more of the library stacked on top:

    - `Rust only`: `_yamluna._roundtrip_in_rust`, which is the scanner, the loader,
      trivia attachment and the emitter, with no Python object ever built. This is the
      floor.
    - `+ FFI records`: `emit(parse(text))`, the same work plus building the flat `Node`
      and `Trivia` records on the way out and reading them back on the way in.
    - `+ object model`: `YAML.dump(YAML.load(text))`, the whole library. The
      constructor builds a `CommentedMap` or `CommentedSeq` per collection and a scalar
      subclass per scalar, and the representer takes them apart again.

    The last column of the table is the share of the round trip spent above the FFI
    records, which is what the Python object model costs.

    Args:
        docs: The benchmark documents, keyed by name.
        quick: Take fewer timing batches.
    """
    # A criterion bench under crates/yamluna-core/benches/ would measure the first
    # column and nothing else. Measuring all three from here puts them on the same
    # input, in the same process, on the same clock, so the differences between the
    # columns are the attribution rather than a comparison of unlike runs.
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
    """Runs `work` `total` times over `threads` threads and returns the wall time.

    Args:
        work: The operation to run. It takes no arguments.
        threads: How many worker threads to spread the calls over.
        total: How many calls in all, rounded down to a multiple of `threads`.

    Returns:
        Wall-clock seconds for the whole run.
    """
    per = total // threads
    start = timeit.default_timer()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(lambda: [work() for _ in range(per)]) for _ in range(threads)]
        for f in futures:
            f.result()
    return timeit.default_timer() - start


def section_threads(docs: dict[str, str], quick: bool) -> None:
    """Prints how load throughput scales with thread count, for both libraries.

    `yamluna._yamluna.parse` runs the scanner, the loader and the trivia attachment
    inside `py.detach`, so that work runs genuinely in parallel. Building the `Node`
    records, and everything the constructor does on top, is Python object creation and
    holds the GIL. So `parse` scales, `YAML.load` scales partly, and ruamel is pure
    Python and does not scale at all. All three are printed, including the one that
    does not flatter yamluna.

    Args:
        docs: The benchmark documents, keyed by name. Only `nested` is timed.
        quick: Measure 1 and 4 threads instead of 1, 2, 4 and 8.
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
        # Two passes, ascending then descending, keeping the faster of the two per
        # thread count. A single pass measures whichever count runs first on a cold,
        # un-throttled CPU and the rest on a hot one, which moves the reported speedup
        # by about 20% on a laptop, in whichever direction the ordering happens to
        # favour.
        up = [parallel(work, n, total) for n in counts]
        down = [parallel(work, n, total) for n in reversed(counts)]
        times = [min(a, b) for a, b in zip(up, reversed(down), strict=True)]
        cells = ' | '.join(f'{t:.2f} s' for t in times)
        print(f'| {label} | {cells} | {times[0] / times[-1]:.2f}x |')
    print()


def _cpu_count() -> int:
    """Returns the number of hardware threads this process is allowed to run on."""
    getaffinity = getattr(os, 'sched_getaffinity', None)
    return len(getaffinity(0)) if getaffinity else (os.cpu_count() or 1)


def header(docs: dict[str, str]) -> None:
    """Prints the machine, interpreter, library versions and input sizes.

    Args:
        docs: The benchmark documents, keyed by name.
    """
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
    """Returns the CPU model name, read from /proc/cpuinfo where that is readable.

    Returns:
        The model name, or `platform.processor()`, or `'unknown'` when neither is
        available.
    """
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or 'unknown'


def main(argv: list[str]) -> int:
    """Runs the sections selected by `argv` and prints the report to stdout.

    Args:
        argv: Command-line arguments. `--quick` takes fewer timing batches and
            `--threads` skips everything but the parallel-scaling section.

    Returns:
        The process exit status, always 0.
    """
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
