"""The record types that cross the FFI boundary between the Rust core and Python.

Developer-facing, and load-bearing. This module is the single definition of that boundary.
The Rust extension imports it, caches one class object each for `Node`, `Trivia` and `Doc`,
calls those classes with positional arguments to build records on load, and reads their
attributes by name on dump:

```python
from yamluna._yamluna import parse, emit          # the Rust extension

docs: list[Doc] = parse(source, allow_duplicate_keys=False)
text: str = emit(docs, EmitOptions())
```

So the order of each `__init__`'s parameters and the spelling of every attribute are both
part of the contract. Changing either without changing `crates/yamluna-py` to match breaks
loading and dumping. The order of `__slots__` sets the order fields are compared and
printed in.

Nothing here imports anything but `typing`, and no class does any work in `__init__` beyond
storing its arguments: these objects are allocated one per YAML node, in a loop, on the
other side of the FFI.

The tree is flat. A `Doc` owns `nodes`, an arena; a node refers to its children by index
into that arena, never by reference. `root` is the index of the document's root node, or
`None` for a document with no content.

## What the fields carry

None of this is visible in the annotations.

* `Node.kind`: one of the `KIND_*` constants.
* `Node.style`: `STYLE_PLAIN`, `STYLE_SINGLE`, `STYLE_DOUBLE`, `STYLE_LITERAL` or
  `STYLE_FOLDED` for a scalar, `STYLE_BLOCK` or `STYLE_FLOW` for a sequence or a mapping,
  ignored for an alias.
* `Node.anchor`: for a scalar, sequence or mapping, the anchor this node defines, `&name`
  without the `&`; for `KIND_ALIAS`, the anchor it references, `*name` without the `*`,
  which is `NodeKind::Alias { anchor }` in the core. A node never does both.
* `Node.tag`: `(handle, suffix, resolved)` as written, such as
  `('!', 'Circuit', 'tag:libx/Circuit')`.
* `Node.value` and `Node.raw`: scalars only. `value` is cooked, escapes resolved and block
  scalars folded; `raw` is the source lexeme verbatim, quotes and block header included,
  and is what makes an unmutated round trip byte-exact. `raw` is `None` for a node the user
  constructed.
* `Node.children`: sequence items, or `k, v, k, v, ...` for a mapping. Empty for scalars
  and aliases.
* `Node.merge`: positions in `children` holding the key of a `<<` entry, so always even, in
  source order. The merge is left unexpanded and the Python layer resolves it lazily, so a
  dump re-emits `<<: *base`.
* `Node.explicit`: positions in `children` holding the key of an entry written in the
  explicit `? key` / `: value` form, so always even, in source order. Same shape as
  `merge`.
* `Node.tag_first`: the tag was written ahead of the anchor, `!!str &a v` rather than
  `&a !!str v`. YAML allows either order, so the order the source used has to be carried.
* `Node.flow_seps`: what a flow collection's source wrote between its lexemes, one run in
  front of each child and one in front of the closing bracket, so a recorded list is always
  `len(children) + 1` long. Each run is the white space, `,`, `:` and `?` verbatim, with
  comments taken out. It is what tells `[1, 2]` from `[1, 2, ]` from `[ 1 , 2 ]`, and which
  key of `{a: 1, b}` was written with no `:`. Empty for a collection the user built. An
  insertion or a deletion changes `children`, which is what stops a stale list from being
  believed.
* `Node.anchor_at`, `Node.tag_at`, `Node.header_at`, `Node.colon` and `Doc.line_space`:
  where the source put things. `anchor_at` and `tag_at` are the `(line, col)` of the
  `&anchor` and of the tag, which sit ahead of the node and so have positions of their own.
  `header_at` is the `(line, col)` of a block scalar's `|` or `>` header, which sits ahead
  of the body's own `line` and `col` and may be a line below the tag. `colon` is the
  `(line, col)` of each entry's `:`, one slot per entry in entry order, `None` where the
  source wrote none as in `{a: 1, b}`, and empty when nothing was recorded. `line_space` is
  `{0-based line: the line verbatim}` for the source lines the emitter cannot reproduce
  from a column alone, the ones holding a TAB and the ones ending in white space; it is a
  fact about the stream, so only the first document carries it.

    These five are opaque to the Python layer. It never reads them, it hands them back for
    a node it did not change, which is what `NODE_ATTRIB` in `yamluna.constructor` carries.
    The emitter believes a recorded position only while the output is still on the line it
    names, so a model that has been edited falls back to the layout path instead of writing
    them somewhere wrong.

* `Doc.tags_before_version`: how many of `tag_directives` were written above the `%YAML`
  line. The rest were written below it.
* `Doc.bom`: the stream began with a byte-order mark. Only ever true on the first document;
  the loader strips it and the emitter writes it back.
* `Doc.final_line_break`: the source ended with a line break. A file whose last line is an
  unterminated comment is the case this exists for.
* `Node.before`, `Node.eol`, `Node.inner`, `Node.after`: the four trivia slots, keyed by
  node identity rather than by index. `before` is the own-line trivia immediately ahead of
  the node, `eol` the end-of-line comment on its last line, `inner` the trivia between a
  collection's start token and its first child, and `after` a collection's trailing trivia,
  ahead of whatever its parent writes next.
* `Trivia`: either a comment, with `text` set and `blank_lines == 0`, or a run of blank
  lines, with `blank_lines > 0` and `text` at `None`. Comment `text` includes the leading
  `#` and excludes the line break; `own_line` is `False` for an end-of-line comment; `col`
  is the 0-based column of the `#`.

Positions (`Node.line`, `Node.col`, `Trivia.col`) are 0-based, matching `Marker::col()` and
ruamel's `Mark.column`.

The classes compare by value, so a whole record tree can be asserted against a hand-built
one, and they `repr` only their non-default fields so a failing assert stays readable.
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
"""The `KIND_*` codes as names, indexed by the code, for `repr`."""

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
"""The `STYLE_*` codes as names, indexed by the code, for `repr`."""


def _boring(value: Any) -> bool:
    """True for a value `repr` leaves out: `None`, an empty list, tuple or dict, or `0`.

    `False` and `''` are printed, because `own_line=False` and an empty scalar both mean
    something. A `bool` escapes the `int` test because `type(False) is bool`.
    """
    return value is None or value in ([], (), {}) or (type(value) is int and value == 0)


class _Record:
    """Value equality, and a `repr` that prints only the fields worth reading.

    A subclass lists its fields in `__slots__` and both work off that, so the two never
    drift from the field list the FFI uses.
    """

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not self.__class__:
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name) for name in self.__slots__)

    __hash__ = None  # type: ignore[assignment]  # mutable, like list and dict

    def _show(self, name: str, value: Any) -> str:
        """The text field `name` prints as inside `repr`. Subclasses override it."""
        return repr(value)

    def __repr__(self) -> str:
        fields = ', '.join(
            f'{name}={self._show(name, value)}'
            for name in self.__slots__
            if not _boring(value := getattr(self, name))
        )
        return f'{type(self).__name__}({fields})'


class Node(_Record):
    """One YAML node, its trivia and the positions the source wrote things at.

    The module docstring says what each field carries.
    """

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
        'flow_seps',
        'anchor_at',
        'tag_at',
        'header_at',
        'colon',
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
    flow_seps: list[str]
    anchor_at: tuple[int, int] | None
    tag_at: tuple[int, int] | None
    header_at: tuple[int, int] | None
    colon: list[tuple[int, int] | None]

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
        flow_seps: list[str] | None = None,
        anchor_at: tuple[int, int] | None = None,
        tag_at: tuple[int, int] | None = None,
        header_at: tuple[int, int] | None = None,
        colon: list[tuple[int, int] | None] | None = None,
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
        self.flow_seps = [] if flow_seps is None else flow_seps
        self.anchor_at = anchor_at
        self.tag_at = tag_at
        self.header_at = header_at
        self.colon = [] if colon is None else colon

    def _show(self, name: str, value: Any) -> str:
        """Prints `kind` and `style` by name, so a failing assert reads as YAML terms."""
        if name == 'kind' and 0 <= value < len(KIND_NAMES):
            return KIND_NAMES[value]
        if name == 'style' and 0 <= value < len(STYLE_NAMES):
            return STYLE_NAMES[value]
        return repr(value)


class Trivia(_Record):
    """A comment, or a run of blank lines.

    A run of blank lines is a record of its own. ruamel instead smuggles them into comment
    text as embedded newlines, which drifts comments onto the wrong node and leaves "how
    many blank lines" unanswerable.
    """

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
    """One document of the stream: the node arena, plus everything outside the root."""

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
        'directives_raw',
        'stream_tail',
        'line_space',
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

    directives_raw: tuple[str, int] | None
    """The directive region as written, and how many of `leading`'s trivia were read from
    inside it. `None` for a document with no `%` line. A directive line's spelling does not
    follow from its meaning, so it is carried verbatim."""

    stream_tail: str
    """White space the source ends with that no line break closes. Always `''` when
    `final_line_break` is set."""

    line_space: dict[int, str]
    """The source lines the emitter cannot reproduce from a column alone, by 0-based line.
    A fact about the stream, so only its first document carries it."""

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
        directives_raw: tuple[str, int] | None = None,
        stream_tail: str = '',
        line_space: dict[int, str] | None = None,
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
        self.directives_raw = directives_raw
        self.stream_tail = stream_tail
        self.line_space = {} if line_space is None else line_space


class EmitOptions(_Record):
    """The emitter settings, as one record for the FFI call.

    The defaults here are ruamel's round-trip defaults, and `YAML._emit_options` resolves
    every unset setting to one of them before building this.

    Three fields do not reach the core as they stand. `line_break` of `'\n'` becomes the
    core's automatic mode, which takes the break from the lexemes, so a CRLF file stays
    byte-identical through a default `YAML()`. `explicit_start`, `explicit_end` and
    `default_flow_style` are `bool` here and `Option<bool>` in the core, where `False`
    becomes "leave each document as it was" and only `True` overrides. `canonical` has no
    counterpart in the core emitter and is ignored.
    """

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
    """Columns a nested mapping is indented by."""

    seq_indent: int
    """Columns a sequence's items are indented by, measured from the key that holds them."""

    seq_offset: int
    """Columns the `-` itself is indented by, inside `seq_indent`."""

    width: int
    """Column to fold at."""

    line_break: str
    """The line break to write: `'\n'`, `'\r\n'` or `'\r'`. Anything else is a
    `ValueError` from the extension. `'\n'` means "whatever the source used"."""

    explicit_start: bool
    """Force `---` on every document. `False` leaves each document's own marker alone."""

    explicit_end: bool
    """Force `...` on every document. `False` leaves each document's own marker alone."""

    default_flow_style: bool
    """Force every collection into flow style. `False` leaves each node's own style."""

    canonical: bool
    """Accepted for ruamel compatibility. The emitter ignores it."""

    preserve_quotes: bool
    """Keep the quoting style of a modified scalar, where it is still legal."""

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
