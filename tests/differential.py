"""ruamel.yaml oracle -- and yamluna's own score -- for the acceptance corpus.

Importable (pytest uses the helpers) and runnable::

    python tests/differential.py               # the whole corpus, as a table
    python tests/differential.py --diff        # ... plus a unified diff per failure
    python tests/differential.py comment-eol   # one file, with its diff
    python tests/differential.py --ruamel      # what ruamel changed, not yamluna

What this measures: whether a round-trip YAML library reads a corpus file and
writes back the exact same bytes.  Both libraries are measured the same way and
the table prints one column each.  DESIGN 6.2 holds yamluna to byte-identity on
every one of these files, which is *stricter* than ruamel;
DESIGN 6.3 says every divergence from ruamel must be a deliberate fix recorded
in ``docs/DIVERGENCES.md``.  The rows below that say "no" are the list of
places where being bug-compatible with ruamel is the wrong goal -- measured
rather than assumed, which is the whole point of running this before writing
any of the library.

The ruamel configuration is the ordinary round-trip recipe::

    yaml = YAML()            # typ='rt'
    yaml.preserve_quotes = True

and yamluna is given exactly the same two lines.  Everything else is left at its
default in both, including ``width = 80`` (so refolded long lines show up as a
failure -- that is a real default-configuration behaviour, not a rigged one) and
``allow_duplicate_keys = False``.
"""

from __future__ import annotations

import difflib
import io
import re
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML, version_info

CORPUS_DIR = Path(__file__).parent / "corpus"
RUAMEL_VERSION = ".".join(str(part) for part in version_info[:3])

# --------------------------------------------------------------------------
# ruamel helpers
# --------------------------------------------------------------------------


def ruamel_rt(sequence: int = 2, offset: int = 0) -> YAML:
    """A round-trip ``YAML`` configured the way this harness measures it.

    ``sequence``/``offset`` are ruamel's own defaults; pass ``4, 2`` to give
    ruamel the indentation style most of this corpus is written in, which is
    how the "indentation only" figure in tests/README.md was measured.
    """
    yaml = YAML()  # typ='rt'
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=sequence, offset=offset)
    return yaml


def load_with_ruamel(text: str, yaml: YAML | None = None) -> list[Any]:
    """Load every document in *text*.  Always a list, even for one document."""
    return list((yaml or ruamel_rt()).load_all(text))


def dump_with_ruamel(data: list[Any], yaml: YAML | None = None) -> str:
    """Dump a list of documents back to a string."""
    buf = io.StringIO()
    (yaml or ruamel_rt()).dump_all(data, buf)
    return buf.getvalue()


def roundtrip_with_ruamel(text: str, sequence: int = 2, offset: int = 0) -> str:
    """``load`` then ``dump`` through one ruamel instance, as users do."""
    yaml = ruamel_rt(sequence, offset)
    return dump_with_ruamel(load_with_ruamel(text, yaml), yaml)


# --------------------------------------------------------------------------
# yamluna, measured identically
# --------------------------------------------------------------------------


def roundtrip_with_yamluna(text: str) -> str:
    """``load_all`` then ``dump_all`` through one ``yamluna.YAML``, as users do."""
    import yamluna

    yaml = yamluna.YAML()  # typ='rt'
    yaml.preserve_quotes = True
    buf = io.StringIO()
    yaml.dump_all(list(yaml.load_all(text)), buf)
    return buf.getvalue()


def read_corpus_file(path: Path) -> str:
    """Read a corpus file as text without touching newlines or the BOM."""
    return path.read_bytes().decode("utf-8")


def corpus_files() -> list[Path]:
    return sorted(CORPUS_DIR.glob("*.yaml"))


# --------------------------------------------------------------------------
# one file
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Result:
    """What ruamel did to one corpus file."""

    path: Path
    ok: bool
    """True when ruamel's load -> dump is byte-identical to the input."""
    summary: str
    """One line naming what changed (empty when ``ok``)."""
    diff: str
    """Unified diff, input -> ruamel's output (empty when ``ok``)."""
    error: str | None = None
    """Exception type and message, when ruamel refused the file outright."""

    @property
    def name(self) -> str:
        return self.path.stem


def check_file(
    path: Path,
    sequence: int = 2,
    offset: int = 0,
    roundtrip: Callable[[str], str] | None = None,
    label: str = "ruamel",
) -> Result:
    """Round-trip one corpus file through a library and report what changed."""
    if roundtrip is None:

        def roundtrip(text: str) -> str:
            return roundtrip_with_ruamel(text, sequence, offset)

    source = read_corpus_file(path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            output = roundtrip(source)
        except Exception as exc:  # noqa: BLE001 - the failure mode *is* the result
            first = str(exc).strip().splitlines()
            error = f"{type(exc).__name__}: {first[0] if first else ''}".strip()
            return Result(path, False, f"raises {error}", "", error)
    warned = sorted({w.category.__name__ for w in caught})

    if output == source:
        return Result(path, True, f"(warns {', '.join(warned)})" if warned else "", "")
    notes = summarize(source, output)
    if warned:
        notes = f"{notes}; warns {', '.join(warned)}"
    return Result(path, False, notes, unified(source, output, label))


def unified(source: str, output: str, label: str = "ruamel") -> str:
    """Unified diff with the invisible characters made visible."""
    return "".join(
        difflib.unified_diff(
            [_visible(line) + "\n" for line in source.split("\n")],
            [_visible(line) + "\n" for line in output.split("\n")],
            fromfile="input",
            tofile=label,
            lineterm="\n",
        )
    )


def _visible(line: str) -> str:
    """Markers, not backslash escapes: a literal TAB must not look like `\\t`."""
    return (
        line.replace("\r", "<CR>")
        .replace("\t", "<TAB>")
        .replace("\ufeff", "<BOM>")
        .replace("\xa0", "<NBSP>")
    )


# --------------------------------------------------------------------------
# "what changed", in one line
# --------------------------------------------------------------------------


def _comment_lines(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.lstrip().startswith("#"))


def _eol_comments(text: str) -> int:
    return sum(
        1
        for line in text.split("\n")
        if "#" in line and not line.lstrip().startswith("#")
    )


def _blank_lines(text: str) -> int:
    return sum(1 for line in text.split("\n")[:-1] if not line.strip())


def _blank_runs(text: str) -> int:
    runs, in_run = 0, False
    for line in text.split("\n")[:-1]:
        blank = not line.strip()
        runs += blank and not in_run
        in_run = blank
    return runs


def summarize(source: str, output: str) -> str:
    """A one-line description of how *output* differs from *source*."""
    notes: list[str] = []

    if source.startswith("\ufeff") and not output.startswith("\ufeff"):
        notes.append("dropped the BOM")
    if "\r\n" in source and "\r\n" not in output:
        notes.append("CRLF -> LF")

    src_comments, out_comments = _comment_lines(source), _comment_lines(output)
    src_eol, out_eol = _eol_comments(source), _eol_comments(output)
    gained, lost_eol = out_comments - src_comments, src_eol - out_eol
    if gained > 0 and gained == lost_eol:
        # ruamel keeps the comment but demotes it to its own line.
        notes.append(f"moved {gained} end-of-line comment(s) onto their own line")
    else:
        if out_comments < src_comments:
            notes.append(f"lost {src_comments - out_comments} own-line comment(s)")
        elif gained > 0:
            notes.append(f"invented {gained} own-line comment(s)")
        if lost_eol > 0:
            notes.append(f"lost {lost_eol} end-of-line comment(s)")

    src_blank, out_blank = _blank_lines(source), _blank_lines(output)
    if src_blank != out_blank:
        notes.append(f"blank lines {src_blank} -> {out_blank}")
    elif _blank_runs(source) != _blank_runs(output):
        notes.append("blank lines moved")

    for marker, what in (("---", "start"), ("...", "end")):
        src_n = sum(1 for ln in source.split("\n") if ln.rstrip() == marker)
        out_n = sum(1 for ln in output.split("\n") if ln.rstrip() == marker)
        if src_n != out_n:
            verb = "added" if out_n > src_n else "dropped"
            notes.append(f"{verb} {abs(out_n - src_n)} `{marker}` document {what}")

    for directive in ("%YAML", "%TAG"):
        src_n = source.count(directive)
        out_n = output.count(directive)
        if out_n < src_n:
            notes.append(f"dropped {src_n - out_n} {directive} directive(s)")
        elif out_n > src_n:
            notes.append(f"added {out_n - src_n} {directive} directive(s)")

    if source.endswith("\n") and not output.endswith("\n"):
        notes.append("removed the final newline")
    elif not source.endswith("\n") and output.endswith("\n"):
        notes.append("appended a final newline")

    for label, pattern in (
        ("anchor", r"&[\w-]+"),
        ("alias", r"\*[\w-]+"),
        ("merge key", r"<<\s*:"),
    ):
        src_n = len(re.findall(pattern, source))
        out_n = len(re.findall(pattern, output))
        if out_n < src_n:
            notes.append(f"lost {src_n - out_n} {label}(s)")
        elif out_n > src_n:
            notes.append(f"added {out_n - src_n} {label}(s)")

    if _seq_indents(source) != _seq_indents(output):
        notes.append("re-indented block sequences")

    src_lines = source.split("\n")
    out_lines = output.split("\n")
    if len(src_lines) != len(out_lines):
        notes.append(f"{len(src_lines) - 1} lines -> {len(out_lines) - 1}")
        if len(out_lines) > len(src_lines) and max(map(len, src_lines)) > 80:
            notes.append("refolded long lines")

    if len(notes) < 2:
        notes.append(_first_change(src_lines, out_lines))
    return "; ".join(notes)


def _seq_indents(text: str) -> set[int]:
    """The columns block-sequence dashes appear at."""
    return {
        len(line) - len(line.lstrip(" "))
        for line in text.split("\n")
        if line.lstrip(" ").startswith("- ") or line.strip() == "-"
    }


def _first_change(src_lines: list[str], out_lines: list[str]) -> str:
    """`-old / +new` for the first line that differs."""
    for n, (old, new) in enumerate(zip(src_lines, out_lines), start=1):
        if old != new:
            shared = len(_visible(old)) - len(_visible(old).lstrip())
            for shared, (a, b) in enumerate(zip(_visible(old), _visible(new))):
                if a != b:
                    break
            start = max(0, shared - 18)
            return (
                f"line {n}: `{_clip(_visible(old), start)}`"
                f" -> `{_clip(_visible(new), start)}`"
            )
    return "differs only in trailing content"


def _clip(shown: str, start: int = 0, width: int = 44) -> str:
    """A ``width``-wide window of *shown* around the first difference."""
    head = "..." if start else ""
    tail = "..." if len(shown) - start > width else ""
    return head + shown[start : start + width] + tail


# --------------------------------------------------------------------------
# corpus self-check
# --------------------------------------------------------------------------


def lint_corpus() -> list[str]:
    """Problems with the corpus itself, independent of any YAML library."""
    problems = []
    for path in corpus_files():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{path.name}: not valid UTF-8 ({exc})")
            continue
        head = text.lstrip("\ufeff").split("\n", 1)[0]
        if not head.startswith("# covers:"):
            problems.append(f"{path.name}: first line is not a `# covers:` comment")
    if not corpus_files():
        problems.append(f"{CORPUS_DIR} holds no *.yaml files")
    return problems


# --------------------------------------------------------------------------
# script
# --------------------------------------------------------------------------


def _mark(ok: bool) -> str:
    return "yes" if ok else "**no**"


def _table(rows: list[tuple[Result, Result]], notes_from: str = "yamluna") -> str:
    """One row per corpus file: does ruamel round-trip it, does yamluna."""
    width = max(len(r.name) for r, _ in rows) + 2  # the backticks around the name
    lines = [
        f"| {'corpus file':<{width}} | ruamel | yamluna | what {notes_from} changed |",
        f"| {'-' * width} | ------ | ------- | {'-' * (len(notes_from) + 13)} |",
    ]
    for ruamel, yamluna in rows:
        note = (yamluna if notes_from == "yamluna" else ruamel).summary
        cell = note.replace("|", "\\|")  # keep the markdown table intact
        lines.append(
            f"| {'`' + ruamel.name + '`':<{width}} "
            f"| {_mark(ruamel.ok):<6} | {_mark(yamluna.ok):<7} | {cell} |"
        )
    return "\n".join(lines)


def _yamluna_version() -> str:
    try:
        import yamluna

        return getattr(yamluna, "__version__", "(unknown version)")
    except ImportError as exc:  # pragma: no cover -- only when the wheel is missing
        return f"(not importable: {exc})"


def main(argv: list[str]) -> int:
    show_diffs = "--diff" in argv
    show_ruamel = "--ruamel" in argv
    # ruamel emits one global indentation; `--seq-indent` measures the corpus
    # again with the style most of it is written in, to separate "ruamel cannot
    # keep this file's layout" from "ruamel was pointed at the other layout".
    sequence, offset = (4, 2) if "--seq-indent" in argv else (2, 0)
    wanted = [a for a in argv if not a.startswith("-")]

    paths = corpus_files()
    if wanted:
        names = {w.removesuffix(".yaml") for w in wanted}
        paths = [p for p in paths if p.stem in names]
        show_diffs = True
        if not paths:
            print(f"no corpus file matches {sorted(names)}", file=sys.stderr)
            return 2

    rows = [
        (
            check_file(p, sequence, offset),
            check_file(p, roundtrip=roundtrip_with_yamluna, label="yamluna"),
        )
        for p in paths
    ]
    total = len(rows)
    ruamel_clean = sum(r.ok for r, _ in rows)
    yamluna_clean = sum(y.ok for _, y in rows)

    config = f"indent(mapping=2, sequence={sequence}, offset={offset})"
    print(f"ruamel.yaml {RUAMEL_VERSION}, typ='rt', preserve_quotes=True, {config}")
    print(f"yamluna {_yamluna_version()}, typ='rt', preserve_quotes=True, defaults")
    print()
    print(f"ruamel : {ruamel_clean:>2}/{total} corpus files round-trip byte-identically")
    print(f"yamluna: {yamluna_clean:>2}/{total} corpus files round-trip byte-identically")
    print()
    print(_table(rows, "ruamel" if show_ruamel else "yamluna"))

    if show_diffs:
        for ruamel, yamluna in rows:
            shown = ruamel if show_ruamel else yamluna
            if shown.ok:
                continue
            label = "ruamel" if show_ruamel else "yamluna"
            print(f"\n{'=' * 72}\n{shown.name} ({label}): {shown.summary}\n{'=' * 72}")
            print(shown.diff or f"(no diff: {shown.error})")

    problems = lint_corpus()
    if problems:
        print("\ncorpus problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
