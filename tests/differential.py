"""The ruamel.yaml oracle, and yamluna's own score, for the acceptance corpus.

Importable, since pytest uses the helpers, and runnable:

```bash
python tests/differential.py               # the whole corpus, as a table
python tests/differential.py --diff        # the same, plus a unified diff per failure
python tests/differential.py comment-eol   # one file, with its diff
python tests/differential.py --ruamel      # what ruamel changed, rather than yamluna
```

What this measures is whether a round-trip YAML library reads a corpus file and writes
back the exact same bytes. Both libraries are measured the same way and the table prints
one column each. yamluna is held to byte-identity on every one of these files, a stricter
bar than ruamel meets, and each place the two disagree is a deliberate fix rather than an
accident. The rows that say "no" are where being bug-compatible with ruamel is the wrong
goal, measured rather than assumed.

The ruamel configuration is the ordinary round-trip recipe:

```python
yaml = YAML()            # typ='rt'
yaml.preserve_quotes = True
```

and yamluna is given exactly the same two lines. Everything else is left at its default
in both, including `width = 80`, so a refolded long line shows up as a failure. That is a
real default-configuration behaviour rather than a rigged one.

The byte-identity headline is over 40 round-trippable files.
`tests/corpus/key-duplicate.yaml` is the forty-first and is scored on something else: it
writes `a: 1` and later `a: 3`, a mapping keeps one of two equal keys, so no dict-backed
API can write those bytes back. "Does it round-trip" is a question neither library can
answer yes to, and counting it as a round-trip failure marked a correct refusal as a
defect. What the file specifies is behaviour, so `check_duplicate_keys` measures that
instead, the stem is listed in `BEHAVIOUR_ONLY`, and it gets a row of its own.
"""

from __future__ import annotations

import difflib
import io
import os
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML, version_info

if TYPE_CHECKING:
    from collections.abc import Callable

CORPUS_DIR = Path(__file__).parent / 'corpus'
# ruamel builds `version_info` out of an untyped `dict(...)`, so a checker sees the union
# of that dict's value types rather than the `(0, 19, 1)` tuple it holds.
_VERSION = version_info[:3]  # ty: ignore[not-subscriptable, invalid-argument-type]
RUAMEL_VERSION = '.'.join(str(part) for part in _VERSION)

# --------------------------------------------------------------------------
# ruamel helpers
# --------------------------------------------------------------------------


def ruamel_rt(sequence: int = 2, offset: int = 0) -> YAML:
    """Build a round-trip `YAML` configured the way this harness measures it.

    Args:
        sequence: Block-sequence indent. The default is ruamel's own. Pass 4 with an
            offset of 2 to give ruamel the indentation style most of this corpus is
            written in, which is what `--seq-indent` does.
        offset: Where the `-` sits inside that indent, again ruamel's own default.

    Returns:
        A ruamel `YAML` in round-trip mode with `preserve_quotes` on and the requested
        indentation.

    """
    yaml = YAML()  # typ='rt'
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=sequence, offset=offset)
    return yaml


def load_with_ruamel(text: str, yaml: YAML | None = None) -> list[Any]:
    """Load every document in the source.

    Args:
        text: The YAML source.
        yaml: The instance to load through. A fresh `ruamel_rt()` when omitted.

    Returns:
        The documents, always as a list, even for a source holding one document.

    Raises:
        YAMLError: ruamel refused the source. A duplicate key raises unless the caller
            set `allow_duplicate_keys` on the instance it passed in.

    """
    return list((yaml or ruamel_rt()).load_all(text))


def dump_with_ruamel(data: list[Any], yaml: YAML | None = None) -> str:
    """Dump a list of documents back to a string.

    Args:
        data: One entry per document, in stream order.
        yaml: The instance to dump through. A fresh `ruamel_rt()` when omitted.

    Returns:
        The emitted stream.

    Raises:
        YAMLError: ruamel could not represent one of the objects.

    """
    buf = io.StringIO()
    (yaml or ruamel_rt()).dump_all(data, buf)
    return buf.getvalue()


def roundtrip_with_ruamel(
    text: str,
    sequence: int = 2,
    offset: int = 0,
    *,
    allow_duplicate_keys: bool = False,
) -> str:
    """Load then dumps through one ruamel instance, the way a user would.

    Args:
        text: The YAML source.
        sequence: Block-sequence indent, passed to `ruamel_rt`.
        offset: Dash offset inside that indent, passed to `ruamel_rt`.
        allow_duplicate_keys: Accept a mapping with two equal keys instead of raising.

    Returns:
        What ruamel writes back for that source.

    Raises:
        YAMLError: ruamel refused the source or could not represent what it loaded.

    """
    yaml = ruamel_rt(sequence, offset)
    yaml.allow_duplicate_keys = allow_duplicate_keys
    return dump_with_ruamel(load_with_ruamel(text, yaml), yaml)


# --------------------------------------------------------------------------
# yamluna, measured identically
# --------------------------------------------------------------------------


def roundtrip_with_yamluna(text: str, *, allow_duplicate_keys: bool = False) -> str:
    """Load then dumps through one `yamluna.YAML`, the way a user would.

    Args:
        text: The YAML source.
        allow_duplicate_keys: Accept a mapping with two equal keys instead of raising.

    Returns:
        What yamluna writes back for that source.

    Raises:
        YAMLError: yamluna refused the source or could not represent what it loaded.
        ImportError: The Rust extension is not built.

    """
    import yamluna

    yaml = yamluna.YAML()  # typ='rt'
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = allow_duplicate_keys
    buf = io.StringIO()
    yaml.dump_all(list(yaml.load_all(text)), buf)
    return buf.getvalue()


def load_with_yamluna(text: str, *, allow_duplicate_keys: bool = False) -> Any:
    """Load one document the way `roundtrip_with_yamluna` loads it.

    Args:
        text: The YAML source.
        allow_duplicate_keys: Accept a mapping with two equal keys instead of raising.

    Returns:
        The first document's root object, or `None` for a document with no content.

    Raises:
        YAMLError: yamluna refused the source.
        ImportError: The Rust extension is not built.

    """
    import yamluna

    yaml = yamluna.YAML()  # typ='rt'
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = allow_duplicate_keys
    return yaml.load(text)


def load_one_with_ruamel(text: str, *, allow_duplicate_keys: bool = False) -> Any:
    """Load one document through ruamel, on the ordinary round-trip recipe.

    Args:
        text: The YAML source.
        allow_duplicate_keys: Accept a mapping with two equal keys instead of raising.

    Returns:
        The first document's root object, or `None` for a document with no content.

    Raises:
        YAMLError: ruamel refused the source.

    """
    yaml = ruamel_rt()
    yaml.allow_duplicate_keys = allow_duplicate_keys
    return yaml.load(text)


def read_corpus_file(path: Path) -> str:
    """Read a corpus file as text without touching its newlines or its BOM.

    Args:
        path: The file to read.

    Returns:
        The bytes decoded as UTF-8, with CRLF and a leading BOM left in place.

    Raises:
        UnicodeDecodeError: The file is not valid UTF-8.
        OSError: The file cannot be read.

    """
    return path.read_bytes().decode('utf-8')


def corpus_files() -> list[Path]:
    """Every `tests/corpus/*.yaml` path, sorted, so runs are comparable."""
    return sorted(CORPUS_DIR.glob('*.yaml'))


BEHAVIOUR_ONLY: set[str] = {'key-duplicate'}
"""Corpus stems scored on behaviour rather than on bytes, and left out of the headline.

Only `key-duplicate`, and only because byte-identity is not a question that file has an
answer to: it writes `a: 1` and later `a: 3`, a mapping keeps one of two equal keys, so
every dict-backed library writes back fewer lines than it read. The file's subject is
what a library does about that: refuse by default, and when told to allow duplicates,
say so and let the last one win. `check_duplicate_keys` measures those three things.
"""


# --------------------------------------------------------------------------
# one file
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Result:
    """What one library did to one corpus file."""

    path: Path
    ok: bool
    """True when the library's load followed by dump is byte-identical to the input."""
    summary: str
    """One line naming what changed. Empty when `ok`."""
    diff: str
    """Unified diff from the input to the library's output. Empty when `ok`."""
    error: str | None = None
    """Exception type and message, when the library refused the file outright."""

    @property
    def name(self) -> str:
        """The corpus file's stem, which is the id it is reported under."""
        return self.path.stem


def check_file(
    path: Path,
    sequence: int = 2,
    offset: int = 0,
    roundtrip: Callable[..., str] | None = None,
    label: str = 'ruamel',
) -> Result:
    """Round-trips one corpus file through a library and reports what changed.

    An exception from the library is a result rather than a failure here: it becomes a
    `Result` that is not `ok`, carrying the exception type and message in `error`.

    Args:
        path: The corpus file to measure.
        sequence: Block-sequence indent, used only by the default ruamel round trip.
        offset: Dash offset inside that indent, same.
        roundtrip: The round trip to measure. Takes the source and the keyword options,
            and returns what the library writes back. ruamel's when omitted.
        label: The name to put on the right-hand side of the diff.

    Returns:
        A `Result` for that file, with `ok` set when the output is the input byte for
        byte, and `summary` naming what changed when it is not.

    Raises:
        UnicodeDecodeError: The corpus file is not valid UTF-8.
        OSError: The corpus file cannot be read.

    """
    if roundtrip is None:

        def roundtrip(text: str, **options: bool) -> str:
            return roundtrip_with_ruamel(text, sequence, offset, **options)

    source = read_corpus_file(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            output = roundtrip(source)
        # Whatever the library under test raises is the measurement, so nothing is narrowed.
        except Exception as exc:  # noqa: BLE001
            first = str(exc).strip().splitlines()
            error = f'{type(exc).__name__}: {first[0] if first else ""}'.strip()
            return Result(path, ok=False, summary=f'raises {error}', diff='', error=error)
    warned = sorted({w.category.__name__ for w in caught})

    if output == source:
        return Result(
            path, ok=True, summary=f'(warns {", ".join(warned)})' if warned else '', diff=''
        )
    notes = summarize(source, output)
    if warned:
        notes = f'{notes}; warns {", ".join(warned)}'
    return Result(path, ok=False, summary=notes, diff=unified(source, output, label))


def _last_key_won(data: Any) -> bool:
    """Report whether every duplicate in `tests/corpus/key-duplicate.yaml` kept its last value."""
    # Each key is spelled out rather than derived, because "is this key a duplicate" is a
    # parser's job and not a regex's: `seq_items_are_not_keys` holds two `dup:` lines in
    # two different mappings, which are not duplicates at all, so both items survive.
    try:
        return (
            data['a'] == 3
            and data['nested']['x'] == 4
            and data['flow']['k'] == 2
            and data['quoted'] == 2
            and len(data['seq_items_are_not_keys']) == 2
        )
    except (KeyError, IndexError, TypeError):
        return False


def check_duplicate_keys(load: Callable[..., Any]) -> Result:
    """Score `tests/corpus/key-duplicate.yaml` on behaviour instead of on bytes.

    The three things that file specifies, in order: refuse duplicates by default; when
    told to allow them, say so rather than losing data silently; and keep the last of
    each duplicated pair. All three, or the row is a `no`. The point of the row is that
    "cannot round-trip" is a verdict on none of them.

    Args:
        load: Loads one document. Takes the source and an `allow_duplicate_keys` keyword.

    Returns:
        A `Result` whose `ok` is True only when all three hold, and whose `summary` says
        what the library did in each of the two runs.

    Raises:
        UnicodeDecodeError: The corpus file is not valid UTF-8.
        OSError: The corpus file cannot be read.

    """
    path = CORPUS_DIR / 'key-duplicate.yaml'
    source = read_corpus_file(path)
    try:
        load(source)  # allow_duplicate_keys=False, the default
        refuses, notes = False, ['accepts duplicates by default']
    # What the library raises for a duplicate key is the thing being measured.
    except Exception as exc:  # noqa: BLE001
        refuses, notes = True, [f'raises {type(exc).__name__} by default']

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            data = load(source, allow_duplicate_keys=True)
        # Same again: a raise here is a row in the table rather than a harness failure.
        except Exception as exc:  # noqa: BLE001
            error = f'{type(exc).__name__}: {str(exc).strip().splitlines()[0]}'
            notes.append(f'still raises {type(exc).__name__} when allowed')
            return Result(path, ok=False, summary='; '.join(notes), diff='', error=error)
    warned = sorted({w.category.__name__ for w in caught})
    kept = 'the last key wins' if _last_key_won(data) else 'the first key wins'
    told = f'warns {", ".join(warned)}' if warned else 'warns nothing'
    notes.append(f'when allowed, {told} and {kept}')
    return Result(path, refuses and bool(warned) and _last_key_won(data), '; '.join(notes), '')


def unified(source: str, output: str, label: str = 'ruamel') -> str:
    """Build a unified diff with the invisible characters made visible.

    Args:
        source: The corpus file's text.
        output: What the library wrote back.
        label: The name for the right-hand file in the diff header.

    Returns:
        The diff as one string, empty when the two texts are equal.

    """
    return ''.join(
        difflib.unified_diff(
            [_visible(line) + '\n' for line in source.split('\n')],
            [_visible(line) + '\n' for line in output.split('\n')],
            fromfile='input',
            tofile=label,
            lineterm='\n',
        )
    )


def _visible(line: str) -> str:
    """One line with its invisible characters replaced by named markers."""
    # Markers rather than backslash escapes, so a literal TAB in the file cannot be read
    # as the two characters someone typed.
    return (
        line.replace('\r', '<CR>')
        .replace('\t', '<TAB>')
        .replace('\ufeff', '<BOM>')
        .replace('\xa0', '<NBSP>')
    )


# --------------------------------------------------------------------------
# "what changed", in one line
# --------------------------------------------------------------------------


def _comment_lines(text: str) -> int:
    """Count the lines that are nothing but a comment."""
    return sum(1 for line in text.split('\n') if line.lstrip().startswith('#'))


def _eol_comments(text: str) -> int:
    """Count the lines that hold a `#` after something else."""
    return sum(1 for line in text.split('\n') if '#' in line and not line.lstrip().startswith('#'))


def _blank_lines(text: str) -> int:
    """Count the blank lines, ignoring what follows the final line break."""
    return sum(1 for line in text.split('\n')[:-1] if not line.strip())


def _blank_runs(text: str) -> int:
    """Count the runs of consecutive blank lines, so a moved gap is visible."""
    runs, in_run = 0, False
    for line in text.split('\n')[:-1]:
        blank = not line.strip()
        runs += blank and not in_run
        in_run = blank
    return runs


# One independent check per kind of change, each two or three lines; splitting them into
# separate functions would lengthen this rather than shorten it.
def summarize(source: str, output: str) -> str:  # noqa: C901, PLR0912, PLR0915
    """Describe in one line how the library's output differs from the source.

    Args:
        source: The corpus file's text.
        output: What the library wrote back.

    Returns:
        Semicolon-separated notes: lost or moved comments, blank-line counts, document
        markers, directives, anchors and aliases, re-indented sequences, line counts.
        When fewer than two of those fire, the first differing line is quoted instead.

    """
    notes: list[str] = []

    if source.startswith('\ufeff') and not output.startswith('\ufeff'):
        notes.append('dropped the BOM')
    if '\r\n' in source and '\r\n' not in output:
        notes.append('CRLF -> LF')

    src_comments, out_comments = _comment_lines(source), _comment_lines(output)
    src_eol, out_eol = _eol_comments(source), _eol_comments(output)
    gained, lost_eol = out_comments - src_comments, src_eol - out_eol
    if gained > 0 and gained == lost_eol:
        # ruamel keeps the comment but demotes it to its own line.
        notes.append(f'moved {gained} end-of-line comment(s) onto their own line')
    else:
        if out_comments < src_comments:
            notes.append(f'lost {src_comments - out_comments} own-line comment(s)')
        elif gained > 0:
            notes.append(f'invented {gained} own-line comment(s)')
        if lost_eol > 0:
            notes.append(f'lost {lost_eol} end-of-line comment(s)')

    src_blank, out_blank = _blank_lines(source), _blank_lines(output)
    if src_blank != out_blank:
        notes.append(f'blank lines {src_blank} -> {out_blank}')
    elif _blank_runs(source) != _blank_runs(output):
        notes.append('blank lines moved')

    for marker, what in (('---', 'start'), ('...', 'end')):
        src_n = sum(1 for ln in source.split('\n') if ln.rstrip() == marker)
        out_n = sum(1 for ln in output.split('\n') if ln.rstrip() == marker)
        if src_n != out_n:
            verb = 'added' if out_n > src_n else 'dropped'
            notes.append(f'{verb} {abs(out_n - src_n)} `{marker}` document {what}')

    for directive in ('%YAML', '%TAG'):
        src_n = source.count(directive)
        out_n = output.count(directive)
        if out_n < src_n:
            notes.append(f'dropped {src_n - out_n} {directive} directive(s)')
        elif out_n > src_n:
            notes.append(f'added {out_n - src_n} {directive} directive(s)')

    if source.endswith('\n') and not output.endswith('\n'):
        notes.append('removed the final newline')
    elif not source.endswith('\n') and output.endswith('\n'):
        notes.append('appended a final newline')

    for label, pattern in (
        ('anchor', r'&[\w-]+'),
        ('alias', r'\*[\w-]+'),
        ('merge key', r'<<\s*:'),
    ):
        src_n = len(re.findall(pattern, source))
        out_n = len(re.findall(pattern, output))
        if out_n < src_n:
            notes.append(f'lost {src_n - out_n} {label}(s)')
        elif out_n > src_n:
            notes.append(f'added {out_n - src_n} {label}(s)')

    if _seq_indents(source) != _seq_indents(output):
        notes.append('re-indented block sequences')

    src_lines = source.split('\n')
    out_lines = output.split('\n')
    if len(src_lines) != len(out_lines):
        notes.append(f'{len(src_lines) - 1} lines -> {len(out_lines) - 1}')
        if len(out_lines) > len(src_lines) and max(map(len, src_lines)) > 80:
            notes.append('refolded long lines')

    if len(notes) < 2:
        notes.append(_first_change(src_lines, out_lines))
    return '; '.join(notes)


def _seq_indents(text: str) -> set[int]:
    """Return the set of columns that block-sequence dashes appear at."""
    return {
        len(line) - len(line.lstrip(' '))
        for line in text.split('\n')
        if line.lstrip(' ').startswith('- ') or line.strip() == '-'
    }


def _first_change(src_lines: list[str], out_lines: list[str]) -> str:
    """Return the old and the new text of the first line that differs."""
    for n, (old, new) in enumerate(zip(src_lines, out_lines, strict=False), start=1):
        if old != new:
            shared = len(os.path.commonprefix([_visible(old), _visible(new)]))
            start = max(0, shared - 18)
            return f'line {n}: `{_clip(_visible(old), start)}` -> `{_clip(_visible(new), start)}`'
    return 'differs only in trailing content'


def _clip(shown: str, start: int = 0, width: int = 44) -> str:
    """Return a window of `width` characters onto `shown`, with ellipses on the cut sides."""
    head = '...' if start else ''
    tail = '...' if len(shown) - start > width else ''
    return head + shown[start : start + width] + tail


# --------------------------------------------------------------------------
# corpus self-check
# --------------------------------------------------------------------------


def lint_corpus() -> list[str]:
    """Check the corpus itself, independently of any YAML library.

    Returns:
        One line per problem: a file that is not valid UTF-8, a file whose first line is
        not a `# covers:` comment, and the case of no corpus files at all. Empty when
        the corpus is sound.

    """
    problems = []
    for path in corpus_files():
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            problems.append(f'{path.name}: not valid UTF-8 ({exc})')
            continue
        head = text.lstrip('\ufeff').split('\n', 1)[0]
        if not head.startswith('# covers:'):
            problems.append(f'{path.name}: first line is not a `# covers:` comment')
    if not corpus_files():
        problems.append(f'{CORPUS_DIR} holds no *.yaml files')
    return problems


# --------------------------------------------------------------------------
# script
# --------------------------------------------------------------------------


def _mark(*, ok: bool) -> str:
    """Return a table cell for one verdict, bolded when the answer is no."""
    return 'yes' if ok else '**no**'


def _table(rows: list[tuple[Result, Result]], notes_from: str = 'yamluna') -> str:
    """Return a markdown table, one row per corpus file: does ruamel round-trip it, does yamluna."""
    width = max(len(r.name) for r, _ in rows) + 2  # room for the backticks around the name
    lines = [
        f'| {"corpus file":<{width}} | ruamel | yamluna | what {notes_from} changed |',
        f'| {"-" * width} | ------ | ------- | {"-" * (len(notes_from) + 13)} |',
    ]
    for ruamel, yamluna in rows:
        note = (yamluna if notes_from == 'yamluna' else ruamel).summary
        cell = note.replace('|', '\\|')  # keep the markdown table intact
        lines.append(
            f'| {"`" + ruamel.name + "`":<{width}} '
            f'| {_mark(ok=ruamel.ok):<6} | {_mark(ok=yamluna.ok):<7} | {cell} |'
        )
    return '\n'.join(lines)


def _behaviour_table(rows: list[tuple[Result, Result]]) -> str:
    """Return a markdown table for the behaviour-scored files, one row per library per file."""
    lines = [
        '| corpus file       | library | as specified | what it does |',
        '| ----------------- | ------- | ------------ | ------------ |',
    ]
    for ruamel, yamluna in rows:
        for label, result in (('ruamel', ruamel), ('yamluna', yamluna)):
            cell = result.summary.replace('|', '\\|')
            mark = _mark(ok=result.ok)
            lines.append(f'| {"`" + result.name + "`":<17} | {label:<7} | {mark:<12} | {cell} |')
    return '\n'.join(lines)


def _yamluna_version() -> str:
    """Return the installed yamluna version, or a note saying why there is none to report."""
    try:
        import yamluna

        return getattr(yamluna, '__version__', '(unknown version)')
    except ImportError as exc:  # pragma: no cover: only when the wheel is missing
        return f'(not importable: {exc})'


def main(argv: list[str]) -> int:
    """Run the corpus comparison and prints the tables.

    Args:
        argv: The command-line arguments after the script name. Flags are `--diff`,
            `--ruamel` and `--seq-indent`; anything else is a corpus stem to measure on
            its own, which turns diffs on.

    Returns:
        0 when the corpus itself is sound, 1 when `lint_corpus` found a problem, and 2
        when a named corpus file does not exist. The score is printed, not returned.

    """
    show_diffs = '--diff' in argv
    show_ruamel = '--ruamel' in argv
    # ruamel emits one global indentation, so `--seq-indent` measures the corpus again
    # with the style most of it is written in. That separates "ruamel cannot keep this
    # file's layout" from "ruamel was pointed at the other layout".
    sequence, offset = (4, 2) if '--seq-indent' in argv else (2, 0)
    wanted = [a for a in argv if not a.startswith('-')]

    paths = corpus_files()
    if wanted:
        names = {w.removesuffix('.yaml') for w in wanted}
        paths = [p for p in paths if p.stem in names]
        show_diffs = True
        if not paths:
            print(f'no corpus file matches {sorted(names)}', file=sys.stderr)
            return 2

    behaviour_paths = [p for p in paths if p.stem in BEHAVIOUR_ONLY]
    rows = [
        (
            check_file(p, sequence, offset),
            check_file(p, roundtrip=roundtrip_with_yamluna, label='yamluna'),
        )
        for p in paths
        if p.stem not in BEHAVIOUR_ONLY
    ]
    behaviour_rows = [
        (check_duplicate_keys(load_one_with_ruamel), check_duplicate_keys(load_with_yamluna))
        for _ in behaviour_paths
    ]
    total = len(rows)
    ruamel_clean = sum(r.ok for r, _ in rows)
    yamluna_clean = sum(y.ok for _, y in rows)

    config = f'indent(mapping=2, sequence={sequence}, offset={offset})'
    print(f"ruamel.yaml {RUAMEL_VERSION}, typ='rt', preserve_quotes=True, {config}")
    print(f"yamluna {_yamluna_version()}, typ='rt', preserve_quotes=True, defaults")
    if rows:
        print()
        scored = f'of {total} round-trippable files round-trip byte-identically'
        print(f'ruamel : {ruamel_clean:>2} {scored}')
        print(f'yamluna: {yamluna_clean:>2} {scored}')
        print()
        print(_table(rows, 'ruamel' if show_ruamel else 'yamluna'))
    if behaviour_rows:
        print()
        print('Scored on behaviour, not bytes: no dict-backed API can write two equal')
        print('keys back, so byte-identity is not a verdict on these files.')
        print()
        print(_behaviour_table(behaviour_rows))

    if show_diffs:
        for ruamel, yamluna in rows:
            shown = ruamel if show_ruamel else yamluna
            if shown.ok:
                continue
            label = 'ruamel' if show_ruamel else 'yamluna'
            print(f'\n{"=" * 72}\n{shown.name} ({label}): {shown.summary}\n{"=" * 72}')
            print(shown.diff or f'(no diff: {shown.error})')

    problems = lint_corpus()
    if problems:
        print('\ncorpus problems:')
        for problem in problems:
            print(f'  - {problem}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
