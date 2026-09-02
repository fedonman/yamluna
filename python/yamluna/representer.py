"""Turns a Python object tree into the flat node records the Rust emitter writes.

This module is the inverse of `yamluna.constructor`.  `represent(data)` returns one `Doc`:
a node arena the emitter turns into text, plus the version, the tag directives and the
trivia that live outside the root.  No YAML text is produced here.  The *choice* of scalar
style is made in this module; the rendering is the emitter's.

**Byte-exactness.** Every scalar type in the package carries the lexeme it was loaded from
(`ScalarString.lexeme()` and friends).  While that lexeme is there it goes into `Node.raw`
and the emitter reproduces it verbatim; once the value has been built or edited in Python
the lexeme is gone, `raw` is `None`, and the emitter formats from `style` and `value`.  That
is the whole mechanism behind an untouched load then dump being byte-identical.

A plain scalar whose builtin re-renders its lexeme exactly (`hello`, `12`, `true`) comes
back from the constructor as a bare `str`, `int` or `bool` with no lexeme to carry, so `raw`
is `None` and the text is rebuilt from `value`, giving the same bytes.  Everything whose
spelling a builtin cannot reproduce (`+12`, `0X1F`, `1_000.5`, `yes`, `2002-1-4`) is a
lexeme-carrying scalar type, and that is what keeps the round trip exact.

**Trivia.** Comments are read off `.ca` through the identity-keyed store, never re-derived
from indices, and never by touching the object: a dump is a read, so every access here is a
`getattr(obj, _attrib, None)` that cannot create the attribute it looks for.  The slots line
up with the bridge table in `yamluna.comments`:

* `before`: the parent's record for this entry, `C_KEY_PRE` or `C_ELEM_PRE`.  For a mapping
  value and for the document root it is the node's own `ca.comment[1]`, which is where the
  constructor put both.
* `eol`: the parent's record, `C_VALUE_EOL` (`C_ELEM_EOL` in a sequence), or the node's own
  `ca.comment[0]` when the parent has none.
* `inner`: the node's own `ca.comment[1]`, for every other position.
* `after`: the node's own `ca.end`, then the parent's `C_VALUE_POST`.

`ca.comment[1]` is one list holding what the records keep in two slots.  For the two
positions where the constructor merges `before` into it the split cannot be recovered, and
it resolves to `before`, the common case by far (a comment block above a nested mapping).
`C_VALUE_POST` likewise holds a scalar value's `before` and its `after` together, and comes
back as `after`.  Both are properties of `.ca`'s shape rather than of this module.

**Anchors.** `&name` goes at the first occurrence of an object and `*name` at every later
one, keyed on `id()`.  An anchor that is set is always emitted, even when it is referenced
once: it is source text, and dropping it is not a round trip.  `always_dump` is never read
here, because the behaviour it asks for is what every set anchor already gets.  A shared
object with no anchor of its own gets a generated `id001`.  Recursive structures terminate
because the name is recorded before the subtree is walked, which makes a back-reference an
alias.

**Merge keys** are re-emitted as `<<: *base` and never expanded; `Node.merge` marks them.

**Tags** come from `TagRegistry.plan`, called with the registered classes this document
actually uses.  Its `%TAG` directives go on `Doc.tag_directives` and the per-node string on
`Node.tag`.  A class with a `to_yaml` hook, either its own classmethod or one passed to
`register_class`, builds its own node through `_Representer.represent_scalar`,
`represent_mapping` or `represent_sequence`, which keep ruamel's hook signatures.

**Positions.** `Node.line` and `Node.col` are the source positions the emitter echoes an
untouched node at, and they come from `.lc`: a container's own, and for every scalar the
parent's `lc.key`, `lc.value` or `lc.item`, since a bare `str` or `int` has nowhere to keep
one.  An alias site is placed by its parent only, never by the object, which remembers where
its *anchor* was written.  A tree the user built has no `.lc` and gets no positions, so it is
laid out from `EmitOptions` instead; a tree that was edited has stale ones, and the emitter
stops believing recorded lines at the first construct that misses.
"""

from __future__ import annotations

import base64
import datetime
import inspect
import math
from collections.abc import Callable, Iterable, Iterator, Mapping
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
    NODE_ATTRIB,
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

# ruamel's style indicator, mapped to the record's style constant.
_STYLE_BY_INDICATOR: dict[str | None, int] = {
    '|': STYLE_LITERAL,
    '>': STYLE_FOLDED,
    "'": STYLE_SINGLE,
    '"': STYLE_DOUBLE,
    '': STYLE_PLAIN,
    None: STYLE_PLAIN,
}

# The `c-indicator` set: a plain scalar may not start with one of these.  `-`, `?` and `:`
# only matter when a space follows, which `_plain_ok` checks separately.
_INDICATOR_START = frozenset('-?:,[]{}#&*!|>\'"%@`')

# What no scalar may hold literally: the C0 controls, below `0x20`, and `DEL`.
_FIRST_PRINTABLE = 0x20
_DEL = 0x7F

# Anything a scalar can be.  Everything else is a container, an alias target, or an object
# for the tag registry.  `datetime.date` covers `datetime` and `TimeStamp`.
_ATOMS = (str, bytes, bytearray, int, float, complex, datetime.date, datetime.time, type(None))

_SET_TAG = ('!!', 'set', 'tag:yaml.org,2002:set')
_BINARY_TAG = ('!!', 'binary', 'tag:yaml.org,2002:binary')


# -- scalar style choice ------------------------------------------------------------------


def _plain_ok(text: str, version: tuple[int, int] | None = None) -> bool:
    """Report whether `text` can be written plain and still load back as this string.

    Whether it would load back is answered by the loader's own
    `yamluna.constructor.resolve`, so the two directions cannot drift apart: whatever the
    constructor would turn into a bool, an int or a timestamp gets quoted here, and `yes`
    under YAML 1.2, which resolves to a string, does not.

    The answer is for block context.  The emitter has the final say per context, since a
    value that is plain-safe in a block mapping may still need quoting inside a flow
    collection, and it may only ever quote *more* than this, never less.

    Args:
        text: The cooked scalar value.
        version: The document's `%YAML` version.  `(1, 1)` puts `yes`, `on` and the rest of
            the YAML 1.1 boolean spellings back among the lexemes that must be quoted.

    Returns:
        `True` when the text is safe to write with no quotes.

    """
    if not text:
        return False
    if text[0] in ' \t' or text[-1] in ' \t':
        return False
    if any(ord(c) < _FIRST_PRINTABLE or ord(c) == _DEL for c in text):
        return False
    if text[0] in _INDICATOR_START and (text[0] not in '-?:' or len(text) == 1 or text[1] in ' \t'):
        return False
    if ': ' in text or ' #' in text or text[-1] == ':':
        return False
    return resolve(text, version) is UNRESOLVED


def _quoted_style(text: str) -> int:
    """Return the cheapest quoting that survives a reparse: single unless escaping is needed."""
    if any(ord(c) < _FIRST_PRINTABLE or ord(c) == _DEL for c in text):
        return STYLE_DOUBLE
    return STYLE_SINGLE


def _float_text(value: float) -> str:
    """Return the YAML spelling of a float, with `.nan` and `.inf` for the two special cases."""
    if math.isnan(value):
        return '.nan'
    if math.isinf(value):
        return '-.inf' if value < 0 else '.inf'
    return repr(value)


# One branch per scalar type, in the order the checks have to be made: the branch count is
# the count of types, and each one is tested to the byte.
def _scalar(  # noqa: C901, PLR0911
    obj: Any, version: tuple[int, int] | None = None
) -> tuple[int, str, str | None]:
    """Return `(style, cooked value, source lexeme or None)` for one scalar object.

    Raises:
        RepresenterError: `obj` is not a type this module can write as a scalar.

    """
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
            # The text no longer loads back plain, so the value wins over the style.
            style = _quoted_style(text)
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
        # `!!binary`.  The tag comes from `_tag_of`, which reads `_BINARY_TAG` for bytes.
        return STYLE_DOUBLE, base64.b64encode(bytes(obj)).decode('ascii'), None
    msg = f'cannot represent an object: {obj!r}'
    raise RepresenterError(msg)


# -- reading the object model, without writing to it --------------------------------------


def _anchor_name(obj: Any) -> str | None:
    """Return the anchor name set on `obj`, or `None` when it has none."""
    anchor = getattr(obj, anchor_attrib, None)
    return None if anchor is None else anchor.value


def _trackable(obj: Any) -> bool:
    """Whether `obj` can be aliased: containers always, scalars only once anchored."""
    # Keying scalars on `id()` unconditionally would alias interned small ints and strings
    # to each other, which is why ruamel refuses to alias scalars at all.
    return not isinstance(obj, _ATOMS) or _anchor_name(obj) is not None


def _state(obj: Any) -> Mapping[Any, Any]:
    """Return the attribute mapping a registered class is represented from, as ruamel does.

    The record the object was loaded from is left out.  It is parked on the object under
    `NODE_ATTRIB` and lands in `__dict__` like anything else, but it is this package's
    bookkeeping rather than a field of the user's class.
    """
    state = obj.__getstate__() if hasattr(obj, '__getstate__') else getattr(obj, '__dict__', None)
    if not isinstance(state, Mapping):
        return {}
    return (
        state if NODE_ATTRIB not in state else {k: v for k, v in state.items() if k != NODE_ATTRIB}
    )


def _children(obj: Any, *, state_too: bool = False) -> Iterator[Any]:
    """Everything reachable from `obj`, for the shared-object pre-pass.

    Args:
        obj: The object to walk.
        state_too: Walk `obj`'s attributes as well as its items. Set for a class that
            writes itself: a `to_yaml` hook is free to write attribute state, and the
            items are all a container subclass would otherwise yield.

    Yields:
        Every object reachable from `obj` in one step.

    """
    if isinstance(obj, Mapping):
        # Merged-in keys belong to the mapping `<<` points at.  Walking them here would
        # count that mapping's values twice and anchor them for no reason.
        items = obj.non_merged_items() if isinstance(obj, CommentedMap) else obj.items()
        for key, value in items:
            yield key
            yield value
        yield from getattr(obj, merge_attrib, None) or ()
    elif isinstance(obj, (set, frozenset, list, tuple)):
        yield from obj
    elif not isinstance(obj, _ATOMS):
        yield from _state(obj).values()
        state_too = False  # `_state` is this object's whole content, hook or no hook
    if state_too:
        yield from _state(obj).values()


def _lc(obj: Any) -> tuple[int, int] | None:
    """`obj`'s own recorded source position, read without creating a `.lc` on it."""
    lc = getattr(obj, line_col_attrib, None)
    if lc is None or lc.line is None or lc.col is None:
        return None
    return lc.line, lc.col


def _lc_of(container: Any, key: Any, which: str) -> tuple[int, int] | None:
    """Return the position `container` recorded for one of its children.

    A bare `str` or `int` has nowhere to hold its own position, so the parent's `.lc` is
    where most scalars get theirs back.  It is also the only right answer for an *alias*
    site: the object knows where its anchor was written, not where the `*name` is.
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
    """`key in keys`, for a `key` that may not be hashable (and so cannot be in there)."""
    try:
        return key in keys
    except TypeError:
        return False


def _flow_style(obj: Any, *, default: bool) -> int:
    """Return the style constant for `obj`: its own `.fa` setting wins over the caller's default."""
    # Ruamel's rule, and the right one: the constructor sets `.fa` from the source, so a
    # collection loaded in flow style stays flow whatever default the caller passed.
    fa = getattr(obj, format_attrib, None)
    flow = default if fa is None else fa.flow_style(default)
    return STYLE_FLOW if flow else STYLE_BLOCK


def _record_of(container: Any, key: Any) -> list[Any] | None:
    """Return the four-slot trivia record for one entry of `container`, or `None`."""
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
    """How `container` recorded its `None` child at `key` being spelled (`NULL_ATTRIB`)."""
    store = getattr(container, NULL_ATTRIB, None)
    if not store:
        return None
    try:
        lexeme: str | None = store.get(key)
    except TypeError:  # an unhashable key was never recorded
        return None
    return lexeme


def _flow_seps(obj: Any) -> list[str]:
    """Return what the source wrote between a flow collection's lexemes."""
    return list(getattr(obj, FLOW_SEPS_ATTRIB, None) or ())


def _loaded(value: Any, found: tuple[Any, Node, Node | None] | None) -> Node | None:
    """Return the record `value` was loaded from: its own, or the one its parent kept for it."""
    src: Node | None = getattr(value, NODE_ATTRIB, None)
    if src is not None:
        return src
    return found[1] if found is not None and found[0] is value else None


def _carry(node: Node, src: Node | None) -> None:
    """Hand the emitter back what the source said that this layer cannot keep itself.

    `src` is the record the object was loaded from, taken off the object (`NODE_ATTRIB`) or
    off its parent (`SOURCE_ATTRIB`) for the bare builtins that hold no attribute.  Either
    way it describes a node that has not been replaced, so nothing here has to *understand*
    any of it: where the `&anchor` and the tag were written, where each entry's `:` went,
    which of the tag and the anchor came first, the lexeme itself.  It is handed back, and
    the emitter decides what is still usable, believing a recorded position only while its
    output is still on the line that position names.

    Only what the node does not already supply is filled in: a `TaggedScalar` brings its own
    tag, a `ScalarString` its own anchor.
    """
    if src is None:
        return
    # `tag_first` is which of `!!str &a` and `&a !!str` the source wrote.  How the properties
    # were laid out, like where they were laid out, is nothing the object model has a slot
    # for, even when the object brings its own tag.
    node.anchor_at, node.tag_at, node.tag_first = src.anchor_at, src.tag_at, src.tag_first
    if node.line == 0 and node.col == 0:
        # A position is taken only for a node that has none of its own.  A scalar has no
        # `.lc` and a *root* has no parent to place it, so without this the one document
        # whose root is a scalar on the line below `---` loses that line.
        node.line, node.col = src.line, src.col
    if len(src.colon) * 2 == len(node.children):
        # `colon` has a slot per entry, so an insertion or a deletion retires the whole
        # record.  The emitter cannot make that test, which is why it is made here; it is
        # the same rule the recorded flow separators live by.
        node.colon = src.colon
    if node.kind != KIND_SCALAR or src.kind != KIND_SCALAR:
        return
    if node.tag is None:
        node.tag = src.tag
    if node.anchor is None:
        node.anchor = src.anchor
    if src.raw is not None:
        # The record is the authority, not what the value could reconstruct: a `ScalarFloat`
        # built from `!!float "1.5"` remembers `1.5` and not the quotes, and re-encoding
        # `!!binary` loses the line wrapping of the payload.  The three travel together: the
        # emitter reads a block scalar's header off the lexeme, and the cooked value is what
        # the lexeme meant.  A scalar with neither a lexeme nor a position is also the
        # emitter's one signal for "the user built this", so a bare `str` that cannot keep
        # its own lexeme has to get it back from here or the document is laid out afresh.
        node.style, node.value, node.raw = src.style, src.value, src.raw
        # A block scalar's header is a lexeme of its own, ahead of the body `line`/`col`, so
        # it travels with the lexeme rather than being reconstructed from it.
        node.header_at = src.header_at


def _unmerge_value_pre(node: Node, src: Node | None) -> None:
    """Split a value's `after` back into the comments that came *before* it and the rest.

    `.ca` has one slot for both the comments between a `key:` and its value and the ones
    that follow the value, so the constructor merges them there.  The record is what tells
    them apart again: the head of the slot the record says was `before`.  A slot the user
    has edited no longer starts with it and stays whole, which is the safe way to be wrong.
    """
    if src is None or not src.before or node.before:
        return
    head = node.after[: len(src.before)]
    if head == src.before:
        node.before, node.after = head, node.after[len(src.before) :]


def _source_of(container: Any, key: Any) -> tuple[Any, Node, Node | None] | None:
    """Return the `(value, value record, key record)` `container` kept for one entry.

    A bare `str`, `int`, `bool`, `bytes` or `None` takes no attribute, so for those the
    parent is the only place a record can live.
    """
    store = getattr(container, SOURCE_ATTRIB, None)
    if not store:
        return None
    try:
        found: tuple[Any, Node, Node | None] | None = store.get(key)
    except TypeError:  # an unhashable key was never recorded
        return None
    return found


def _tokens(value: Any) -> list[CommentToken]:
    """Return the comment tokens in one `.ca` slot, which may hold one token, a list, or `None`."""
    if value is None:
        return []
    if isinstance(value, CommentToken):
        return [value]
    return [t for t in value if isinstance(t, CommentToken)]


def _trivia_list(value: Any) -> list[Trivia]:
    """Comment tokens as trivia records, collapsing runs of blank tokens into `blank_lines`."""
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
            # The record's text starts at the `#`; `col` is what positions it.
            text = text.lstrip(' \t')
        out.append(Trivia(text, own_line, token.column))
    return out


def _stream_trivia(carried: Doc | None, root: Node) -> tuple[list[Trivia], list[Trivia]]:
    """Take the *stream*'s own trivia back off the root node.

    A comment above `---` and one below `...` belong to the document rather than to its
    root: the emitter writes them outside the directives and the markers.  `.ca` has no slot
    for them, so the constructor folds them into the root's own comments, and the loaded
    document record parked on the root is what says how many of them there were.  A prefix
    or suffix the user has since edited no longer matches and stays on the root, which is
    the safe way to be wrong.  A root that holds no comments at all never took them in the
    first place, since the fold only happens for a `CommentedBase`, and there the record is
    all there is.

    The prefix comes back off `before` for a block root and off `inner` for a flow one,
    because a flow collection's `inner` is left alone (a comment there sits after the
    bracket) and that is where the fold is still parked.  Either way the document's leading
    comments end up outside the root.

    Returns:
        The document's leading and trailing trivia, removed from `root`.

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
        # Nothing folded in and nothing to fold *out* of: a root that holds no comments at
        # all, a scalar, never took the document's, so they stand exactly as recorded.
        leading = leading if not (root.before or root.inner) else []
    if trailing and root.after[-len(trailing) :] == trailing:
        root.after = root.after[: -len(trailing)]
    elif root.after:
        trailing = []
    return leading, trailing


def _leading_is_before(node: Node, src: Node | None = None) -> None:
    """Move `inner` to `before`, for a block collection where the two render identically.

    `.ca` has one slot for a comment written before a collection and one written inside it,
    so the two arrive here indistinguishable.  A block collection starts on the line after
    its parent's `:`, and both comments then sit on their own lines above the first child.
    A flow collection opens with a bracket on the parent's line, and there the distinction
    is the whole layout:

    ```yaml
    flow_map: {        # inner: the comment is after the `{`
      # comment
      x: 1, ... }

    flow_map:          # before: the comment pushes the `{` onto the next line
      # comment
      {x: 1, ... }
    ```

    Promoting `inner` there would rewrite the source, so a flow collection is left alone.
    `.ca` keeps the slot for one, so nothing is lost by that, except when the record says
    otherwise: then the head it says was `before` moves and the rest stays inside.
    """
    if node.style == STYLE_FLOW:
        if src is not None and src.before and node.inner[: len(src.before)] == src.before:
            node.before, node.inner = src.before + node.before, node.inner[len(src.before) :]
        return
    node.before, node.inner = node.inner + node.before, []


def _trivia_one(value: Any) -> Trivia | None:
    """Return the first trivia record in one `.ca` slot, or `None` when the slot is empty."""
    found = _trivia_list(value)
    return found[0] if found else None


def _entries(obj: Mapping[Any, Any]) -> list[tuple[Any, Any, bool]]:
    """Return the `(key, value, is_merge)` triples of `obj`, in emission order.

    Merged-in keys are not entries: they belong to the mapping `<<` points at.  The `<<`
    entry goes back where it was, at `MergeList.merge_pos`, with its value left as an alias,
    so the dump says `<<: *base` instead of expanding it.
    """
    if not isinstance(obj, CommentedMap):
        return [(k, v, False) for k, v in obj.items()]
    entries = [(k, v, False) for k, v in obj.non_merged_items()]
    merged = getattr(obj, merge_attrib, None)
    if not merged:
        return entries
    value: Any = merged[0]
    if len(merged) > 1:  # `<<: [*a, *b]`, a flow sequence of aliases
        value = CommentedSeq(merged)
        value.fa.set_flow_style()
    entries.insert(getattr(merged, 'merge_pos', 0), ('<<', value, True))
    return entries


# -- the walk -----------------------------------------------------------------------------


class _Representer:
    """One document's worth of state: the arena, the anchor bookkeeping, the wire plan."""

    __slots__ = (
        '_aliases',
        '_counter',
        'default_flow_style',
        'hooked',
        'names',
        'nodes',
        'plan',
        'registry',
        'shared',
        'taken',
        'used',
        'version',
    )

    def __init__(self, registry: TagRegistry | None, *, default_flow_style: bool) -> None:
        self.registry = registry
        self.default_flow_style = default_flow_style
        self.nodes: list[Node] = []
        self.shared: dict[int, int] = {}  # id -> occurrences, from the pre-pass
        self.names: dict[int, str] = {}  # id -> anchor, once its definition is under way
        self.taken: set[str] = set()
        self.used: list[type] = []
        self.plan: WirePlan = WirePlan((), {})
        self.hooked: frozenset[type] = frozenset()  # registered classes that write themselves
        self.version: tuple[int, int] | None = None
        self._aliases: list[tuple[int, str]] = []  # alias sites the walk could not place yet
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
        """Build the record for one document, rooted at `data`.

        Args:
            data: The document root.  `None` with no `carried` record gives a document
                whose root is a plain `null`.
            version: The `%YAML` version to write.  Defaults to the loaded document's.
            explicit_start: Write `---` even when the source had none.
            explicit_end: Write `...` even when the source had none.
            carried: The loaded record for a document that has no root object to park one
                on, which is a document that loaded as `None`.  With `data` of `None` this
                record is the document and is re-emitted as it was read.

        Returns:
            The `Doc`: the node arena, the root index, the directives and the trivia that
            sits outside the root.

        Raises:
            RepresenterError: The tree holds a value no scalar rule covers and no registered
                class claims, or a `to_yaml` hook returned something that is not a node
                index.

        """
        # `%YAML`, `%TAG`, `---` and `...` belong to the document, not to the root object;
        # the constructor parks them on the root and this is where they come back.  An
        # explicit argument still wins, and so does `YAML.explicit_start`, which the `YAML`
        # object applies to the finished record.
        if carried is None:
            carried = getattr(data, DOC_ATTRIB, None)
        elif data is None:
            # An empty document has no content to re-represent: the record *is* the
            # document, comments, `---` and all.  Copied, because a dump is a read: the
            # stored record has to survive it unchanged for the next dump.
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
                directives_raw=carried.directives_raw,
                stream_tail=carried.stream_tail,
                line_space=dict(carried.line_space),
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
            # Resolved once per document: the walk below only has to test membership.
            self.hooked = frozenset(c for c in self.plan.tags if self._writes_itself(c))
        root = self._add(Node(value='null')) if data is None else self._emit(data)
        self._realias()
        # No parent holds the root's leading comments, nor its end-of-line one: an entry's come
        # off the parent's `.ca`, and a root has no parent, so the record is all there is.
        _leading_is_before(self.nodes[root], getattr(data, NODE_ATTRIB, None))
        if self.nodes[root].eol is None:
            self.nodes[root].eol = getattr(getattr(data, NODE_ATTRIB, None), 'eol', None)
        leading, trailing = _stream_trivia(carried, self.nodes[root])
        directives = self._directives(carried)
        return Doc(
            version=version,
            tag_directives=directives,
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
            # The directive region is echoed *whole*, so it is only the truth while nothing
            # has been added to the directives it spells out.
            directives_raw=None
            if carried is None or version != carried.version or directives != carried.tag_directives
            else carried.directives_raw,
            stream_tail='' if carried is None else carried.stream_tail,
            line_space={} if carried is None else dict(carried.line_space),
        )

    def _directives(self, carried: Doc | None) -> list[tuple[str, str]]:
        """Return the document's `%TAG` lines: the source's in source order, then the plan's.

        A handle the source declared that the wire plan also wants keeps its place on the
        page and takes the plan's prefix; a handle only the plan wants is appended.
        """
        planned = {d.handle: d.prefix for d in self.plan.directives}
        out = [
            (handle, planned.pop(handle, prefix))
            for handle, prefix in (() if carried is None else carried.tag_directives)
        ]
        return out + list(planned.items())

    # -- pre-pass: which objects are shared, which registered classes are used -------------

    def _scan(self, obj: Any, seen: set[int]) -> None:
        """Count occurrences by identity, and collect the anchor names and classes in use."""
        writes_itself = False
        if self.registry is not None and self.registry.registration_for(type(obj)) is not None:
            self.used.append(type(obj))
            writes_itself = self._writes_itself(type(obj))
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
        for child in _children(obj, state_too=writes_itself):
            self._scan(child, seen)

    # -- main pass ------------------------------------------------------------------------

    def _add(self, node: Node) -> int:
        self.nodes.append(node)
        return len(self.nodes) - 1

    def _generate(self) -> str:
        """Return the next unused generated anchor name: `id001`, `id002`, and so on."""
        while True:
            self._counter += 1
            name = f'id{self._counter:03d}'
            if name not in self.taken:
                self.taken.add(name)
                return name

    def _emit(self, obj: Any) -> int:
        """Append `obj`'s subtree to the arena in pre-order and return its index.

        The node also gets `obj`'s recorded source position, which is what lets the emitter
        reproduce the file's own indentation instead of laying the node out afresh.  A stale
        position cannot open a hole in the output, because the emitter stops believing
        recorded lines at the first construct that does not land on one, so an edited tree
        degrades to the layout path rather than to garbage.
        """
        index = self._emit_node(obj)
        node = self.nodes[index]
        # An alias site is not where its anchor was written; only the parent can place it.
        if node.kind != KIND_ALIAS:
            _carry(node, getattr(obj, NODE_ATTRIB, None))
            if (pos := _lc(obj)) is not None:
                node.line, node.col = pos
        return index

    def _at(self, index: int, pos: tuple[int, int] | None) -> None:
        """Give a node the position its *parent* recorded, when it carries none itself."""
        node = self.nodes[index]
        if pos is not None and node.line == 0 and node.col == 0:
            node.line, node.col = pos

    def _emit_node(self, obj: Any) -> int:
        """Emit one node, as an alias when this object has already been written."""
        if not _trackable(obj) and type(obj) not in self.hooked:
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

    # ruamel's hook name, kept so ported classes work unchanged.
    represent_data = _emit

    def _build(self, obj: Any, anchor: str | None) -> int:
        # A registered class that writes itself does so whatever it subclasses.  The branches
        # below match on `isinstance`, so without this a registered `tuple`, `dict` or `str`
        # subclass would be written in its container form and its hook never called, while the
        # load side still called `from_yaml`.
        if type(obj) in self.hooked:
            return self._custom(obj, anchor)
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
        node = Node(KIND_SCALAR, style, value=value, raw=raw, tag=self._tag_of(obj))
        # A `TaggedScalar` is a `CommentedBase` and can hold comments of its own, and a scalar
        # *root* is where the document's end-of-line comment is folded in.
        self._own_trivia(obj, node)
        return node

    def _mapping(self, obj: Mapping[Any, Any], anchor: str | None) -> int:
        node = Node(
            KIND_MAPPING,
            _flow_style(obj, default=self.default_flow_style),
            anchor=anchor,
            tag=self._tag_of(obj),
            flow_seps=_flow_seps(obj),
        )
        index = self._add(node)
        self._own_trivia(obj, node)
        explicit = getattr(obj, EXPLICIT_ATTRIB, None) or frozenset()
        for key, value, is_merge in _entries(obj):
            record = _record_of(obj, key)
            source = _source_of(obj, key)
            key_index = self._emit(key)
            self._respell_key(key_index, source)
            self._at(key_index, _lc_of(obj, key, 'key'))
            self._entry_trivia(self.nodes[key_index], record, C_KEY_PRE, C_KEY_EOL, None)
            value_index = self._emit(value)
            self._at(value_index, _lc_of(obj, key, 'value'))
            if value is None:
                self._spell_null(value_index, _null_lexeme(obj, key))
            self._respell(value_index, value, source)
            src = _loaded(value, source)
            # A value has no `before` of its own.
            _leading_is_before(self.nodes[value_index], src)
            self._entry_trivia(self.nodes[value_index], record, None, C_VALUE_EOL, C_VALUE_POST)
            _unmerge_value_pre(self.nodes[value_index], src)
            if is_merge:
                node.merge.append(len(node.children))
            elif _in(explicit, key):
                node.explicit.append(len(node.children))
            node.children += [key_index, value_index]
        return index

    def _sequence(self, obj: Any, anchor: str | None) -> int:
        node = Node(
            KIND_SEQUENCE,
            _flow_style(obj, default=self.default_flow_style),
            anchor=anchor,
            tag=self._tag_of(obj),
            flow_seps=_flow_seps(obj),
        )
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
        """Add a `!!set` node: a mapping whose values are all null."""
        node = Node(
            KIND_MAPPING,
            _flow_style(obj, default=self.default_flow_style),
            anchor=anchor,
            tag=self._tag_of(obj) or _SET_TAG,
            flow_seps=_flow_seps(obj),
        )
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
            # `raw=''` is the *absent* value the emitter writes nothing for: a set member
            # is `? a`, not `a: ''`.  It is the same empty lexeme `_scalar(None)` returns.
            absent = self._add(Node(KIND_SCALAR, STYLE_PLAIN, value='', raw=''))
            node.children += [key_index, absent]
        return index

    def _to_yaml(self, cls: type) -> Callable[[Any, Any], int] | None:
        """Return the `to_yaml` hook for a registered class, or `None` when it has none.

        Args:
            cls: The registered class.

        Returns:
            The hook passed to `register_class` for `cls`, which wins, otherwise the one the
            class carries. Tested against `None` rather than for truth: a callable object is
            a valid hook whatever its `__bool__` says.

        """
        registration = None if self.registry is None else self.registry.registration_for(cls)
        hook = registration.to_yaml if registration is not None else None
        return getattr(cls, 'to_yaml', None) if hook is None else hook

    def _writes_itself(self, cls: type) -> bool:
        """Whether `cls` builds its own node, so the container branches must not claim it.

        A hook passed to `register_class` counts, and so does a `to_yaml` classmethod, which
        is the form ruamel documents. An ordinary method that happens to be called `to_yaml`
        does not: it takes `self`, and `dict` and `tuple` subclasses that expose one are
        common enough that hijacking their dump on the strength of the name would be wrong.

        Args:
            cls: The registered class.

        Returns:
            Whether a hook should be preferred over the form `cls`'s base type implies.

        """
        registration = None if self.registry is None else self.registry.registration_for(cls)
        if registration is not None and registration.to_yaml is not None:
            return True
        return inspect.ismethod(getattr(cls, 'to_yaml', None))  # a classmethod, bound to `cls`

    def _custom(self, obj: Any, anchor: str | None) -> int:
        """Represent one instance of a registered class.

        A `to_yaml` hook builds its own node and is given the anchor this walk chose for
        it; any other class is written as a mapping of its attributes. The hook is the one
        passed to `register_class`, or failing that the classmethod on the class.

        Raises:
            RepresenterError: The class is not registered with `YAML.register_class()`, or
                its `to_yaml` returned something other than the node index that
                `represent_scalar`, `represent_mapping` and `represent_sequence` return.

        """
        cls = type(obj)
        written = self.plan.tags.get(cls)
        if written is None:
            msg = (
                f'cannot represent an object: {obj!r}; register {cls.__module__}.'
                f'{cls.__qualname__} with YAML.register_class() first'
            )
            raise RepresenterError(msg)
        hook = self._to_yaml(cls)
        if hook is not None:
            index = hook(self, obj)
            if not isinstance(index, int) or not 0 <= index < len(self.nodes):
                msg = (
                    f'the to_yaml hook for {cls.__qualname__} must return what '
                    f'representer.represent_* returned, not {index!r}'
                )
                raise RepresenterError(msg)
            if self.nodes[index].anchor is None:
                self.nodes[index].anchor = anchor
            return index
        node = Node(
            KIND_MAPPING,
            _flow_style(obj, default=self.default_flow_style),
            anchor=anchor,
            tag=self._triple(written, cls),
        )
        index = self._add(node)
        for key, value in _state(obj).items():
            node.children += [self._emit(key), self._emit(value)]
        return index

    def _spell_null(self, index: int, lexeme: str | None) -> None:
        """Give a null node back the spelling it was loaded with: `~`, `null`, and so on.

        `_scalar(None)` can only produce the empty lexeme, `key:` with nothing after it,
        because `None` carries nothing.  The parent remembers the rest.
        """
        if lexeme:
            node = self.nodes[index]
            node.value = node.raw = lexeme

    def _respell(self, index: int, value: Any, found: tuple[Any, Node, Node | None] | None) -> None:
        """Hand a value the record its parent kept for it under `SOURCE_ATTRIB`.

        Applied only while the entry still holds the value that was loaded: an edited value
        is a new value and is written from scratch like any other.  That test is the whole
        difference between this and the record a value keeps on itself, which cannot outlive
        the object it is parked on.
        """
        if found is None or not isinstance(value, _ATOMS):
            return
        was, src, _ = found
        if type(was) is not type(value) or was != value:
            return
        _carry(self.nodes[index], src)
        self._note_alias(index, src)

    def _respell_key(self, index: int, found: tuple[Any, Node, Node | None] | None) -> None:
        """Do the same for the key the record was looked up by, which cannot have changed."""
        if found is not None and found[2] is not None:
            _carry(self.nodes[index], found[2])
            self._note_alias(index, found[2])

    def _note_alias(self, index: int, src: Node) -> None:
        """Note an entry whose parent recorded it as an `*name` site.

        `None` and `bytes` are the two values that cannot hold an anchor, so an alias to one
        of them constructs to a value with no identity to alias on: `a: &anchor` with
        `b: *anchor` gives both keys the one `None` singleton, and the record the parent
        kept is the only trace left of the `*name`.  Every other alias comes back from
        object identity in `_emit_node`.
        """
        # Writing the alias back unconditionally would be wrong: an alias whose anchor has
        # since left the document is invalid YAML, which is worse than losing the alias.  So
        # the site is only noted here and settled in `_realias`, once the whole arena is
        # there to say whether the anchor was written.
        if src.kind == KIND_ALIAS and src.anchor:
            self._aliases.append((index, src.anchor))

    def _realias(self) -> None:
        """Turn the noted sites into aliases, but only where their anchor is really there.

        Arena order is document order, so an anchor at a lower index is one the reader will
        have seen by the time it reaches the alias.  A name defined nowhere earlier, because
        the anchored entry was deleted or its value replaced, leaves the site as the plain
        null it already is: an alias lost, never a dangling one emitted.
        """
        if not self._aliases:
            return
        first: dict[str, int] = {}
        for index, node in enumerate(self.nodes):
            if node.anchor is not None:
                first.setdefault(node.anchor, index)
        for index, name in self._aliases:
            if first.get(name, index) < index:
                node = self.nodes[index]
                node.kind, node.anchor = KIND_ALIAS, name
                # An alias is the name and nothing else: a `*x` standing in for a `bytes`
                # must shed the `!!binary` its value would otherwise be written with, since
                # a tag on an alias is not YAML at all.
                node.value = node.raw = node.tag = None

    # -- trivia ---------------------------------------------------------------------------

    def _own_trivia(self, obj: Any, node: Node) -> None:
        """Read the node's own `.ca`: `ca.comment[1]` -> inner, `ca.end` -> after."""
        if not isinstance(obj, CommentedBase):
            return
        # Never `.ca` itself: reading it creates one, and a dump must not write to the tree.
        ca = getattr(obj, comment_attrib, None)
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
        """Read the parent's record for this entry: pre -> before, eol -> eol, post -> after."""
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
        """Return the tag to write for `obj`: the wire plan's, or the one it was loaded with."""
        if isinstance(obj, (bytes, bytearray)):
            return _BINARY_TAG
        written = self.plan.tags.get(type(obj))
        if written is not None:
            return self._triple(written, type(obj))
        return self._tag_triple(getattr(obj, tag_attrib, None))

    def _triple(self, written: str, cls: type) -> tuple[str, str, str]:
        """Return a registered class's tag as `(handle, suffix, resolved URI)`."""
        handle, suffix = _split_tag(written)
        registration = None if self.registry is None else self.registry.registration_for(cls)
        return handle or '!', suffix, registration.uri if registration else written

    @staticmethod
    def _tag_triple(tag: Tag | None) -> tuple[str, str, str] | None:
        """Return a loaded `Tag` as the `(handle, suffix, resolved URI)` triple a node holds."""
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
        """Add a scalar node to the arena, for a class representing itself.

        Args:
            tag: The tag to write, as the text it should appear as (`!Circuit`) or as a
                loaded `Tag`.
            value: The scalar value.  It is written through `str()`.
            style: ruamel's style indicator: `|`, `>`, `'`, `"`, or `''` and `None` for
                plain.  An indicator this package does not know is written plain.
            anchor: The anchor name to write, or `None` for no anchor.

        Returns:
            The index of the new node, which is what `to_yaml` must return.

        """
        return self._add(
            Node(
                KIND_SCALAR,
                _STYLE_BY_INDICATOR.get(style, STYLE_PLAIN),
                anchor=anchor,
                tag=self._hook_tag(tag),
                value=str(value),
            )
        )

    def represent_mapping(
        self,
        tag: str | Tag,
        mapping: Mapping[Any, Any],
        # ruamel's hook signature, which a ported class calls positionally.
        flow_style: bool | None = None,  # noqa: FBT001
    ) -> int:
        """Add a mapping node to the arena, for a class representing itself.

        Args:
            tag: The tag to write, as text or as a loaded `Tag`.
            mapping: The entries, written in iteration order.
            flow_style: `True` for `{a: 1}`; `False` or `None` for block style.

        Returns:
            The index of the new node, which is what `to_yaml` must return.

        """
        node = Node(
            KIND_MAPPING, STYLE_FLOW if flow_style else STYLE_BLOCK, tag=self._hook_tag(tag)
        )
        index = self._add(node)
        for key, value in mapping.items():
            node.children += [self._emit(key), self._emit(value)]
        return index

    def represent_sequence(
        self,
        tag: str | Tag,
        sequence: Any,
        # ruamel's hook signature, which a ported class calls positionally.
        flow_style: bool | None = None,  # noqa: FBT001
    ) -> int:
        """Add a sequence node to the arena, for a class representing itself.

        Args:
            tag: The tag to write, as text or as a loaded `Tag`.
            sequence: The items, written in iteration order.
            flow_style: `True` for `[1, 2]`; `False` or `None` for block style.

        Returns:
            The index of the new node, which is what `to_yaml` must return.

        """
        node = Node(
            KIND_SEQUENCE, STYLE_FLOW if flow_style else STYLE_BLOCK, tag=self._hook_tag(tag)
        )
        index = self._add(node)
        node.children += [self._emit(item) for item in sequence]
        return index

    def _hook_tag(self, tag: str | Tag | None) -> tuple[str, str, str] | None:
        """Return the tag a `to_yaml` hook passed, as the triple a node holds."""
        if tag is None:
            return None
        if isinstance(tag, Tag):
            return self._tag_triple(tag)
        handle, suffix = _split_tag(tag)
        return handle or '!', suffix, tag


# -- public API ---------------------------------------------------------------------------


def represent(data: Any, yaml: Any = None, **options: Any) -> Doc:
    """Turn one Python object into one document record.

    Args:
        data: The document root.  `None` gives a document whose root is a plain `null`,
            unless the record of a document that loaded empty is passed as `carried`.
        yaml: An optional `YAML` instance whose settings supply the defaults for
            `registry`, `default_flow_style` and `version`.
        **options: `registry`, `default_flow_style`, `version`, `explicit_start`,
            `explicit_end` and `carried`, each overriding what `yaml` supplies.

    Returns:
        The `Doc` for the document: the node arena, the root index, the directives and the
        trivia that sits outside the root.

    Raises:
        RepresenterError: The tree holds a value no scalar rule covers and no registered
            class claims, or a `to_yaml` hook returned something that is not a node index.

    """
    settings = _settings(yaml, options)
    return _Representer(
        settings.pop('registry', None),
        default_flow_style=settings.pop('default_flow_style', False),
    ).document(data, **settings)


def represent_all(documents: Iterable[Any], yaml: Any = None, **options: Any) -> list[Doc]:
    """Turn a stream of Python objects into the record list `emit` takes.

    Each document gets its own arena and its own `%TAG` directives, so a class used in the
    second document puts no directive on the first.

    Args:
        documents: The document roots, in stream order.
        yaml: An optional `YAML` instance whose settings supply the defaults, as for
            `represent`.
        **options: The keywords `represent` takes, applied to every document.

    Returns:
        One `Doc` per document, in the order the documents came in.

    Raises:
        RepresenterError: Any of the documents holds a value that cannot be represented.

    """
    return [represent(data, yaml, **options) for data in documents]


def _settings(yaml: Any, overrides: dict[str, Any]) -> dict[str, Any]:
    """`yaml`'s settings, overridden by the explicit keywords: `Constructor.for_yaml`'s rule."""
    if yaml is None:
        return overrides
    return {
        'registry': getattr(yaml, 'registry', None),
        'default_flow_style': bool(getattr(yaml, 'default_flow_style', False)),
        'version': getattr(yaml, 'version', None),
        **overrides,
    }
