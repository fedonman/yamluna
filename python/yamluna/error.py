"""Error hierarchy and source positions.

Layout mirrors ``ruamel.yaml.error`` so existing ``except`` blocks and error output keep
working, with one class per ruamel class that ``typ='rt'`` can actually raise.

Positions are **character** offsets with 0-based ``line``/``column`` throughout, matching
both ruamel's ``Mark`` and the offsets the Rust core reports (DESIGN.md 1.5).  Error
*messages* print ``line + 1`` / ``column + 1``.  Never feed a byte offset into ``pointer``:
``buffer`` is a ``str``, so a byte offset slices mid-character on any document containing
an accent or an emoji.
"""

import re
import textwrap

__all__ = [
    'CommentMark',
    'ComposerError',
    'ConstructorError',
    'DuplicateKeyError',
    'DuplicateKeyFutureWarning',
    'EmitterError',
    'FileMark',
    'Mark',
    'MarkedYAMLError',
    'MarkedYAMLFutureWarning',
    'MarkedYAMLWarning',
    'ParserError',
    'RepresenterError',
    'ReusedAnchorWarning',
    'ScannerError',
    'StreamMark',
    'StringMark',
    'YAMLError',
    'YAMLFutureWarning',
    'YAMLStreamError',
    'YAMLWarning',
    'make_error',
]

#: What the scanner counts as a line break (`char_traits::is_break`): CR, LF, CRLF.
_BREAK = re.compile(r'\r\n|[\r\n]')

#: What ruamel's snippet scan stops at.  Wider than `_BREAK` on purpose: this set is
#: copied verbatim from ruamel so snippets render identically.
_SNIPPET_STOP = '\0\r\n\x85\u2028\u2029'

DUPKEY_URL = 'https://yaml.dev/doc/ruamel.yaml/api/#Duplicate_keys'


def _line_bounds(text: str) -> tuple[list[int], list[int]]:
    """Char offsets of the start and of the end-of-content of every line."""
    starts, ends = [0], []
    for m in _BREAK.finditer(text):
        ends.append(m.start())
        starts.append(m.end())
    ends.append(len(text))
    return starts, ends


def _offset_of(text: str, line: int, column: int) -> int:
    """Char offset of 0-based ``line``/``column``, clamped into ``text``."""
    starts, ends = _line_bounds(text)
    if line < 0:
        return 0
    if line >= len(starts):
        return len(text)
    return min(starts[line] + max(column, 0), ends[line])


class Mark:
    """A position in a stream.

    ``index`` and ``pointer`` are char offsets, ``line`` and ``column`` are 0-based.
    ``index`` may be ``None``, in which case it is derived from ``line``/``column``
    (this is the path used for errors that come from a node rather than the scanner).
    Covers ruamel's ``StreamMark``/``FileMark``/``StringMark``; ``buffer is None``
    simply means there is no snippet.
    """

    __slots__ = ('name', 'index', 'line', 'column', 'buffer', 'pointer')

    def __init__(
        self,
        name: object,
        index: int | None,
        line: int,
        column: int,
        buffer: str | None = None,
        pointer: int | None = None,
    ) -> None:
        self.name = name
        self.line = line
        self.column = column
        self.buffer = buffer
        if index is None:
            index = _offset_of(buffer, line, column) if buffer is not None else 0
        if pointer is None:
            pointer = index
        if buffer is not None:
            # A position outside the buffer is a bug upstream; clamping keeps the snippet
            # from raising on top of the error we are already reporting.
            index = min(max(index, 0), len(buffer))
            pointer = min(max(pointer, 0), len(buffer))
        self.index = index
        self.pointer = pointer

    def get_snippet(self, indent: int = 4, max_length: int = 75) -> str | None:
        if self.buffer is None:
            return None
        head = ''
        start = self.pointer
        while start > 0 and self.buffer[start - 1] not in _SNIPPET_STOP:
            start -= 1
            if self.pointer - start > max_length / 2 - 1:
                head = ' ... '
                start += 5
                break
        tail = ''
        end = self.pointer
        while end < len(self.buffer) and self.buffer[end] not in _SNIPPET_STOP:
            end += 1
            if end - self.pointer > max_length / 2 - 1:
                tail = ' ... '
                end -= 5
                break
        snippet = self.buffer[start:end]
        caret = f'^ (line: {self.line + 1})'
        return (
            ' ' * indent
            + head
            + snippet
            + tail
            + '\n'
            + ' ' * (indent + self.pointer - start + len(head))
            + caret
        )

    def __str__(self) -> str:
        where = f'  in "{self.name!s}", line {self.line + 1:d}, column {self.column + 1:d}'
        snippet = self.get_snippet()
        if snippet is not None:
            where += ':\n' + snippet
        return where

    __repr__ = __str__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mark):
            return NotImplemented
        return (
            self.line == other.line
            and self.column == other.column
            and self.name == other.name
            and self.index == other.index
        )

    __hash__ = None  # type: ignore[assignment]  # mutable position, same as ruamel


# ruamel names.  One class does for all three: a mark without a buffer behaves exactly
# like ruamel's StreamMark.
StreamMark = Mark
FileMark = Mark
StringMark = Mark


class CommentMark:
    """Column a comment was written at (ruamel compatibility)."""

    __slots__ = ('column',)

    def __init__(self, column: int) -> None:
        self.column = column


class _Marked:
    """context/problem/marks rendering, shared by the marked error and warning classes."""

    def __init__(
        self,
        context: str | None = None,
        context_mark: Mark | None = None,
        problem: str | None = None,
        problem_mark: Mark | None = None,
        note: str | None = None,
        warn: str | None = None,
    ) -> None:
        self.context = context
        self.context_mark = context_mark
        self.problem = problem
        self.problem_mark = problem_mark
        self.note = note
        self.warn = warn

    def __str__(self) -> str:
        lines: list[str] = []
        if self.context is not None:
            lines.append(self.context)
        if self.context_mark is not None and (
            self.problem is None
            or self.problem_mark is None
            or self.context_mark.name != self.problem_mark.name
            or self.context_mark.line != self.problem_mark.line
            or self.context_mark.column != self.problem_mark.column
        ):
            lines.append(str(self.context_mark))
        if self.problem is not None:
            lines.append(self.problem)
        if self.problem_mark is not None:
            lines.append(str(self.problem_mark))
        for extra in (self.note, self.warn):
            if extra:
                lines.append(textwrap.dedent(extra))
        return '\n'.join(lines)


class YAMLError(Exception):
    pass


class MarkedYAMLError(_Marked, YAMLError):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.warn = None  # ruamel accepts and ignores `warn` on errors


class ScannerError(MarkedYAMLError):
    pass


class ParserError(MarkedYAMLError):
    pass


class ComposerError(MarkedYAMLError):
    pass


class ConstructorError(MarkedYAMLError):
    pass


class RepresenterError(MarkedYAMLError):
    pass


class EmitterError(MarkedYAMLError):
    pass


class DuplicateKeyError(MarkedYAMLError):
    pass


class YAMLStreamError(Exception):
    """Not a `YAMLError` -- deliberately, that is where ruamel puts it."""


class YAMLWarning(Warning):
    pass


class MarkedYAMLWarning(_Marked, YAMLWarning):
    pass


class ReusedAnchorWarning(YAMLWarning):
    pass


class YAMLFutureWarning(Warning):
    pass


class MarkedYAMLFutureWarning(_Marked, YAMLFutureWarning):
    pass


class DuplicateKeyFutureWarning(MarkedYAMLFutureWarning):
    pass


_KINDS: dict[str, type[MarkedYAMLError]] = {
    cls.__name__.lower().removesuffix('error'): cls
    for cls in (
        ScannerError,
        ParserError,
        ComposerError,
        ConstructorError,
        RepresenterError,
        EmitterError,
        DuplicateKeyError,
    )
}


def make_error(
    kind: str,
    message: str,
    line: int,
    col: int,
    index: int | None = None,
    source: str | None = None,
    name: str = '<unicode string>',
    note: str | None = None,
) -> MarkedYAMLError:
    """Build the exception for a structured error reported by the Rust core.

    `kind` is the `ParseError` discriminant -- 'scanner', 'parser', 'duplicate_key', ...
    (the class name, with or without the 'Error' suffix, also works).  This is the only
    place classification happens; Rust must never string-match on `message`.
    `line`/`col` are 0-based and `index` is a **char** offset into `source`; pass
    `index=None` when only a line/column is known.
    """
    key = kind.lower().replace('_', '').replace('-', '').removesuffix('error')
    cls = _KINDS.get(key, MarkedYAMLError)
    return cls(None, None, message, Mark(name, index, line, col, source), note)
