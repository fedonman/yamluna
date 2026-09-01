"""The exception and warning hierarchy, and the source positions the errors carry.

The layout mirrors `ruamel.yaml.error`, one class per ruamel class that `typ='rt'` can
raise, so `except` blocks and error output written against ruamel keep working.

Positions are character offsets, with `line` and `column` 0-based throughout, matching both
ruamel's `Mark` and the offsets the Rust core reports. Messages print `line + 1` and
`column + 1`. Never feed a byte offset into `pointer`: `buffer` is a `str`, so a byte
offset slices mid-character on any document containing an accent or an emoji.
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

# What the scanner counts as a line break (`char_traits::is_break`): CR, LF, CRLF.
_BREAK = re.compile(r'\r\n|[\r\n]')

# What ruamel's snippet scan stops at. Wider than `_BREAK` deliberately: the set is copied
# verbatim from ruamel so snippets render identically.
_SNIPPET_STOP = '\0\r\n\x85\u2028\u2029'

DUPKEY_URL = 'https://yaml.dev/doc/ruamel.yaml/api/#Duplicate_keys'
"""ruamel's page on duplicate-key handling. Nothing in yamluna prints it."""


def _line_bounds(text: str) -> tuple[list[int], list[int]]:
    """Char offsets of the start and of the end-of-content of every line."""
    starts, ends = [0], []
    for m in _BREAK.finditer(text):
        ends.append(m.start())
        starts.append(m.end())
    ends.append(len(text))
    return starts, ends


def _offset_of(text: str, line: int, column: int) -> int:
    """Char offset of the 0-based `line` and `column`, clamped into `text`."""
    starts, ends = _line_bounds(text)
    if line < 0:
        return 0
    if line >= len(starts):
        return len(text)
    return min(starts[line] + max(column, 0), ends[line])


class Mark:
    """A position in a stream, with the text around it when there is any.

    One class covers ruamel's `StreamMark`, `FileMark` and `StringMark`.

    Args:
        name: What to call the stream in messages, usually a file name.
        index: Character offset of the position. `None` derives it from `line` and
            `column`, which is the path errors raised against a node take, since a node
            carries a line and a column and no offset.
        line: 0-based line.
        column: 0-based column.
        buffer: The source text the position is in. `None` means no snippet is printed.
        pointer: Character offset the caret points at. Defaults to `index`.

    """

    __slots__ = ('buffer', 'column', 'index', 'line', 'name', 'pointer')

    # ruamel's positional signature, which ported code calls as `Mark(name, index, line,
    # column, buffer, pointer)`; the class docstring documents the arguments.
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        name: object,
        index: int | None,
        line: int,
        column: int,
        buffer: str | None = None,
        pointer: int | None = None,
    ) -> None:
        """Store the position, deriving `index` and `pointer` when they are `None`."""
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
        """Return the source around this position, with a caret on the line below it.

        Args:
            indent: Spaces put in front of both lines.
            max_length: Longest snippet to print. A longer line is cut on whichever side
                overruns and the cut end is marked with ` ... `.

        Returns:
            Two lines, the source text and the caret line, or `None` when this mark carries
            no buffer.

        """
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
        """Where the mark points, and the snippet under it when it carries a buffer."""
        where = f'  in "{self.name!s}", line {self.line + 1:d}, column {self.column + 1:d}'
        snippet = self.get_snippet()
        if snippet is not None:
            where += ':\n' + snippet
        return where

    # A mark in a traceback reads the same as a mark in a message.
    __repr__ = __str__

    def __eq__(self, other: object) -> bool:
        """Return whether `other` is a `Mark` at the same place in the same stream.

        The comparison is the stream name, the line, the column and `index`. The buffer
        and the caret are left out, but `index` is derived from the buffer when the caller
        does not pass one, so a mark built with the source text and a mark built without it
        do not compare equal even when they name the same line and column.
        """
        # Position and stream name only. `buffer` and `pointer` are there for printing.
        if not isinstance(other, Mark):
            return NotImplemented
        return (
            self.line == other.line
            and self.column == other.column
            and self.name == other.name
            and self.index == other.index
        )

    __hash__ = None  # type: ignore[assignment]  # mutable position, same as ruamel


# ruamel's three names. One class does for all of them: a mark with no buffer behaves
# exactly like ruamel's StreamMark.
StreamMark = Mark
"""ruamel's name for a mark into a stream. The same class as `Mark`."""

FileMark = Mark
"""ruamel's name for a mark into a file. The same class as `Mark`."""

StringMark = Mark
"""ruamel's name for a mark into a string, the one that carries a snippet buffer."""


class CommentMark:
    """The column a comment was written at.

    Here so that code importing `CommentMark` from `yamluna.error`, as ruamel allows, keeps
    working. The class yamluna itself attaches to comment tokens is
    `yamluna.comments.CommentMark`.

    Args:
        column: 0-based column of the `#`.

    """

    __slots__ = ('column',)

    def __init__(self, column: int) -> None:
        """Store the 0-based `column` of the `#`."""
        self.column = column


class _Marked:
    """The context, problem, mark and note rendering the marked errors and warnings share."""

    # ruamel's positional signature, which ported code and the Rust core's `make_error`
    # both call as `cls(context, context_mark, problem, problem_mark, note)`.
    def __init__(  # noqa: PLR0913, PLR0917
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
        lines.extend(textwrap.dedent(extra) for extra in (self.note, self.warn) if extra)
        return '\n'.join(lines)


class YAMLError(Exception):
    """Base class of the YAML errors. Catch it to catch any of them."""


class MarkedYAMLError(_Marked, YAMLError):
    """A `YAMLError` that says where in the source it happened.

    `str()` renders the context line, the context mark, the problem line, the problem mark
    and the note, each one when it is set, in ruamel's order and layout. It is also what
    `make_error` falls back to for a kind it does not recognise.

    Args:
        context: What was being read, such as `while parsing a block mapping`.
        context_mark: Where that started.
        problem: What went wrong.
        problem_mark: Where it went wrong.
        note: Extra text, printed last and dedented.

    """

    # `_Marked`'s signature, spelled out rather than forwarded through `*args`, so that the
    # arguments keep their types across the call. Same order and defaults as ruamel's.
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        context: str | None = None,
        context_mark: Mark | None = None,
        problem: str | None = None,
        problem_mark: Mark | None = None,
        note: str | None = None,
        warn: str | None = None,
    ) -> None:
        """Store the context, problem, marks and note, and drop `warn`."""
        super().__init__(context, context_mark, problem, problem_mark, note, warn)
        self.warn = None  # ruamel accepts and ignores `warn` on errors


class ScannerError(MarkedYAMLError):
    """Raised when the source is not well-formed YAML.

    Every parse failure the Rust core reports arrives as this class, at the line and column
    the core gave for it.
    """


class ParserError(MarkedYAMLError):
    """Kept for compatibility with ruamel's name. yamluna does not raise it.

    `make_error('parser', ...)` builds one, but the core reports every parse failure as a
    `ScannerError`, so an `except ParserError` block carried over from ruamel imports and
    never fires. Catch `YAMLError` or `MarkedYAMLError` to cover both.
    """


class ComposerError(MarkedYAMLError):
    """Raised by `YAML.load` when the stream holds more than one document.

    Use `YAML.load_all` for a multi-document stream.
    """


class ConstructorError(MarkedYAMLError):
    """Raised when a node cannot be turned into a Python object.

    Covers a tag naming no registered class, a tag matching several registered classes, a
    scalar that does not hold the type its tag claims, and a registered class the loader
    cannot build because it has neither a `from_yaml` nor a mapping to unpack.
    """


class RepresenterError(MarkedYAMLError):
    """Raised when dumping meets an object it has no representation for.

    Either the object's class was never registered, or its `to_yaml` hook returned
    something other than the node index `representer.represent_*` gave it.
    """


class EmitterError(MarkedYAMLError):
    """Raised when the Rust emitter cannot write the model it was given.

    It carries a message and no mark: an emit failure has no position in any source text.
    """


class DuplicateKeyError(MarkedYAMLError):
    """Raised when a mapping repeats a key and `allow_duplicate_keys` is off.

    Off is the default. The message gives both positions, and the mark is on the second
    occurrence. Set `yaml.allow_duplicate_keys = True` to get a
    `DuplicateKeyFutureWarning` and last-value-wins instead.
    """


class YAMLStreamError(Exception):
    """Raised for a stream yamluna cannot read from or write to.

    That means an object with no `read()` or `write()` method, and the context-manager form
    used without `YAML(output=...)` or with a stream passed to `dump` inside it. It sits
    beside `YAMLError` rather than under it, which is where ruamel puts it, so it survives
    `except YAMLError`.
    """


class YAMLWarning(Warning):
    """Base class of the warnings yamluna issues."""


class MarkedYAMLWarning(_Marked, YAMLWarning):
    """A `YAMLWarning` that says where in the source it happened.

    Renders like `MarkedYAMLError` and also prints `warn`. Kept for ruamel compatibility;
    yamluna issues no warning of this class.
    """


class ReusedAnchorWarning(YAMLWarning):
    """ruamel's warning for a document that defines the same anchor twice.

    yamluna keeps both definitions and never issues this, so the name is here only so that
    an import or a `filterwarnings` entry written against ruamel keeps working.
    """


class YAMLFutureWarning(Warning):
    """Base class of the warnings about behaviour that is going to change."""


class MarkedYAMLFutureWarning(_Marked, YAMLFutureWarning):
    """A `YAMLFutureWarning` that says where in the source it applies."""


class DuplicateKeyFutureWarning(MarkedYAMLFutureWarning):
    """Warned for a repeated mapping key when `allow_duplicate_keys` is on.

    The message gives both positions and says that the last value wins.
    """


# Keyed on the class name with its `error` suffix taken off, so `ScannerError` is reached as
# 'scanner'. Adding a class above and not here silently degrades it to `MarkedYAMLError`.
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


# The Rust core calls this positionally through the C API (`crates/yamluna-py/src/lib.rs`
# caches it as `make_error`), so the parameter order is part of the FFI contract.
def make_error(  # noqa: PLR0913, PLR0917
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

    Args:
        kind: The `ParseError` discriminant: `scanner`, `parser`, `duplicate_key` and so
            on. A class name works too, with or without the `Error` suffix; case, `_` and
            `-` are ignored. An unrecognised kind gives a plain `MarkedYAMLError`.
        message: What went wrong. Printed as the first line and kept as `problem`.
        line: 0-based line of the problem.
        col: 0-based column of the problem.
        index: Character offset of the problem into `source`. Pass `None` when only a line
            and a column are known and it is derived from them.
        source: The text the problem is in, which is what the snippet is cut from. `None`
            prints no snippet.
        name: What to call the source in the message.
        note: Extra text, printed after the snippet and dedented.

    Returns:
        An instance of the class `kind` names, with `problem` set to `message` and
        `problem_mark` at the position.

    """
    # The only place a kind becomes a class. The Rust side sends the discriminant so that
    # neither side of the boundary ever has to match on the text of a message.
    key = kind.lower().replace('_', '').replace('-', '').removesuffix('error')
    cls = _KINDS.get(key, MarkedYAMLError)
    return cls(None, None, message, Mark(name, index, line, col, source), note)
