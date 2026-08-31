"""Python tree -> FFI records (DESIGN.md §3) — the inverse of :mod:`yamluna.constructor`.

``represent(data)`` returns one :class:`~yamluna._record.Doc`: a flat node arena the Rust
emitter turns into text.  Nothing here formats YAML — the *choice* of scalar style is made
here, the rendering is not (DESIGN.md §0).

**Byte-exactness.**  Every scalar type in the package carries the lexeme it was loaded from
(``ScalarString.lexeme()`` and friends).  When it is still there, it goes into ``Node.raw``
and the emitter reproduces it verbatim; when the value was built or edited in Python the
lexeme is gone, ``raw`` is ``None``, and the emitter formats from ``style`` + ``value``.
That is the whole mechanism behind "an untouched load → dump is byte-identical".

A plain scalar whose builtin re-renders its lexeme exactly (``hello``, ``12``, ``true``)
comes back from the constructor as a bare ``str``/``int``/``bool`` with no lexeme to carry,
so ``raw`` is ``None`` and the text is rebuilt from ``value`` — the same bytes.  Everything
whose spelling a builtin *cannot* reproduce (``+12``, ``0X1F``, ``1_000.5``, ``yes``,
``2002-1-4``) is a lexeme-carrying scalar type, which is what keeps the round trip exact.

**Trivia.**  Read off ``.ca`` through the identity-keyed store, never by re-deriving from
indices, and never by *touching* the object: a dump is a read (DIVERGENCES A8), so every
access here is a ``getattr(obj, _attrib, None)`` that cannot create the attribute it looks
for.  The slot mapping is the bridge table in :mod:`yamluna.comments`:

======  =====================================================================
slot    source
======  =====================================================================
before  the parent's record for this entry, ``C_KEY_PRE`` / ``C_ELEM_PRE``;
        for a mapping *value* and for the document root, the node's own
        ``ca.comment[1]`` — that is where the constructor put both
eol     the parent's record, ``C_VALUE_EOL`` (``C_ELEM_EOL`` in a sequence),
        or the node's own ``ca.comment[0]`` when the parent has none
inner   the node's own ``ca.comment[1]``, for every other position
after   the node's own ``ca.end``, then the parent's ``C_VALUE_POST``
======  =====================================================================

``ca.comment[1]`` is one list holding what the records keep in two slots, so for the two
positions where the constructor merges ``before`` into it the split cannot be recovered and
resolves to ``before`` — the common case by far (a comment block above a nested mapping).
Likewise ``C_VALUE_POST`` holds a scalar value's ``before`` *and* ``after``, and comes back
as ``after``.  Both are properties of ``.ca``'s shape, not of this module.

**Anchors.**  ``&name`` at the first occurrence of an object, ``*name`` at every later one,
by ``id()``.  An anchor that is *set* is always emitted, even if referenced once — it is
source text, and deleting it is not a round trip (DIVERGENCES B1); ``always_dump`` is
honoured but consequently redundant for loaded anchors.  A shared object with no anchor of
its own gets a generated ``id001``.  Recursive structures terminate: the name is recorded
before the subtree is walked, so a back-reference is an alias.

**Merge keys** are re-emitted as ``<<: *base``, never expanded (``Node.merge`` marks them).

**Tags** come from :meth:`~yamluna.registry.TagRegistry.plan`, called with the registered
classes this document actually uses; its ``%TAG`` directives go on ``Doc.tag_directives``
and the per-node string on ``Node.tag`` (DESIGN.md §5.3).  A class with a ``to_yaml``
classmethod gets to build its own node through :meth:`_Representer.represent_scalar` /
``represent_mapping`` / ``represent_sequence``, keeping ruamel's hook signature.

**Positions.**  ``Node.line`` / ``Node.col`` are the source positions the emitter echoes an
untouched node at, and they come from ``.lc``: a container's own, and for every scalar the
parent's ``lc.key`` / ``lc.value`` / ``lc.item`` -- a bare ``str`` or ``int`` has nowhere to
keep one.  An alias site is placed by its parent only, never by the object, which remembers
where its *anchor* was written.  A tree the user built has no ``.lc`` and gets no positions,
so it is laid out from ``EmitOptions`` instead; a tree that was edited has stale ones, and
the emitter stops believing recorded lines at the first construct that misses.
"""

from __future__ import annotations

import base64
import datetime
import math
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from yamluna._record import (
    KIND_ALIAS,
    KIND_MAPPING,
    KIND_SCALAR,
    KIND_SEQUENCE,
    STYLE_BLOCK,
    STYLE_DOUBLE,
    STYLE_FLOW,
    STYLE_FOLDED,
    STYLE_LITERAL,
    STYLE_PLAIN,
    STYLE_SINGLE,
    Doc,
    Node,
    Trivia,
)
from yamluna.comments import (
    C_ELEM_EOL,
    C_ELEM_POST,
    C_ELEM_PRE,
    C_KEY_EOL,
    C_KEY_PRE,
    C_VALUE_EOL,
    C_VALUE_POST,
    CommentedBase,
    CommentedMap,
    CommentedSeq,
    CommentToken,
    Tag,
    TaggedScalar,
    anchor_attrib,
    comment_attrib,
    format_attrib,
    line_col_attrib,
    merge_attrib,
    tag_attrib,
    trivia_attrib,
)
from yamluna.constructor import (
    DOC_ATTRIB,
    EXPLICIT_ATTRIB,
    FLOW_SEPS_ATTRIB,
    NULL_ATTRIB,
    SOURCE_ATTRIB,
    UNRESOLVED,
    resolve,
)
from yamluna.error import RepresenterError
from yamluna.registry import TagRegistry, WirePlan
from yamluna.registry import _split as _split_tag
from yamluna.scalarbool import ScalarBoolean
from yamluna.scalarfloat import ScalarFloat
from yamluna.scalarint import ScalarInt
from yamluna.scalarstring import ScalarString
from yamluna.timestamp import TimeStamp

__all__ = ['represent', 'represent_all']

#: ruamel's style indicator -> the record's style constant.
_STYLE_BY_INDICATOR: dict[str | None, int] = {
    '|': STYLE_LITERAL,
    '>': STYLE_FOLDED,
    "'": STYLE_SINGLE,
    '"': STYLE_DOUBLE,
    '': STYLE_PLAIN,
    None: STYLE_PLAIN,
}

#: `c-indicator`: a plain scalar may not start with one of these.  `-`, `?` and `:` only
#: matter when a space follows, which :func:`_plain_ok` checks separately.
_INDICATOR_START = frozenset('-?:,[]{}#&*!|>\'"%@`')

#: Anything a scalar can be.  Everything else is a container, an alias target, or an
#: object for the tag registry.  `datetime.date` covers `datetime` and `TimeStamp`.
_ATOMS = (str, bytes, bytearray, int, float, complex, datetime.date, datetime.time, type(None))

_SET_TAG = ('!!', 'set', 'tag:yaml.org,2002:set')
_BINARY_TAG = ('!!', 'binary', 'tag:yaml.org,2002:binary')


# -- scalar style choice ------------------------------------------------------------------


def _plain_ok(text: str, version: tuple[int, int] | None = None) -> bool:
    """Can `text` be written as a plain scalar in block context and mean the same thing?

    "Would it load back as this string" is answered by the loader's own
    :func:`~yamluna.constructor.resolve`, so the two directions cannot drift apart: whatever
    the constructor would turn into a bool, an int or a timestamp gets quoted here — and
    `yes` under YAML 1.2, which resolves to a string, does not.

    The emitter has the final say per context — a value that is plain-safe in a block
    mapping may still need quoting inside a flow collection — but it may only ever quote
    *more* than this, never less.
    """
    if not text:
        return False
    if text[0] in ' \t' or text[-1] in ' \t':
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        return False
    if text[0] in _INDICATOR_START and (
        text[0] not in '-?:' or len(text) == 1 or text[1] in ' \t'
    ):
        return False
    if ': ' in text or ' #' in text or text[-1] == ':':
        return False
    return resolve(text, version) is UNRESOLVED


def _quoted_style(text: str) -> int:
    """The cheapest quoting that survives a reparse: single unless something needs escaping."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        return STYLE_DOUBLE
    return STYLE_SINGLE


def _float_text(value: float) -> str:
    if math.isnan(value):
        return '.nan'
    if math.isinf(value):
        return '-.inf' if value < 0 else '.inf'
    return repr(value)


def _scalar(obj: Any, version: tuple[int, int] | None = None) -> tuple[int, str, str | None]:
    """``(style, cooked value, source lexeme or None)`` for one scalar object."""
    if obj is None:
        # `null`'s lexeme is the empty one: `key:` with nothing after it.  It goes in as a
        # lexeme rather than as a bare empty value so the emitter writes nothing rather than
        # the `''` an empty *string* has to be written as.
        return STYLE_PLAIN, '', ''
    if isinstance(obj, TaggedScalar):
        return _STYLE_BY_INDICATOR.get(obj.style, STYLE_PLAIN), obj.value, None
    if isinstance(obj, ScalarString):
        raw, text = obj.lexeme(), str(obj)
        style = _STYLE_BY_INDICATOR[obj.style]
        if raw is None and style == STYLE_PLAIN and not _plain_ok(text, version):
            style = _quoted_style(text)  # the value changed under the style; keep the value
        return style, text, raw
    if isinstance(obj, (ScalarInt, ScalarFloat, ScalarBoolean, TimeStamp)):
        return STYLE_PLAIN, obj.lexeme(), getattr(obj, '_lexeme', None)
    if isinstance(obj, str):
        return (STYLE_PLAIN if _plain_ok(obj, version) else _quoted_style(obj)), str(obj), None
    if isinstance(obj, bool):
        return STYLE_PLAIN, 'true' if obj else 'false', None
    if isinstance(obj, int):
        return STYLE_PLAIN, str(obj), None
    if isinstance(obj, float):
        return STYLE_PLAIN, _float_text(obj), None
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return STYLE_PLAIN, obj.isoformat(), None
    if isinstance(obj, (bytes, bytearray)):
        # `!!binary`; the tag comes from `_tag_of`, which reads `_BINARY_TAG` for bytes.
        return STYLE_DOUBLE, base64.b64encode(bytes(obj)).decode('ascii'), None
    raise RepresenterError(f'cannot represent an object: {obj!r}')


# -- reading the object model, without writing to it --------------------------------------


def _anchor_name(obj: Any) -> str | None:
    anchor = getattr(obj, anchor_attrib, None)
    return None if anchor is None else anchor.value


def _trackable(obj: Any) -> bool:
    """Can this object be aliased?  Containers always; scalars only once anchored.

    Keying scalars on ``id()`` unconditionally would alias interned small ints and strings
    to each other, which is why ruamel refuses to alias scalars at all.
    """
    return not isinstance(obj, _ATOMS) or _anchor_name(obj) is not None


def _state(obj: Any) -> Mapping[Any, Any]:
    """The attribute mapping a registered class is represented from (ruamel's rule)."""
    state = obj.__getstate__() if hasattr(obj, '__getstate__') else getattr(obj, '__dict__', None)
    return state if isinstance(state, Mapping) else {}


def _children(obj: Any) -> Iterator[Any]:
    """Everything reachable from `obj`, for the shared-object pre-pass."""
    if isinstance(obj, Mapping):
        # merged-in keys are the merge target's, not this mapping's: walking them here
        # would count the target's values twice and anchor them for no reason
        items = obj.non_merged_items() if isinstance(obj, CommentedMap) else obj.items()
        for key, value in items:
            yield key
            yield value
        yield from getattr(obj, merge_attrib, None) or ()
    elif isinstance(obj, (set, frozenset, list, tuple)):
        yield from obj
    elif not isinstance(obj, _ATOMS):
        yield from _state(obj).values()


def _lc(obj: Any) -> tuple[int, int] | None:
    """`obj`'s own recorded source position, without creating a ``.lc`` (A8)."""
    lc = getattr(obj, line_col_attrib, None)
    if lc is None or lc.line is None or lc.col is None:
        return None
    return lc.line, lc.col


def _lc_of(container: Any, key: Any, which: str) -> tuple[int, int] | None:
    """The position `container` recorded for one of its children.

    A bare ``str``/``int`` has nowhere to hold its own position, so the parent's ``.lc`` is
    where most scalars get theirs back.  It is also the only right answer for an *alias*
    site: the object knows where its anchor was written, not where the ``*name`` is.
    """
    lc = getattr(container, line_col_attrib, None)
    if lc is None:
        return None
    try:
        found: tuple[int, int] | None = getattr(lc, which)(key)
    except TypeError:  # an unhashable key was never recorded
        return None
    return found


def _in(keys: Any, key: Any) -> bool:
    """``key in keys``, for a `key` that may not be hashable (and so cannot be in there)."""
    try:
        return key in keys
    except TypeError:
        return False


def _flow_style(obj: Any, default: bool) -> int:
    """``.fa`` wins over the caller's default -- that is ruamel's rule and it is correct."""
    fa = getattr(obj, format_attrib, None)
    flow = default if fa is None else fa.flow_style(default)
    return STYLE_FLOW if flow else STYLE_BLOCK


def _record_of(container: Any, key: Any) -> list[Any] | None:
    """The 4-slot trivia record for one entry of `container`, or ``None``."""
    store = getattr(container, trivia_attrib, None)
    if not store:
        return None
    if isinstance(store, list):
        return store[key] if isinstance(key, int) and -len(store) <= key < len(store) else None
    try:
        record: list[Any] | None = store.get(key)
    except TypeError:  # an unhashable key cannot have a record
        return None
    return record


def _null_lexeme(container: Any, key: Any) -> str | None:
    """How `container` recorded its ``None`` child at `key` being spelled (:data:`NULL_ATTRIB`)."""
    store = getattr(container, NULL_ATTRIB, None)
    if not store:
        return None
    try:
        lexeme: str | None = store.get(key)
    except TypeError:  # an unhashable key was never recorded
        return None
    return lexeme


def _flow_seps(obj: Any) -> list[str]:
    """What the source wrote between a flow collection's lexemes (:data:`FLOW_SEPS_ATTRIB`)."""
    return list(getattr(obj, FLOW_SEPS_ATTRIB, None) or ())


def _source_of(container: Any, key: Any) -> tuple[Any, Node] | None:
    """The ``(value, node)`` `container` recorded for a child (:data:`SOURCE_ATTRIB`)."""
    store = getattr(container, SOURCE_ATTRIB, None)
    if not store:
        return None
    try:
        found: tuple[Any, Node] | None = store.get(key)
    except TypeError:  # an unhashable key was never recorded
        return None
    return found


def _tokens(value: Any) -> list[CommentToken]:
    if value is None:
        return []
    if isinstance(value, CommentToken):
        return [value]
    return [t for t in value if isinstance(t, CommentToken)]


def _trivia_list(value: Any) -> list[Trivia]:
    """``CommentToken``s -> trivia records, collapsing blank tokens into ``BlankLines``."""
    out: list[Trivia] = []
    for token in _tokens(value):
        if token.is_blank_line:
            count = max(1, token.value.count('\n'))
            if out and out[-1].text is None:
                out[-1].blank_lines += count
            else:
                out.append(Trivia(blank_lines=count))
            continue
        own_line = token.value.endswith('\n')
        text = token.value[:-1] if own_line else token.value
        if not text.startswith('#'):
            text = text.lstrip(' \t')  # the record's text starts at the `#`; `col` positions it
        out.append(Trivia(text, own_line, token.column))
    return out


def _stream_trivia(carried: Doc | None, root: Node) -> tuple[list[Trivia], list[Trivia]]:
    """Take the *stream*'s own trivia back off the root node.

    A comment above `---` and one below `...` belong to the document, not to its root: the
    emitter writes them outside the directives and the markers.  ``.ca`` has no slot for
    them, so the constructor folds them into the root's own comments and the loaded document
    record -- parked on the root by :data:`~yamluna.constructor.DOC_ATTRIB` -- is what says
    how many of them there were.  A prefix or suffix the user has since edited no longer
    matches and simply stays on the root, which is the safe way to be wrong.

    The prefix comes back off ``before`` for a block root and off ``inner`` for a flow one:
    :func:`_leading_is_before` must leave a flow collection's ``inner`` alone (a comment there
    sits *after* the bracket), so that is where the fold is still parked.  Either way the
    document's leading comments are outside the root, never inside it.
    """
    if carried is None:
        return [], []
    leading, trailing = list(carried.leading), list(carried.trailing)
    for slot in ('before', 'inner'):
        run = getattr(root, slot)
        if leading and run[: len(leading)] == leading:
            setattr(root, slot, run[len(leading) :])
            break
    else:
        leading = []
    if trailing and root.after[-len(trailing) :] == trailing:
        root.after = root.after[: -len(trailing)]
    else:
        trailing = []
    return leading, trailing


def _leading_is_before(node: Node) -> None:
    """Move ``inner`` to ``before``: ``.ca`` cannot tell the two apart (see the module doc).

    Only for a *block* collection, where the two render identically -- it starts on the line
    after its parent's ``:``, so a comment before it and a comment inside it both sit on their
    own lines above the first child.  A flow collection opens with a bracket on the parent's
    line, and there the distinction is the whole layout::

        flow_map: {        # `inner`: the comment is after the `{`
          # comment
          x: 1, ... }

        flow_map:          # `before`: the comment pushes the `{` onto the next line
          # comment
          {x: 1, ... }

    so promoting `inner` there rewrites the source.  `.ca` keeps the slot for a flow
    collection, so nothing is lost by leaving it alone.
    """
    if node.style == STYLE_FLOW:
        return
    node.before, node.inner = node.inner + node.before, []


def _trivia_one(value: Any) -> Trivia | None:
    found = _trivia_list(value)
    return found[0] if found else None


def _entries(obj: Mapping[Any, Any]) -> list[tuple[Any, Any, bool]]:
    """``(key, value, is_merge)`` in emission order.

    Merged-in keys are *not* entries: they belong to the mapping ``<<`` points at.  The
    ``<<`` entry goes back where it was (``MergeList.merge_pos``) with its value left as an
    alias, so the dump says ``<<: *base`` instead of expanding it.
    """
    if not isinstance(obj, CommentedMap):
        return [(k, v, False) for k, v in obj.items()]
    entries = [(k, v, False) for k, v in obj.non_merged_items()]
    merged = getattr(obj, merge_attrib, None)
    if not merged:
        return entries
    value: Any = merged[0]
    if len(merged) > 1:  # `<<: [*a, *b]` -- a flow sequence of aliases
        value = CommentedSeq(merged)
        value.fa.set_flow_style()
    entries.insert(getattr(merged, 'merge_pos', 0), ('<<', value, True))
    return entries


# -- the walk -----------------------------------------------------------------------------


class _Representer:
    """One document's worth of state: the arena, the anchor bookkeeping, the wire plan."""

    __slots__ = ('_counter', 'default_flow_style', 'names', 'nodes', 'plan', 'registry',
                 'shared', 'taken', 'used', 'version')

    def __init__(self, registry: TagRegistry | None, default_flow_style: bool) -> None:
        self.registry = registry
        self.default_flow_style = default_flow_style
        self.nodes: list[Node] = []
        self.shared: dict[int, int] = {}  # id -> occurrences, from the pre-pass
        self.names: dict[int, str] = {}  # id -> anchor, once its definition is under way
        self.taken: set[str] = set()
        self.used: list[type] = []
        self.plan: WirePlan = WirePlan((), {})
        self.version: tuple[int, int] | None = None
        self._counter = 0

    # -- entry point ----------------------------------------------------------------------

    def document(
        self,
        data: Any,
        *,
        version: tuple[int, int] | None = None,
        explicit_start: bool = False,
        explicit_end: bool = False,
        carried: Doc | None = None,
    ) -> Doc:
        # `%YAML`, `%TAG`, `---` and `...` belong to the document, not to the root object;
        # the constructor parks them on the root and this is where they come back.  An
        # explicit argument still wins, and so does `YAML.explicit_start`, which `main.py`
        # applies to the finished record.  `carried` is the record for a document that has
        # no root object to park anything on -- `YAML._empty`, see that module's docstring.
        if carried is None:
            carried = getattr(data, DOC_ATTRIB, None)
        elif data is None:
            # An empty document has no content to re-represent: the record *is* the
            # document, comments, `---` and all.  Copied, because a dump is a read (A8).
            return Doc(
                version=carried.version if version is None else version,
                tag_directives=list(carried.tag_directives),
                explicit_start=explicit_start or carried.explicit_start,
                explicit_end=explicit_end or carried.explicit_end,
                root=carried.root,
                nodes=list(carried.nodes),
                leading=list(carried.leading),
                trailing=list(carried.trailing),
                bom=carried.bom,
                final_line_break=carried.final_line_break,
                tags_before_version=carried.tags_before_version,
            )
        if carried is not None:
            version = carried.version if version is None else version
            explicit_start = explicit_start or carried.explicit_start
            explicit_end = explicit_end or carried.explicit_end
            bom, final_line_break = carried.bom, carried.final_line_break
        else:
            bom, final_line_break = False, True
        self.version = version  # `%YAML 1.1` puts `yes` and `on` back in the boolean set
        self._scan(data, set())
        if self.registry is not None and self.used:
            self.plan = self.registry.plan(self.used)
        root = self._add(Node(value='null')) if data is None else self._emit(data)
        _leading_is_before(self.nodes[root])  # no parent holds the root's leading comments
        leading, trailing = _stream_trivia(carried, self.nodes[root])
        return Doc(
            version=version,
            tag_directives=self._directives(carried),
            explicit_start=explicit_start,
            explicit_end=explicit_end,
            root=root,
            nodes=self.nodes,
            leading=leading,
            trailing=trailing,
            bom=bom,
            final_line_break=final_line_break,
            # Where the `%YAML` line sat among the `%TAG` lines. A plan's directives are
            # appended, so they land below it exactly as the source's own tail did.
            tags_before_version=0 if carried is None else carried.tags_before_version,
        )

    def _directives(self, carried: Doc | None) -> list[tuple[str, str]]:
        """The document's ``%TAG`` lines: the source's, in source order, plus the plan's.

        A handle the source declared and the wire plan also wants keeps its place on the page
        and takes the plan's prefix; a handle only the plan wants is appended (DESIGN 5.3).
        """
        planned = {d.handle: d.prefix for d in self.plan.directives}
        out = [
            (handle, planned.pop(handle, prefix))
            for handle, prefix in (() if carried is None else carried.tag_directives)
        ]
        return out + list(planned.items())

    # -- pre-pass: which objects are shared, which registered classes are used -------------

    def _scan(self, obj: Any, seen: set[int]) -> None:
        if self.registry is not None and self.registry.registration_for(type(obj)) is not None:
            self.used.append(type(obj))
        name = _anchor_name(obj)
        if name:
            self.taken.add(name)
        if not _trackable(obj):
            return
        key = id(obj)
        self.shared[key] = self.shared.get(key, 0) + 1
        if key in seen:
            return  # a cycle, or a second reference: counted, not re-walked
        seen.add(key)
        for child in _children(obj):
            self._scan(child, seen)

    # -- main pass ------------------------------------------------------------------------

    def _add(self, node: Node) -> int:
        self.nodes.append(node)
        return len(self.nodes) - 1

    def _generate(self) -> str:
        while True:
            self._counter += 1
            name = f'id{self._counter:03d}'
            if name not in self.taken:
                self.taken.add(name)
                return name

    def _emit(self, obj: Any) -> int:
        """Append `obj`'s subtree to the arena, pre-order; return its index.

        The node also gets `obj`'s recorded source position, which is what lets the emitter
        reproduce the file's own indentation instead of laying the node out afresh.  A stale
        position cannot open a hole in the output -- the emitter stops believing recorded
        lines at the first construct that does not land on one -- so an edited tree degrades
        to the layout path rather than to garbage.
        """
        index = self._emit_node(obj)
        node = self.nodes[index]
        # An alias site is not where its anchor was written; only the parent can place it.
        if node.kind != KIND_ALIAS and (pos := _lc(obj)) is not None:
            node.line, node.col = pos
        return index

    def _at(self, index: int, pos: tuple[int, int] | None) -> None:
        """Give a node the position its *parent* recorded, when it carries none itself."""
        node = self.nodes[index]
        if pos is not None and node.line == 0 and node.col == 0:
            node.line, node.col = pos

    def _emit_node(self, obj: Any) -> int:
        if not _trackable(obj):
            return self._add(self._scalar_node(obj))
        key = id(obj)
        if key in self.names:
            return self._add(Node(KIND_ALIAS, anchor=self.names[key]))
        anchor = _anchor_name(obj)
        if anchor is None and self.shared.get(key, 0) > 1:
            anchor = self._generate()
        if anchor is not None:
            self.names[key] = anchor  # recorded *before* the walk: recursion becomes an alias
        return self._build(obj, anchor)

    #: ruamel's hook name, kept so ported classes work unchanged.
    represent_data = _emit

    def _build(self, obj: Any, anchor: str | None) -> int:
        if isinstance(obj, Mapping):
            return self._mapping(obj, anchor)
        if isinstance(obj, (set, frozenset)):
            return self._set(obj, anchor)
        if isinstance(obj, (list, tuple)):
            return self._sequence(obj, anchor)
        if isinstance(obj, _ATOMS):
            node = self._scalar_node(obj)
            node.anchor = anchor
            return self._add(node)
        return self._custom(obj, anchor)

    def _scalar_node(self, obj: Any) -> Node:
        style, value, raw = _scalar(obj, self.version)
        return Node(KIND_SCALAR, style, value=value, raw=raw, tag=self._tag_of(obj))

    def _mapping(self, obj: Mapping[Any, Any], anchor: str | None) -> int:
        node = Node(KIND_MAPPING, _flow_style(obj, self.default_flow_style),
                    anchor=anchor, tag=self._tag_of(obj), flow_seps=_flow_seps(obj))
        index = self._add(node)
        self._own_trivia(obj, node)
        explicit = getattr(obj, EXPLICIT_ATTRIB, None) or frozenset()
        for key, value, is_merge in _entries(obj):
            record = _record_of(obj, key)
            key_index = self._emit(key)
            self._at(key_index, _lc_of(obj, key, 'key'))
            self._entry_trivia(self.nodes[key_index], record, C_KEY_PRE, C_KEY_EOL, None)
            value_index = self._emit(value)
            self._at(value_index, _lc_of(obj, key, 'value'))
            if value is None:
                self._spell_null(value_index, _null_lexeme(obj, key))
            self._respell(value_index, value, _source_of(obj, key))
            _leading_is_before(self.nodes[value_index])  # a value has no `before` slot of its own
            self._entry_trivia(self.nodes[value_index], record, None, C_VALUE_EOL, C_VALUE_POST)
            if is_merge:
                node.merge.append(len(node.children))
            elif _in(explicit, key):
                node.explicit.append(len(node.children))
            node.children += [key_index, value_index]
        return index

    def _sequence(self, obj: Any, anchor: str | None) -> int:
        node = Node(KIND_SEQUENCE, _flow_style(obj, self.default_flow_style),
                    anchor=anchor, tag=self._tag_of(obj), flow_seps=_flow_seps(obj))
        index = self._add(node)
        self._own_trivia(obj, node)
        for position, item in enumerate(obj):
            record = _record_of(obj, position)
            child = self._emit(item)
            self._at(child, _lc_of(obj, position, 'item'))
            if item is None:
                self._spell_null(child, _null_lexeme(obj, position))
            self._respell(child, item, _source_of(obj, position))
            self._entry_trivia(self.nodes[child], record, C_ELEM_PRE, C_ELEM_EOL, C_ELEM_POST)
            node.children.append(child)
        return index

    def _set(self, obj: Any, anchor: str | None) -> int:
        """A ``!!set``: a mapping whose values are all null."""
        node = Node(KIND_MAPPING, _flow_style(obj, self.default_flow_style),
                    anchor=anchor, tag=self._tag_of(obj) or _SET_TAG, flow_seps=_flow_seps(obj))
        index = self._add(node)
        self._own_trivia(obj, node)
        explicit = getattr(obj, EXPLICIT_ATTRIB, None) or frozenset()
        for member in obj:
            record = _record_of(obj, member)
            key_index = self._emit(member)
            self._at(key_index, _lc_of(obj, member, 'key'))
            self._entry_trivia(self.nodes[key_index], record, C_KEY_PRE, C_KEY_EOL, None)
            if _in(explicit, member):
                node.explicit.append(len(node.children))
            # `raw=''` is the *absent* value the emitter writes nothing for -- a set member
            # is `? a`, not `a: ''`.  It is the same empty lexeme `_scalar(None)` returns.
            absent = self._add(Node(KIND_SCALAR, STYLE_PLAIN, value='', raw=''))
            node.children += [key_index, absent]
        return index

    def _custom(self, obj: Any, anchor: str | None) -> int:
        cls = type(obj)
        written = self.plan.tags.get(cls)
        if written is None:
            raise RepresenterError(
                f'cannot represent an object: {obj!r}; register {cls.__module__}.'
                f'{cls.__qualname__} with YAML.register_class() first'
            )
        hook = getattr(cls, 'to_yaml', None)
        if hook is not None:
            index = hook(self, obj)
            if not isinstance(index, int) or not 0 <= index < len(self.nodes):
                raise RepresenterError(
                    f'{cls.__qualname__}.to_yaml must return what representer.represent_* '
                    f'returned, not {index!r}'
                )
            if self.nodes[index].anchor is None:
                self.nodes[index].anchor = anchor
            return index
        node = Node(KIND_MAPPING, _flow_style(obj, self.default_flow_style),
                    anchor=anchor, tag=self._triple(written, cls))
        index = self._add(node)
        for key, value in _state(obj).items():
            node.children += [self._emit(key), self._emit(value)]
        return index

    def _spell_null(self, index: int, lexeme: str | None) -> None:
        """Give a null node back the spelling it was loaded with (``~``, ``null``, ...).

        ``_scalar(None)`` can only produce the empty lexeme -- ``key:`` with nothing after
        it -- because ``None`` carries nothing; the parent remembers the rest.
        """
        if lexeme:
            node = self.nodes[index]
            node.value = node.raw = lexeme

    def _respell(self, index: int, value: Any, found: tuple[Any, Node] | None) -> None:
        """Give a scalar back the tag, anchor and lexeme its value has nowhere to keep.

        `found` is the parent's ``(value, node)`` record
        (:data:`~yamluna.constructor.SOURCE_ATTRIB`).  It is applied only while the entry
        still holds the value that was loaded -- an edited value is a new value and is
        written from scratch like any other -- and only over what the value did not already
        supply: a `TaggedScalar` brings its own tag and a `ScalarString` its own anchor.
        """
        if found is None or not isinstance(value, _ATOMS):
            return
        node = self.nodes[index]
        if node.kind != KIND_SCALAR:
            return
        was, src = found
        if type(was) is not type(value) or was != value:
            return
        if node.tag is None:
            # `tag_first` is which of `!!str &ta` and `&ta !!str` the source wrote: part of
            # how the tag was written, so it travels with it.
            node.tag, node.tag_first = src.tag, src.tag_first
        if node.anchor is None:
            node.anchor = src.anchor
        if src.raw is not None:
            # The record is the authority, not what the value could reconstruct: a
            # `ScalarFloat` built from `!!float "1.5"` remembers `1.5`, not the quotes, and
            # re-encoding `!!binary` loses the line wrapping of the payload.  The three
            # travel together -- the emitter reads a block scalar's header off the lexeme,
            # and the cooked value is what the lexeme meant.
            node.style, node.value, node.raw = src.style, src.value, src.raw

    # -- trivia ---------------------------------------------------------------------------

    def _own_trivia(self, obj: Any, node: Node) -> None:
        """The node's own `.ca`: `ca.comment[1]` -> inner, `ca.end` -> after."""
        if not isinstance(obj, CommentedBase):
            return
        ca = getattr(obj, comment_attrib, None)  # never `.ca`: that would create one (A8)
        if ca is None:
            return
        if ca.comment is not None:
            node.inner = _trivia_list(ca.comment[1] if len(ca.comment) > 1 else None)
            node.eol = _trivia_one(ca.comment[0])
        node.after = _trivia_list(ca.end)

    @staticmethod
    def _entry_trivia(
        node: Node, record: list[Any] | None, pre: int | None, eol: int, post: int | None
    ) -> None:
        """The parent's record for this entry: pre -> before, eol -> eol, post -> after."""
        if record is None:
            return
        if pre is not None:
            node.before = _trivia_list(record[pre]) + node.before
        if record[eol] is not None:
            node.eol = _trivia_one(record[eol])
        if post is not None:
            node.after = node.after + _trivia_list(record[post])

    # -- tags -----------------------------------------------------------------------------

    def _tag_of(self, obj: Any) -> tuple[str, str, str] | None:
        if isinstance(obj, (bytes, bytearray)):
            return _BINARY_TAG
        written = self.plan.tags.get(type(obj))
        if written is not None:
            return self._triple(written, type(obj))
        return self._tag_triple(getattr(obj, tag_attrib, None))

    def _triple(self, written: str, cls: type) -> tuple[str, str, str]:
        handle, suffix = _split_tag(written)
        registration = None if self.registry is None else self.registry.registration_for(cls)
        return handle or '!', suffix, registration.uri if registration else written

    @staticmethod
    def _tag_triple(tag: Tag | None) -> tuple[str, str, str] | None:
        if tag is None or not tag:
            return None
        handle, suffix = tag.handle, tag.suffix
        if handle is None:
            handle, suffix = _split_tag(str(tag.value))
            handle = handle or '!'
        return handle, suffix or '', tag.resolved or f'{handle}{suffix or ""}'

    # -- the `to_yaml` hook surface (ruamel's signatures) ----------------------------------

    def represent_scalar(
        self, tag: str | Tag, value: Any, style: str | None = None, anchor: str | None = None
    ) -> int:
        return self._add(Node(
            KIND_SCALAR,
            _STYLE_BY_INDICATOR.get(style, STYLE_PLAIN),
            anchor=anchor,
            tag=self._hook_tag(tag),
            value=str(value),
        ))

    def represent_mapping(
        self, tag: str | Tag, mapping: Mapping[Any, Any], flow_style: bool | None = None
    ) -> int:
        node = Node(KIND_MAPPING, STYLE_FLOW if flow_style else STYLE_BLOCK,
                    tag=self._hook_tag(tag))
        index = self._add(node)
        for key, value in mapping.items():
            node.children += [self._emit(key), self._emit(value)]
        return index

    def represent_sequence(
        self, tag: str | Tag, sequence: Any, flow_style: bool | None = None
    ) -> int:
        node = Node(KIND_SEQUENCE, STYLE_FLOW if flow_style else STYLE_BLOCK,
                    tag=self._hook_tag(tag))
        index = self._add(node)
        node.children += [self._emit(item) for item in sequence]
        return index

    def _hook_tag(self, tag: str | Tag | None) -> tuple[str, str, str] | None:
        if tag is None:
            return None
        if isinstance(tag, Tag):
            return self._tag_triple(tag)
        handle, suffix = _split_tag(tag)
        return handle or '!', suffix, tag


# -- public API ---------------------------------------------------------------------------


def represent(data: Any, yaml: Any = None, **options: Any) -> Doc:
    """One Python object -> one document record.

    `yaml` is an optional :class:`~yamluna.main.YAML` whose settings supply the defaults;
    keyword arguments (`registry`, `default_flow_style`, `version`, `explicit_start`,
    `explicit_end`) are :class:`_Representer`'s and override it.
    """
    settings = _settings(yaml, options)
    return _Representer(
        settings.pop('registry', None), settings.pop('default_flow_style', False)
    ).document(data, **settings)


def represent_all(documents: Iterable[Any], yaml: Any = None, **options: Any) -> list[Doc]:
    """A stream of Python objects -> the record list ``emit`` takes.

    Each document gets its own arena and its own ``%TAG`` directives: a class used in the
    second document does not put a directive on the first (DESIGN.md §5.3).
    """
    return [represent(data, yaml, **options) for data in documents]


def _settings(yaml: Any, overrides: dict[str, Any]) -> dict[str, Any]:
    """`yaml`'s settings, overridden by explicit keywords -- ``Constructor.for_yaml``'s rule."""
    if yaml is None:
        return overrides
    return {
        'registry': getattr(yaml, 'registry', None),
        'default_flow_style': bool(getattr(yaml, 'default_flow_style', False)),
        'version': getattr(yaml, 'version', None),
        **overrides,
    }
