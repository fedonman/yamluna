"""The FFI record types -- DESIGN.md §3.

This module is the *single* definition of the boundary between the Rust core and the
Python layer.  Rust imports it once, caches a ``Py<PyType>`` per class, builds instances
through the C API on load and reads their attributes on dump::

    from yamluna._yamluna import parse, emit          # the Rust extension
    docs: list[Doc] = parse(source, allow_duplicate_keys=False)
    text: str = emit(docs, EmitOptions())

Nothing here imports anything but :mod:`typing`, and no class does any work in
``__init__`` beyond storing its arguments: these objects are allocated one per YAML node,
in a loop, on the other side of the FFI.

The tree is **flat**.  A :class:`Doc` owns ``nodes``, an arena; a node refers to its
children by index into that arena, never by reference.  ``root`` is the index of the
document's root node, or ``None`` for an empty document.

Field conventions that the type annotations cannot express:

``Node.kind``
    one of the ``KIND_*`` constants.
``Node.style``
    ``STYLE_PLAIN``/``SINGLE``/``DOUBLE``/``LITERAL``/``FOLDED`` for scalars,
    ``STYLE_BLOCK``/``STYLE_FLOW`` for sequences and mappings, ignored for aliases.
``Node.anchor``
    for a scalar/sequence/mapping, the anchor this node *defines* (``&name`` without the
    ``&``); for ``KIND_ALIAS``, the anchor it *references* (``*name`` without the ``*``) --
    ``NodeKind::Alias { anchor }`` in the core.  A node cannot do both.
``Node.tag``
    ``(handle, suffix, resolved)`` as written, e.g. ``('!', 'Circuit', 'tag:libx/Circuit')``.
``Node.value`` / ``Node.raw``
    scalars only.  ``value`` is cooked (escapes resolved, block scalars folded); ``raw`` is
    the source lexeme verbatim, including quotes and block header, and is what makes an
    unmutated round trip byte-exact.  ``raw`` is ``None`` for a node the user constructed.
``Node.children``
    sequence items, or ``k, v, k, v, ...`` for a mapping.  Empty for scalars and aliases.
``Node.merge``
    positions in ``children`` holding the key of a ``<<`` entry (so always even), in source
    order.  The merge is *not* expanded; the Python layer resolves it lazily so a dump
    re-emits ``<<: *base``.
``Node.explicit``
    positions in ``children`` holding the key of an entry written in the explicit ``? key``
    / ``: value`` form (so always even), in source order.  Same shape as ``merge``.
``Node.tag_first``
    the tag was written *before* the anchor (``!!str &a v``, not ``&a !!str v``).  YAML
    allows either order, so the order the source used has to be carried.
``Doc.tags_before_version``
    how many of ``tag_directives`` were written above the ``%YAML`` line; the rest were
    written below it.
``Doc.bom``
    the stream began with a byte-order mark.  Only ever true on the first document; the
    loader strips it and the emitter writes it back.
``Doc.final_line_break``
    the source ended with a line break.  A file whose last line is an unterminated comment
    is the case this exists for.
``Node.before`` / ``eol`` / ``inner`` / ``after``
    the four trivia slots of DESIGN.md §2.1, keyed by node identity rather than by index.
``Trivia``
    either a comment (``text`` set, ``blank_lines == 0``) or a run of blank lines
    (``blank_lines > 0``, ``text is None``).  Comment ``text`` includes the leading ``#``
    and excludes the line break; ``own_line`` is ``False`` for an end-of-line comment;
    ``col`` is the 0-based column of the ``#``.

Positions (``Node.line``, ``Node.col``, ``Trivia.col``) are 0-based, matching
``Marker::col()`` and ruamel's ``Mark.column``.

The classes compare by value, so a whole record tree can be asserted against a hand-built
one, and they ``repr`` only their non-default fields so a failing assert stays readable.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = [
    'KIND_ALIAS',
    'KIND_MAPPING',
    'KIND_NAMES',
    'KIND_SCALAR',
    'KIND_SEQUENCE',
    'STYLE_BLOCK',
    'STYLE_DOUBLE',
    'STYLE_FLOW',
    'STYLE_FOLDED',
    'STYLE_LITERAL',
    'STYLE_NAMES',
    'STYLE_PLAIN',
    'STYLE_SINGLE',
    'Doc',
    'EmitOptions',
    'Node',
    'Trivia',
]

# --- kind ------------------------------------------------------------------------------

KIND_SCALAR: Final = 0
KIND_SEQUENCE: Final = 1
KIND_MAPPING: Final = 2
KIND_ALIAS: Final = 3

KIND_NAMES: Final = ('SCALAR', 'SEQUENCE', 'MAPPING', 'ALIAS')

# --- style -----------------------------------------------------------------------------
# 0..4 are `yamluna_scanner::ScalarStyle` in declaration order; 5..6 are the collection
# styles (`StructureStyle`), which cannot collide because a node is never both.

STYLE_PLAIN: Final = 0
STYLE_SINGLE: Final = 1
STYLE_DOUBLE: Final = 2
STYLE_LITERAL: Final = 3
STYLE_FOLDED: Final = 4
STYLE_BLOCK: Final = 5
STYLE_FLOW: Final = 6

STYLE_NAMES: Final = ('PLAIN', 'SINGLE', 'DOUBLE', 'LITERAL', 'FOLDED', 'BLOCK', 'FLOW')


def _boring(value: Any) -> bool:
    """True for a value not worth printing: ``None``, an empty list/tuple, or ``0``.

    ``False`` and ``''`` are printed -- ``own_line=False`` and an empty scalar both mean
    something.  ``bool`` is not caught by the ``int`` test because ``type(False) is bool``.
    """
    return value is None or value == [] or value == () or (type(value) is int and value == 0)


class _Record:
    """Value semantics and a readable ``repr`` for the record classes below."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not self.__class__:
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name) for name in self.__slots__)

    __hash__ = None  # type: ignore[assignment]  # mutable, like list and dict

    def _show(self, name: str, value: Any) -> str:
        return repr(value)

    def __repr__(self) -> str:
        fields = ', '.join(
            f'{name}={self._show(name, value)}'
            for name in self.__slots__
            if not _boring(value := getattr(self, name))
        )
        return f'{type(self).__name__}({fields})'


class Node(_Record):
    """One YAML node.  See the module docstring for what each field carries."""

    __slots__ = (
        'kind',
        'style',
        'anchor',
        'tag',
        'value',
        'raw',
        'line',
        'col',
        'children',
        'merge',
        'explicit',
        'before',
        'eol',
        'inner',
        'after',
        'tag_first',
    )

    kind: int
    style: int
    anchor: str | None
    tag: tuple[str, str, str] | None
    value: str | None
    raw: str | None
    line: int
    col: int
    children: list[int]
    merge: list[int]
    explicit: list[int]
    before: list[Trivia]
    eol: Trivia | None
    inner: list[Trivia]
    after: list[Trivia]
    tag_first: bool

    def __init__(
        self,
        kind: int = KIND_SCALAR,
        style: int = STYLE_PLAIN,
        anchor: str | None = None,
        tag: tuple[str, str, str] | None = None,
        value: str | None = None,
        raw: str | None = None,
        line: int = 0,
        col: int = 0,
        children: list[int] | None = None,
        merge: list[int] | None = None,
        explicit: list[int] | None = None,
        before: list[Trivia] | None = None,
        eol: Trivia | None = None,
        inner: list[Trivia] | None = None,
        after: list[Trivia] | None = None,
        tag_first: bool = False,
    ) -> None:
        self.kind = kind
        self.style = style
        self.anchor = anchor
        self.tag = tag
        self.value = value
        self.raw = raw
        self.line = line
        self.col = col
        self.children = [] if children is None else children
        self.merge = [] if merge is None else merge
        self.explicit = [] if explicit is None else explicit
        self.before = [] if before is None else before
        self.eol = eol
        self.inner = [] if inner is None else inner
        self.after = [] if after is None else after
        self.tag_first = tag_first

    def _show(self, name: str, value: Any) -> str:
        if name == 'kind' and 0 <= value < len(KIND_NAMES):
            return KIND_NAMES[value]
        if name == 'style' and 0 <= value < len(STYLE_NAMES):
            return STYLE_NAMES[value]
        return repr(value)


class Trivia(_Record):
    """A comment or a run of blank lines.  Blank lines are first class, not embedded
    newlines inside comment text (DESIGN.md §2.1)."""

    __slots__ = ('text', 'own_line', 'col', 'blank_lines')

    text: str | None
    own_line: bool
    col: int
    blank_lines: int

    def __init__(
        self,
        text: str | None = None,
        own_line: bool = True,
        col: int = 0,
        blank_lines: int = 0,
    ) -> None:
        self.text = text
        self.own_line = own_line
        self.col = col
        self.blank_lines = blank_lines


class Doc(_Record):
    """One document of the stream: the node arena plus everything outside the root."""

    __slots__ = (
        'version',
        'tag_directives',
        'explicit_start',
        'explicit_end',
        'root',
        'nodes',
        'leading',
        'trailing',
        'bom',
        'final_line_break',
        'tags_before_version',
    )

    version: tuple[int, int] | None
    tag_directives: list[tuple[str, str]]
    explicit_start: bool
    explicit_end: bool
    root: int | None
    nodes: list[Node]
    leading: list[Trivia]
    trailing: list[Trivia]
    bom: bool
    final_line_break: bool
    tags_before_version: int

    def __init__(
        self,
        version: tuple[int, int] | None = None,
        tag_directives: list[tuple[str, str]] | None = None,
        explicit_start: bool = False,
        explicit_end: bool = False,
        root: int | None = None,
        nodes: list[Node] | None = None,
        leading: list[Trivia] | None = None,
        trailing: list[Trivia] | None = None,
        bom: bool = False,
        final_line_break: bool = True,
        tags_before_version: int = 0,
    ) -> None:
        self.version = version
        self.tag_directives = [] if tag_directives is None else tag_directives
        self.explicit_start = explicit_start
        self.explicit_end = explicit_end
        self.root = root
        self.nodes = [] if nodes is None else nodes
        self.leading = [] if leading is None else leading
        self.trailing = [] if trailing is None else trailing
        self.bom = bom
        self.final_line_break = final_line_break
        self.tags_before_version = tags_before_version


class EmitOptions(_Record):
    """Emitter knobs (DESIGN.md §2.4).  Defaults are ruamel's round-trip defaults."""

    __slots__ = (
        'map_indent',
        'seq_indent',
        'seq_offset',
        'width',
        'line_break',
        'explicit_start',
        'explicit_end',
        'default_flow_style',
        'canonical',
        'preserve_quotes',
    )

    map_indent: int
    seq_indent: int
    seq_offset: int
    width: int
    line_break: str
    explicit_start: bool
    explicit_end: bool
    default_flow_style: bool
    canonical: bool
    preserve_quotes: bool

    def __init__(
        self,
        map_indent: int = 2,
        seq_indent: int = 2,
        seq_offset: int = 0,
        width: int = 80,
        line_break: str = '\n',
        explicit_start: bool = False,
        explicit_end: bool = False,
        default_flow_style: bool = False,
        canonical: bool = False,
        preserve_quotes: bool = False,
    ) -> None:
        self.map_indent = map_indent
        self.seq_indent = seq_indent
        self.seq_offset = seq_offset
        self.width = width
        self.line_break = line_break
        self.explicit_start = explicit_start
        self.explicit_end = explicit_end
        self.default_flow_style = default_flow_style
        self.canonical = canonical
        self.preserve_quotes = preserve_quotes
