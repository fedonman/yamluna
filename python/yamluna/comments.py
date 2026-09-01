"""The container object model a round trip hands back to you.

The containers subclass the builtins, so `isinstance(x, dict)`, `json.dumps(x)`,
`copy.deepcopy(x)`, `pickle` and `x == {'a': 1}` all work, and you can hang your own
attributes on a node.

Comments and blank lines are not keyed by position. Every container owns a store in which a
record is bound to the entry it was loaded for:

* `CommentedSeq` and `CommentedKeySeq` keep a list of records parallel to the elements, so a
  record travels with its element through `insert`, `del`, `pop`, `sort`, `reverse` and slice
  assignment. ruamel keys these records by integer index and stores an own-line comment glued
  into the previous element's end-of-line token, so renumbering faithfully moves a comment
  that belongs to a different element: `insert(0, x)` puts the old first element's comment
  above the new one, and `del seq[i]` destroys the neighbour's comment along with it.
* `CommentedMap`, `CommentedSet` and `CommentedKeyMap` key the store by the entry key, which
  for a `dict` is the entry's identity: it never shifts. Deleting an entry drops its record,
  so re-adding the key later does not resurrect a stale comment the way ruamel does, and
  `CommentedMap.rename` and `CommentedMap.move_to_end` carry the record with the entry.

`.ca.items` is a projection over that store, in ruamel's four-slot layout
`[key_eol, key_pre, value_eol, value_post]`, so code written against ruamel keeps working.
For the keyed containers the projection is the store itself; for the sequences it is a
write-through view keyed by the current index.

The two attributes you reach for are `.ca`, the comments attached to a node, and `.lc`, the
position the node was loaded from. `.lc` is a load-time snapshot: it is 0-based, it is not
recomputed when you edit the tree, and a node you built yourself has `lc.line` and `lc.col`
set to `None`.

If you are writing a loader or a dumper, the four trivia slots the FFI carries land in `.ca`
like this:

* `before`: the parent's record for this entry, slot `C_KEY_PRE`; for a document root, the
  node's own `ca.comment[1]`.
* `eol`: the parent's record, slot `C_VALUE_EOL`, or `C_ELEM_EOL` in a sequence.
* `inner`: this node's own `ca.comment[1]`.
* `after`: this node's own `ca.end`.

A run of n blank lines arrives from the core as n `CommentToken` objects whose value is blank
(`CommentToken.is_blank_line`). Whether a comment stood on its own line is implied by the slot
it lands in, and its column is `CommentToken.column`.
"""

from __future__ import annotations

import copy as _copy
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Final, Self, SupportsIndex

__all__ = [
    'C_ELEM_EOL',
    'C_ELEM_POST',
    'C_ELEM_PRE',
    'C_KEY_EOL',
    'C_KEY_PRE',
    'C_VALUE_EOL',
    'C_VALUE_POST',
    'Anchor',
    'Comment',
    'CommentMark',
    'CommentToken',
    'CommentedBase',
    'CommentedKeyMap',
    'CommentedKeySeq',
    'CommentedMap',
    'CommentedSeq',
    'CommentedSet',
    'Format',
    'LineCol',
    'MergeList',
    'NotNone',
    'Tag',
    'TaggedScalar',
    'anchor_attrib',
    'comment_attrib',
    'format_attrib',
    'line_col_attrib',
    'merge_attrib',
    'tag_attrib',
    'trivia_attrib',
]

# Attribute names are ruamel's, so ported code that poked at them keeps working.
comment_attrib: Final = '_yaml_comment'
format_attrib: Final = '_yaml_format'
line_col_attrib: Final = '_yaml_line_col'
anchor_attrib: Final = '_yaml_anchor'
tag_attrib: Final = '_yaml_tag'
merge_attrib: Final = '_yaml_merge'
trivia_attrib: Final = '_yaml_trivia'  # the identity-keyed store
order_attrib: Final = '_yaml_order'  # CommentedSet insertion order

# The slots of a `.ca.items` record, in ruamel's layout.
C_KEY_EOL: Final = 0
"""Slot holding the end-of-line comment that follows the key."""
C_KEY_PRE: Final = 1
"""Slot holding the list of own-line comments above the key."""
C_VALUE_EOL: Final = 2
"""Slot holding the end-of-line comment that follows the value."""
C_VALUE_POST: Final = 3
"""Slot holding the list of own-line comments below the value."""
# A sequence element has no key half, so its own end-of-line comment sits in slot 0.
C_ELEM_EOL: Final = C_KEY_EOL
"""Slot holding a sequence element's end-of-line comment."""
C_ELEM_PRE: Final = C_KEY_PRE
"""Slot holding the list of own-line comments above a sequence element."""
C_ELEM_POST: Final = C_VALUE_POST
"""Slot holding the list of own-line comments below a sequence element."""


class NotNone:
    """The sentinel that tells "no key given" apart from `None`, which is a legal YAML key."""


def _record() -> list[Any]:
    """Return a fresh record: one empty slot per `C_*` constant."""
    return [None, None, None, None]


def _copy_store(store: Any) -> Any:
    """Copy a trivia store down to the record lists, so a shallow copy cannot alias them."""

    def rec(r: Any) -> Any:
        return None if r is None else [list(x) if isinstance(x, list) else x for x in r]

    if isinstance(store, list):
        return [rec(r) for r in store]
    return {k: rec(r) for k, r in store.items()}


def _keep_scalar_type(old: Any, new: Any) -> Any:
    """Return `new`, rebuilt as `old`'s type when a plain `str` lands on a scalar string.

    Assigning a plain `str` over a scalar string keeps the scalar string's subclass, so the
    quoting style survives the assignment.
    """
    # Any str subclass qualifies, which keeps this module from importing
    # yamluna.scalarstring just to name the five styles.
    if type(new) is str and isinstance(old, str) and type(old) is not str:
        try:
            return type(old)(new)
        except (TypeError, ValueError):
            return new
    return new


class CommentMark:  # noqa: PLW1641  # mutable: hashing it would move the hash
    """The place a comment starts.

    Args:
        column: The column of the `#`, 0-based as every position in yamluna is.
        line: The line the comment starts on, 0-based.

    """

    __slots__ = ('column', 'line')

    def __init__(self, column: int = 0, line: int = 0) -> None:
        """Build a mark at `column` on `line`."""
        self.column = column
        self.line = line

    def __eq__(self, other: object) -> bool:
        """Report whether `other` is a `CommentMark` at the same column and line."""
        if not isinstance(other, CommentMark):
            return NotImplemented
        return self.column == other.column and self.line == other.line

    def __repr__(self) -> str:
        """Return `CommentMark(column, line)`."""
        return f'CommentMark({self.column}, {self.line})'


class CommentToken:  # noqa: PLW1641  # mutable: hashing it would move the hash
    """One piece of trivia: a comment, or a blank line when `value` is blank.

    Args:
        value: The text as it goes to the emitter, leading `#` included. Own-line comments
            carry the trailing newline and end-of-line comments do not, which is ruamel's
            convention.
        start_mark: Where the comment starts. Built from `column` when omitted.
        end_mark: Where the comment ends, when the loader recorded it.
        column: The 0-based column of the `#`. Read only when `start_mark` is omitted.

    """

    __slots__ = ('end_mark', 'start_mark', 'value')

    def __init__(
        self,
        value: str,
        start_mark: CommentMark | None = None,
        end_mark: CommentMark | None = None,
        column: int | None = None,
    ) -> None:
        """Build a token, deriving `start_mark` from `column` when it is not given."""
        self.value = value
        self.start_mark = start_mark if start_mark is not None else CommentMark(column or 0)
        self.end_mark = end_mark

    @property
    def column(self) -> int:
        """The 0-based column the comment starts at."""
        return self.start_mark.column

    @property
    def is_blank_line(self) -> bool:
        """True when this token stands for a blank line rather than for a comment."""
        return not self.value.strip()

    def __eq__(self, other: object) -> bool:
        """Report whether `other` holds the same text at the same start.

        `end_mark` takes no part: two tokens that say the same thing in the same place are
        the same token, whether or not a loader recorded where they stopped.
        """
        if not isinstance(other, CommentToken):
            return NotImplemented
        return self.value == other.value and self.start_mark == other.start_mark

    def __repr__(self) -> str:
        """Return `CommentToken(value, col=column)`."""
        return f'CommentToken({self.value!r}, col={self.column})'


class Anchor:
    """The `&name` on a node.

    Args:
        value: The anchor name without the `&`, or `None` for a node with no anchor.
        always_dump: The ruamel flag for emitting an anchor nothing refers to. An anchor
            that is set is always emitted here, so the flag is honoured but has nothing left
            to decide; it is kept so ported code keeps working.

    """

    __slots__ = ('always_dump', 'value')
    attrib: ClassVar[str] = anchor_attrib

    # ruamel's signature: `Anchor(value, always_dump)`, called positionally by ported code.
    def __init__(
        self,
        value: str | None = None,
        always_dump: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Build an anchor named `value`."""
        self.value = value
        self.always_dump = always_dump

    def __repr__(self) -> str:
        """Return `Anchor(value)`, noting `always_dump` when it is set."""
        ad = ', (always dump)' if self.always_dump else ''
        return f'Anchor({self.value!r}{ad})'


class Tag:
    """A tag, both as written and as resolved.

    Args:
        handle: The handle as written, such as `!` or `!!` or a `%TAG` shorthand.
        suffix: The part after the handle, such as `Circuit` in `!Circuit`.
        resolved: The full tag the handle expands to, such as `tag:qilisdk/Circuit`, or
            `None` when no `%TAG` directive was in scope.

    """

    __slots__ = ('handle', 'resolved', 'suffix')
    attrib: ClassVar[str] = tag_attrib

    def __init__(
        self, handle: str | None = None, suffix: str | None = None, resolved: str | None = None
    ) -> None:
        """Build a tag from its handle, its suffix and the tag the handle resolves to."""
        self.handle = handle
        self.suffix = suffix
        self.resolved = resolved

    @property
    def value(self) -> str | None:
        """The tag as text: resolved when that is known, otherwise as written.

        Returns:
            The tag text, or `None` when this `Tag` carries neither a suffix nor a resolved
            form, which is the shape a node with no tag gets.

        """
        if self.resolved is not None:
            return self.resolved
        if self.suffix is None:
            return None
        return f'{self.handle or ""}{self.suffix}'

    trval = value  # ruamel spelling

    def startswith(self, prefix: str) -> bool:
        """Report whether the tag text starts with `prefix`.

        Args:
            prefix: The text to look for at the front of the tag.

        Returns:
            `False` for a node with no tag, so no `None` check is needed at the call site.

        """
        return bool(self.value) and self.value.startswith(prefix)  # type: ignore[union-attr]

    def __bool__(self) -> bool:
        """Report whether there is a tag here at all, so a node with none is falsey."""
        return self.value is not None

    def __str__(self) -> str:
        """Return the tag text, or `''` when this node has no tag."""
        return self.value or ''

    def __hash__(self) -> int:
        """Hash the handle, the suffix and the resolved form together.

        This is narrower than `__eq__`, which compares the tag text alone. Two tags that
        compare equal because they resolve to the same URI, one written `!Circuit` under a
        directive and one written out in full, therefore hash apart and do not collide as
        keys of the same dict. Nothing in the library uses a `Tag` as a key.
        """
        return hash((self.handle, self.suffix, self.resolved))

    def __eq__(self, other: object) -> bool:
        """Compare against another `Tag`, or against the tag text as a plain `str`.

        Comparing with a `str` matches `value`, so `node.tag == 'tag:qilisdk/Circuit'` works
        without unwrapping the `Tag` first.
        """
        if isinstance(other, str):
            return self.value == other
        if isinstance(other, Tag):
            return self.value == other.value
        return NotImplemented

    def __repr__(self) -> str:
        """Return `Tag(value)`."""
        return f'Tag({self.value!r})'


class Format:
    """The flow or block preference recorded on one node.

    A node with no preference set follows whatever default the dump is given.
    """

    __slots__ = ('_flow_style',)
    attrib: ClassVar[str] = format_attrib

    def __init__(self) -> None:
        """Build a format with no preference set, so the dump default decides."""
        self._flow_style: bool | None = None

    def set_flow_style(self) -> None:
        """Record that this node is written in flow style, `{a: 1}` or `[1, 2]`."""
        self._flow_style = True

    def set_block_style(self) -> None:
        """Record that this node is written in block style, one entry per line."""
        self._flow_style = False

    # ruamel's signature: `flow_style(default)`, called positionally by ported code.
    def flow_style(self, default: bool | None = None) -> bool | None:  # noqa: FBT001
        """Return this node's preference, falling back to `default`.

        Args:
            default: What to return when no preference has been set on this node.

        Returns:
            `True` for flow style, `False` for block style, and `default` when the node has
            no preference of its own.

        """
        return default if self._flow_style is None else self._flow_style

    def __repr__(self) -> str:
        """Return `Format(flow_style)`."""
        return f'Format({self._flow_style})'


class LineCol:
    """Where a node sat in the source, 0-based in both line and column.

    `.lc` is a snapshot taken at load time. It is not recomputed as you edit, so after an
    insert or a rename the positions still describe the document as it was read. A node you
    built yourself, and a node loaded before positions were recorded, has `line` and `col`
    set to `None`, and `key()`, `value()` and `item()` return `None` for it.

    Args:
        line: The line the node starts on, or `None` when no position was recorded.
        col: The column the node starts at, or `None` when no position was recorded.

    """

    attrib: ClassVar[str] = line_col_attrib

    def __init__(self, line: int | None = None, col: int | None = None) -> None:
        """Build a position, with no entry positions recorded yet."""
        self.line = line
        self.col = col
        self.data: dict[Any, tuple[int, int, int, int]] | None = None
        """Positions of this node's entries, keyed by mapping key or by index.

        A mapping entry holds `[key_line, key_col, value_line, value_col]` and a sequence
        element holds `[line, col]`. `None` until an entry position is recorded.
        """

    def add_kv_line_col(self, key: Any, data: Any) -> None:
        """Record the position of one entry.

        Args:
            key: The mapping key, or the index for a sequence.
            data: `[key_line, key_col, value_line, value_col]` for a mapping entry and
                `[line, col]` for a sequence element, all 0-based.

        """
        if self.data is None:
            self.data = {}
        self.data[key] = data

    add_idx_line_col = add_kv_line_col  # ruamel spelling for a sequence

    def key(self, k: Any) -> tuple[int, int] | None:
        """Return `(line, col)` of the key `k`, or `None` when no position was recorded."""
        return self._kv(k, 0, 1)

    def value(self, k: Any) -> tuple[int, int] | None:
        """Return `(line, col)` of `k`'s value, or `None` when no position was recorded."""
        return self._kv(k, 2, 3)

    def item(self, idx: Any) -> tuple[int, int] | None:
        """Return `(line, col)` of element `idx`, or `None` when no position was recorded."""
        return self._kv(idx, 0, 1)

    def _kv(self, k: Any, x0: int, x1: int) -> tuple[int, int] | None:
        if self.data is None:
            return None
        # An absent entry is None, never a KeyError. ruamel raises for a key it never
        # recorded a position for, which reads as a mistake in the caller rather than as
        # "this node has no recorded position", and forces a try/except on every call.
        data = self.data.get(k)
        if data is None:
            return None
        return data[x0], data[x1]

    def __repr__(self) -> str:
        """Return `LineCol(line, col)`."""
        return f'LineCol({self.line}, {self.col})'


class MergeList(list):
    """The mappings merged in through `<<`, in the order they were written."""

    merge_pos: int = 0
    """The position the `<<` key held among its siblings, so the dump puts it back there."""


class Comment:
    """What `.ca` gives you: the comments attached to one node.

    `comment` and `end` hold the node's own trivia. `items` holds one record per entry of a
    container, `[key_eol, key_pre, value_eol, value_post]`, addressed by mapping key or by
    current index. That `items` is a live projection over the owner's identity-keyed store,
    so writing a record into it, or editing a record in place, changes the node.
    """

    __slots__ = ('_owner', '_post', '_pre', 'comment')
    attrib: ClassVar[str] = comment_attrib

    def __init__(self) -> None:
        """Build an empty `Comment`, attached to no node until one adopts it."""
        self.comment: Any = None
        """The node's own trivia, `[eol_token, [own-line tokens above the node]]`.

        `None` until something is attached. Either half can be `None` on its own.
        """
        self._pre: list[CommentToken] | None = None
        self._post: list[CommentToken] = []
        self._owner: CommentedBase | None = None

    @property
    def items(self) -> Any:
        """One record per entry of the owning container, keyed by mapping key or by index.

        Returns:
            The owner's records, `{entry: [key_eol, key_pre, value_eol, value_post]}`, and an
            empty `dict` when this `Comment` is not attached to a node.

        """
        if self._owner is None:
            return {}
        # The owner's own projection hook: a Comment is a view on its node, not a store.
        return self._owner._ca_items()  # noqa: SLF001

    @property
    def end(self) -> list[CommentToken]:
        """The trailing trivia of the node, after its last entry. Empty when there is none."""
        return self._post

    @end.setter
    def end(self, value: list[CommentToken]) -> None:
        self._post = value

    @property
    def pre(self) -> list[CommentToken] | None:
        """A spare list of tokens, `None` by default.

        The comment block above a node lives in `comment[1]`, not here.
        """
        return self._pre

    @pre.setter
    def pre(self, value: list[CommentToken] | None) -> None:
        self._pre = value

    def get(self, item: Any, pos: int) -> CommentToken | None:
        """Read one slot of one entry's record.

        Args:
            item: The mapping key, or the index, of the entry.
            pos: The slot, one of the `C_*` constants.

        Returns:
            The token in that slot, or `None` when the entry has no record, the record is
            shorter than `pos`, or the slot is empty.

        """
        x = self.items.get(item)
        if x is None or len(x) <= pos:
            return None
        return x[pos]

    def set(self, item: Any, pos: int, value: Any) -> None:
        """Write one slot of one entry's record, creating the record when it is missing.

        Args:
            item: The mapping key, or the index, of the entry.
            pos: The slot, one of the `C_*` constants.
            value: A `CommentToken` for the end-of-line slots, a list of them for the
                own-line slots, or `None` to clear the slot.

        Raises:
            TypeError: This `Comment` is not attached to a node, so there is no store to
                write to.
            IndexError: The owner is a sequence and `item` is out of range.

        """
        if self._owner is None:
            msg = 'Comment is not attached to a node'
            raise TypeError(msg)
        # The owner's own record hook, as in `items` above.
        self._owner._ca_record(item)[pos] = value  # noqa: SLF001

    def __contains__(self, text: str) -> bool:
        """Report whether `text` occurs in any comment attached here, as ruamel does.

        Args:
            text: The substring to look for, matched against the token text including its
                leading `#`.

        Returns:
            `True` when any of the node's own trivia or any entry record contains it.

        """
        return any(text in tok.value for tok in self._all_tokens())

    def _all_tokens(self) -> Iterator[CommentToken]:
        """Every token attached here, from the node's own trivia and from every record."""
        for group in (self.comment, self._pre, self._post):
            if not group:
                continue
            for c in group:
                if isinstance(c, CommentToken):
                    yield c
                elif c:
                    yield from (t for t in c if isinstance(t, CommentToken))
        for rec in self.items.values():
            for c in rec or ():
                if isinstance(c, CommentToken):
                    yield c
                elif c:
                    yield from (t for t in c if isinstance(t, CommentToken))

    def __repr__(self) -> str:
        """Return the node's own trivia on one line and the entry records on the next."""
        end = f',\n  end={self._post!r}' if self._post else ''
        return f'Comment(comment={self.comment!r},\n  items={dict(self.items)!r}{end})'


class _SeqCaItems(dict):
    """`.ca.items` for a sequence: `index -> record`, with writes going back to the store.

    Only the indices that carry a record appear, which is what makes it read like ruamel's
    dict. Setting, popping or deleting an index writes through to the parallel store; the
    record objects are shared with the store, so editing one in place writes through too.
    A negative index counts from the end. Setting or deleting an index outside the sequence
    raises `IndexError` rather than growing anything; `pop` follows `dict.pop` and returns
    the default, or raises `KeyError` when there is none.
    """

    __slots__ = ('_owner',)

    # Every `_owner._ca_store()` below reaches for the owner's own trivia hook, which is
    # exactly what this view exists to project; hence the SLF001 suppressions.
    def __init__(self, owner: Any) -> None:
        self._owner = owner
        store = owner._ca_store()  # noqa: SLF001
        super().__init__((i, r) for i, r in enumerate(store) if r is not None)

    def _idx(self, idx: int) -> int:
        """Normalise a possibly negative index, raising `IndexError` when out of range."""
        n = len(self._owner._ca_store())  # noqa: SLF001
        i = idx + n if idx < 0 else idx
        if not 0 <= i < n:
            msg = f'sequence comment index out of range: {idx}'
            raise IndexError(msg)
        return i

    def __setitem__(self, idx: int, record: Any) -> None:
        i = self._idx(idx)
        self._owner._ca_store()[i] = record  # noqa: SLF001
        dict.__setitem__(self, i, record)

    def setdefault(self, idx: int, default: Any = None) -> Any:
        i = self._idx(idx)
        record = self._owner._ca_store()[i]  # noqa: SLF001
        if record is None:
            record = _record() if default is None else default
            self[i] = record
        return record

    # This view is keyed by sequence index, so `dict.pop`'s `object` key is narrowed to one.
    def pop(self, idx: int, *default: Any) -> Any:  # ty: ignore[invalid-method-override]
        i = idx + len(self._owner._ca_store()) if idx < 0 else idx  # noqa: SLF001
        store = self._owner._ca_store()  # noqa: SLF001
        if 0 <= i < len(store):
            store[i] = None
        return dict.pop(self, i, *default)

    def __delitem__(self, idx: int) -> None:
        i = self._idx(idx)
        dict.__delitem__(self, i)
        self._owner._ca_store()[i] = None  # noqa: SLF001

    def clear(self) -> None:
        store = self._owner._ca_store()  # noqa: SLF001
        store[:] = [None] * len(store)
        dict.clear(self)


class CommentedBase:
    """The YAML attributes every node carries.

    `.ca` holds the comments, `.lc` the load-time position, `.fa` the flow or block
    preference, `.anchor` the `&name`, `.tag` the tag and `.merge` the mappings pulled in
    through `<<`. Each one is created on first access, so reading an attribute of a node
    that has none gives you an empty object rather than an error.
    """

    # Which slot of a record holds this container's end-of-line comment. A sequence
    # overrides it, because an element has no key half.
    _ca_eol_slot: ClassVar[int] = C_VALUE_EOL

    # -- trivia store ---------------------------------------------------------------------
    # Keyed containers store `{entry_key: record}`; the sequences override with a list
    # parallel to the elements. Created lazily, so unpickling, which fills the container
    # before it restores `__dict__`, cannot trip over a missing store.

    def _ca_store(self) -> Any:
        """Return the trivia store, created on first use."""
        store = getattr(self, trivia_attrib, None)
        if store is None:
            store = {}
            setattr(self, trivia_attrib, store)
        return store

    def _ca_items(self) -> Any:
        """Return what `.ca.items` projects.

        For a keyed container that is the store itself, so a write through the projection
        lands on the record it came from.
        """
        return self._ca_store()

    def _ca_record(self, key: Any, /) -> list[Any]:
        """Return the record for one entry, created empty when the entry has none yet."""
        return self._ca_store().setdefault(key, _record())

    def _ca_order(self) -> Iterable[Any]:
        """Return the keys of `.ca.items`, in document order."""
        return list(self._ca_store())

    # -- attributes -----------------------------------------------------------------------
    @property
    def ca(self) -> Comment:
        """The comments attached to this node, created empty on first access."""
        c = getattr(self, comment_attrib, None)
        if c is None:
            c = Comment()
            setattr(self, comment_attrib, c)
        # Rebind on every access: a Comment that arrived by copy still points at the node it
        # was copied from, and must project the store of the node it now sits on.
        c._owner = self  # noqa: SLF001
        return c

    @property
    def fa(self) -> Format:
        """The flow or block preference for this node: `set_flow_style`, `set_block_style`."""
        f = getattr(self, format_attrib, None)
        if f is None:
            f = Format()
            setattr(self, format_attrib, f)
        return f

    @property
    def lc(self) -> LineCol:
        """Where this node was loaded from, 0-based.

        Always a `LineCol`. For a node with no recorded position, one you built yourself
        included, `lc.line` and `lc.col` are `None` and `lc.key()`, `lc.value()` and
        `lc.item()` return `None`. The positions are a load-time snapshot and are not
        recomputed when you edit the tree.
        """
        lc = getattr(self, line_col_attrib, None)
        if lc is None:
            lc = LineCol()
            setattr(self, line_col_attrib, lc)
        return lc

    @property
    def anchor(self) -> Anchor:
        """The `&name` on this node, with `value` `None` when it has none."""
        a = getattr(self, anchor_attrib, None)
        if a is None:
            a = Anchor()
            setattr(self, anchor_attrib, a)
        return a

    @property
    def tag(self) -> Tag:
        """The tag on this node, falsey when it has none.

        Assigning a `str` records it as the suffix of a new `Tag`, so `node.tag = 'Circuit'`
        gives `!Circuit`. Assigning `None` clears the tag.
        """
        t = getattr(self, tag_attrib, None)
        if t is None:
            t = Tag()
            setattr(self, tag_attrib, t)
        return t

    @tag.setter
    def tag(self, value: Tag | str | None) -> None:
        if value is None or isinstance(value, Tag):
            setattr(self, tag_attrib, value if value is not None else Tag())
        else:
            setattr(self, tag_attrib, Tag(suffix=value))

    yaml_set_ctag = tag.fset  # ruamel spelling

    @property
    def merge(self) -> MergeList:
        """The mappings merged into this node through `<<`. Empty when there are none."""
        m = getattr(self, merge_attrib, None)
        if m is None:
            m = MergeList()
            setattr(self, merge_attrib, m)
        return m

    def yaml_anchor(self) -> Anchor | None:
        """Return this node's `Anchor`, or `None` when none was ever set on it."""
        return getattr(self, anchor_attrib, None)

    # ruamel's signature: `yaml_set_anchor(value, always_dump)`, called positionally.
    def yaml_set_anchor(
        self,
        value: str | None,
        always_dump: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Set the anchor name on this node.

        Args:
            value: The name without the `&`, or `None` to drop the anchor.
            always_dump: Kept for ruamel compatibility. An anchor that is set is emitted
                either way.

        """
        self.anchor.value = value
        self.anchor.always_dump = always_dump

    def copy_attributes(self, t: Any, memo: dict[int, Any] | None = None) -> Any:
        """Copy the YAML attributes, but not the data, onto `t`.

        Args:
            t: The node to copy onto. Attributes this node does not carry are left alone.
            memo: The `copy.deepcopy` memo. Pass it to deep-copy the attributes; leave it
                `None` for a shallow copy, in which case the trivia store is still copied
                down to its record lists so the two nodes cannot alias one another.

        Returns:
            `t`, so the call can be the last line of a `copy` method.

        """
        for a in (
            comment_attrib,
            format_attrib,
            line_col_attrib,
            anchor_attrib,
            tag_attrib,
            merge_attrib,
            trivia_attrib,
        ):
            v = getattr(self, a, None)
            if v is None:
                continue
            if memo is not None:
                setattr(t, a, _copy.deepcopy(v, memo))
            elif a == trivia_attrib:
                setattr(t, a, _copy_store(v))
            else:
                setattr(t, a, _copy.copy(v))
        return t

    # -- comment API ----------------------------------------------------------------------
    # ruamel's signature: `yaml_end_comment_extend(comment, clear)`, called positionally.
    def yaml_end_comment_extend(
        self,
        comment: Iterable[CommentToken] | None,
        clear: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Append tokens to the trailing trivia of this node.

        Args:
            comment: The tokens to append. `None` does nothing, which lets a loader pass a
                slot straight through.
            clear: Replace what is there instead of appending to it.

        """
        if comment is None:
            return
        if clear or self.ca.end is None:
            self.ca.end = []
        self.ca.end.extend(comment)

    # ruamel's signature: `yaml_key_comment_extend(key, comment, clear)`, called positionally.
    def yaml_key_comment_extend(
        self,
        key: Any,
        comment: Any,
        clear: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Add trivia to the key half of one entry's record.

        Args:
            key: The mapping key, or the index, of the entry.
            comment: A two-item `[eol_token, own_line_tokens]`. The end-of-line token
                replaces what is in the slot; the own-line tokens are appended.
            clear: Replace the own-line tokens instead of appending to them.

        Raises:
            IndexError: This node is a sequence and `key` is out of range.

        """
        r = self._ca_record(key)
        if clear or r[C_KEY_PRE] is None:
            r[C_KEY_PRE] = comment[1]
        elif comment[1]:
            r[C_KEY_PRE].extend(comment[1])
        r[C_KEY_EOL] = comment[0]

    # ruamel's signature: `yaml_value_comment_extend(key, comment, clear)`, called positionally.
    def yaml_value_comment_extend(
        self,
        key: Any,
        comment: Any,
        clear: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Add trivia to the value half of one entry's record.

        Args:
            key: The mapping key, or the index, of the entry.
            comment: A two-item `[eol_token, own_line_tokens]`. The end-of-line token
                replaces what is in the slot; the own-line tokens, which sit below the
                value, are appended.
            clear: Replace the own-line tokens instead of appending to them.

        Raises:
            IndexError: This node is a sequence and `key` is out of range.

        """
        r = self._ca_record(key)
        if clear or r[C_VALUE_POST] is None:
            r[C_VALUE_POST] = comment[1]
        elif comment[1]:
            r[C_VALUE_POST].extend(comment[1])
        r[C_VALUE_EOL] = comment[0]

    def _yaml_add_comment(self, comment: Any, key: Any = NotNone, value: Any = NotNone) -> None:
        """Route a `[eol, own_line]` pair to the key half, the value half, or the node."""
        if key is not NotNone:
            self.yaml_key_comment_extend(key, comment)
        elif value is not NotNone:
            self.yaml_value_comment_extend(value, comment)
        else:
            self.ca.comment = comment

    def _yaml_add_eol_comment(self, comment: Any, key: Any) -> None:
        """Put an end-of-line comment in whichever slot this container uses for one."""
        if self._ca_eol_slot == C_VALUE_EOL:
            self._yaml_add_comment(comment, value=key)
        else:
            self._yaml_add_comment(comment, key=key)

    def _yaml_get_pre_comment(self) -> list[CommentToken]:
        """Return the list of own-line tokens above this node, created empty when there is none."""
        if self.ca.comment is None:
            pre: list[CommentToken] = []
            self.ca.comment = [None, pre]
            return pre
        if self.ca.comment[1] is None:
            self.ca.comment[1] = []
        return self.ca.comment[1]

    def _yaml_clear_pre_comment(self) -> list[CommentToken]:
        """Empty the own-line tokens above this node and return the fresh list."""
        pre: list[CommentToken] = []
        if self.ca.comment is None:
            self.ca.comment = [None, pre]
        else:
            self.ca.comment[1] = pre
        return pre

    def _yaml_get_column(self, key: Any) -> int | None:
        """Return the column of the nearest neighbour's end-of-line comment, or `None`.

        Searches backwards from `key` first and then forwards, so a new comment lines up
        with the block it joins.
        """
        items = self.ca.items
        if not items:
            return None
        order = list(self._ca_order())
        try:
            pos = order.index(key)
        except ValueError:
            return None
        slot = self._ca_eol_slot
        before = reversed(order[:pos])
        after = order[pos + 1 :]
        for candidate in (*before, *after):
            rec = items.get(candidate)
            if rec is not None and rec[slot] is not None:
                return rec[slot].column
        return None

    def yaml_set_start_comment(self, comment: str, indent: int = 0) -> None:
        r"""Replace the comment block above this node.

        Args:
            comment: The text, without the `#`. One token per line; a line that already
                starts with `#` is left as it is. A single trailing newline is ignored.
            indent: The column to write the `#` at.

        Example:
            ```python
            m = CommentedMap(a=1)
            m.yaml_set_start_comment('first\nsecond', indent=2)
            ```

        """
        pre = self._yaml_clear_pre_comment()
        comment = comment.removesuffix('\n')
        mark = CommentMark(indent)
        for line in comment.split('\n'):
            stripped = line.strip()
            text = '# ' + line if stripped and not stripped.startswith('#') else line
            pre.append(CommentToken(text + '\n', mark))

    def yaml_set_comment_before_after_key(
        self,
        key: Any,
        before: str | None = None,
        indent: int = 0,
        after: str | None = None,
        after_indent: int | None = None,
    ) -> None:
        r"""Set the own-line comments above the key and below its value.

        Both texts are appended to whatever the entry already carries in those slots.

        Args:
            key: The entry to attach the comments to.
            before: The text to put above the key, without the `#`, one token per line. A
                lone `'\n'` adds a blank line. `None` leaves the slot alone.
            indent: The column to write the `#` of `before` at.
            after: The text to put below the value, without the `#`, one token per line.
                `None` or an empty string leaves the slot alone.
            after_indent: The column for `after`. Two columns past `indent` when not given.

        Raises:
            IndexError: This node is a sequence and `key` is out of range.

        Example:
            ```python
            m.yaml_set_comment_before_after_key('b', before='why b', after='end of b')
            ```

        """
        if after_indent is None:
            after_indent = indent + 2
        if before and len(before) > 1 and before.endswith('\n'):
            before = before[:-1]
        if after and after.endswith('\n'):
            after = after[:-1]
        rec = self._ca_record(key)

        def token(text: str, column: int) -> CommentToken:
            # An empty line stays empty. A blank line is trivia of its own, and writing it
            # as '# ' would put a hash into the document where the source had nothing.
            return CommentToken((('# ' + text) if text else '') + '\n', CommentMark(column))

        if before is not None:
            if rec[C_KEY_PRE] is None:
                rec[C_KEY_PRE] = []
            if before == '\n':
                rec[C_KEY_PRE].append(token('', indent))
            else:
                rec[C_KEY_PRE].extend(token(line, indent) for line in before.split('\n'))
        if after:
            if rec[C_VALUE_POST] is None:
                rec[C_VALUE_POST] = []
            rec[C_VALUE_POST].extend(token(line, after_indent) for line in after.split('\n'))

    def yaml_add_eol_comment(
        self, comment: str, key: Any = NotNone, column: int | None = None
    ) -> None:
        """Set the end-of-line comment of one entry, or of this node.

        Args:
            comment: The text. A leading `#` is added when it is missing.
            key: The entry to comment. Left out, the comment goes on the node itself.
            column: The column to write the `#` at. Defaults to the column of the nearest
                neighbouring end-of-line comment, and to 0 when there is none.

        Raises:
            IndexError: This node is a sequence and `key` is out of range.

        Example:
            ```python
            m.yaml_add_eol_comment('in seconds', key='timeout')
            ```

        """
        if column is None:
            column = self._yaml_get_column(key)
        if not comment.startswith('#'):
            comment = '# ' + comment
        token = CommentToken(comment, CommentMark(column or 0))
        if key is NotNone:
            if self.ca.comment is None:
                self.ca.comment = [token, None]
            else:
                self.ca.comment[0] = token
            return
        self._yaml_add_eol_comment([token, None], key=key)


class _SeqTrivia(CommentedBase):
    """Trivia store for the sequence-shaped containers: a list parallel to the elements.

    Slot i holds element i's record, or `None`. Keeping the list the same length as the
    elements is what makes a record travel with its element through every mutation.
    """

    _ca_eol_slot: ClassVar[int] = C_ELEM_EOL

    if TYPE_CHECKING:
        # The subclasses mix this in with `list` or `tuple`, which is where the sequence
        # protocol comes from. Declared here so a checker can see it on the mixin too.
        def __len__(self) -> int: ...

    def _ca_store(self) -> list[list[Any] | None]:
        """Return the parallel record list, created empty and the right length on first use."""
        store: list[list[Any] | None] | None = getattr(self, trivia_attrib, None)
        if store is None:
            store = [None] * len(self)
            setattr(self, trivia_attrib, store)
        return store

    def _ca_items(self) -> _SeqCaItems:
        """Return a fresh write-through view of the store, keyed by current index."""
        return _SeqCaItems(self)

    def _ca_record(self, idx: int, /) -> list[Any]:
        """Return the record for one element, created empty when it has none.

        Accepts a negative index and raises `IndexError` outside the sequence.
        """
        store = self._ca_store()
        i = idx + len(store) if idx < 0 else idx
        if not 0 <= i < len(store):
            msg = f'sequence comment index out of range: {idx}'
            raise IndexError(msg)
        rec = store[i]
        if rec is None:
            store[i] = rec = _record()
        return rec

    def _ca_order(self) -> Iterable[Any]:
        return range(len(self._ca_store()))


class CommentedSeq(_SeqTrivia, list):
    r"""A YAML sequence, and a `list` in every other respect.

    A comment belongs to the element it was written against, so it follows that element
    through `insert`, `del`, `pop`, `remove`, `sort` and `reverse`. Inserting at the front
    leaves the existing comments on the elements they describe, and deleting an element takes
    only its own comments with it.

    Two mutations drop records rather than move them, because there is no element left to
    carry them: assigning to a slice replaces the records of the range it overwrites, and
    `clear` empties the store.

    Example:
        ```python
        s = yaml.load('- one  # first\n- two\n')
        s.insert(0, 'zero')
        s.ca.items[1]  # still the record for 'one'
        ```

    """

    def __init__(self, *args: Any, **kw: Any) -> None:
        """Build a sequence from whatever `list` accepts, with no comments attached."""
        list.__init__(self, *args, **kw)

    # -- mutation: the store is kept parallel to the elements ------------------------------
    def __setitem__(self, idx: Any, value: Any) -> None:
        """Replace an element, keeping the comments attached at that position.

        Assigning to a slice drops the comments of every position it overwrites.
        """
        if isinstance(idx, slice):
            values = list(value)
            list.__setitem__(self, idx, values)
            self._ca_store()[idx] = [None] * len(values)
            return
        value = _keep_scalar_type(list.__getitem__(self, idx), value)
        # Replacing one element in place keeps the comments: they describe the position in
        # the document, and the new value is what that position now says.
        list.__setitem__(self, idx, value)

    def __delitem__(self, idx: Any) -> None:
        """Delete an element and its comments, leaving every other element's comments."""
        store = self._ca_store()
        list.__delitem__(self, idx)
        del store[idx]

    def insert(self, idx: SupportsIndex, value: Any) -> None:
        """Insert `value` before position `idx`, with no comments of its own.

        Args:
            idx: The position to insert before.
            value: The element to insert.

        """
        store = self._ca_store()
        list.insert(self, idx, value)
        store.insert(idx, None)

    def append(self, value: Any) -> None:
        """Append `value`, with no comments of its own.

        Args:
            value: The element to append.

        """
        self._ca_store().append(None)
        list.append(self, value)

    def extend(self, values: Iterable[Any]) -> None:
        """Append every element of `values`, none of them with comments.

        Args:
            values: The elements to append. Comments on a `CommentedSeq` passed here are
                not carried over.

        """
        values = list(values)
        self._ca_store().extend([None] * len(values))
        list.extend(self, values)

    def __iadd__(self, values: Iterable[Any]) -> Self:
        """Extend in place with `values`, which arrive without comments."""
        self.extend(values)
        return self

    def __add__(self, other: Any) -> list[Any]:
        """Concatenate into a plain `list`, so the result carries no comments."""
        return list.__add__(self, other)

    def pop(self, idx: SupportsIndex = -1) -> Any:
        """Remove one element and its comments, and return the element.

        Args:
            idx: The position to remove. Defaults to the last element.

        Returns:
            The removed element.

        Raises:
            IndexError: The sequence is empty, or `idx` is out of range.

        """
        store = self._ca_store()
        value = list.pop(self, idx)
        del store[idx]
        return value

    def remove(self, value: Any) -> None:
        """Remove the first element equal to `value`, along with its comments.

        Args:
            value: The element to remove.

        Raises:
            ValueError: No element equals `value`.

        """
        del self[list.index(self, value)]

    def clear(self) -> None:
        """Remove every element and every comment attached to one."""
        list.clear(self)
        self._ca_store().clear()

    def reverse(self) -> None:
        """Reverse the elements, each keeping its own comments."""
        list.reverse(self)
        self._ca_store().reverse()

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        """Sort in place, carrying each element's comments to its new position.

        Args:
            key: Called on an element to produce the value it sorts by.
            reverse: Sort descending.

        """

        # Sort the indices, not the elements, so the same permutation can be applied to the
        # record list. list.sort would move the elements and leave the records behind.
        def item(i: int) -> Any:
            return list.__getitem__(self, i)

        keyf = item if key is None else (lambda i: key(item(i)))
        order = sorted(range(list.__len__(self)), key=keyf, reverse=reverse)
        store = self._ca_store()
        items = [list.__getitem__(self, i) for i in order]
        store[:] = [store[i] for i in order]
        list.__init__(self, items)

    # -- copying ---------------------------------------------------------------------------
    def __deepcopy__(self, memo: dict[int, Any]) -> CommentedSeq:
        """Deep-copy the elements and the YAML attributes, comments included."""
        res = self.__class__()
        memo[id(self)] = res
        for x in self:
            res.append(_copy.deepcopy(x, memo))
        return self.copy_attributes(res, memo=memo)

    def copy(self) -> CommentedSeq:
        """Return a shallow copy that carries its own comments, tags and anchors.

        Returns:
            A new `CommentedSeq` holding the same elements. Its trivia store is copied down
            to the record lists, so editing a comment on the copy leaves the original alone.

        """
        return self.copy_attributes(self.__class__(self))

    def __repr__(self) -> str:
        """Return the `list` repr, so a sequence prints as its elements."""
        return list.__repr__(self)


class CommentedKeySeq(_SeqTrivia, tuple):
    """A sequence standing as a mapping key: a `tuple`, so it hashes and can be one.

    It carries the same comment attributes as `CommentedSeq`, keyed by element position.
    Nothing can be added or removed, so those positions never move.
    """

    __slots__ = ()
    __hash__ = tuple.__hash__

    def __repr__(self) -> str:
        """Return `CommentedKeySeq((...))` around the `tuple` repr."""
        return f'CommentedKeySeq({tuple.__repr__(self)})'


class CommentedMap(CommentedBase, dict):
    r"""A YAML mapping, and a `dict` in every other respect.

    Comments are stored against the mapping key, which is the entry's identity and never
    shifts, so they survive reordering: `move_to_end`, `insert` and `rename` carry an entry's
    comments with it. Deleting an entry deletes its comments, so adding the key back later
    gives you a clean entry rather than the comments the old one had.

    Example:
        ```python
        m = yaml.load('a: 1  # first\nb: 2\n')
        m.rename('a', 'alpha')
        m.ca.items['alpha']  # the record that was on 'a'
        ```

    """

    def __init__(self, *args: Any, **kw: Any) -> None:
        """Build a mapping from whatever `dict` accepts, with no comments attached."""
        dict.__init__(self, *args, **kw)

    def _ca_order(self) -> Iterable[Any]:
        return list(self)

    def _merged(self) -> set[Any]:
        """Return the keys that came in through a `<<` merge rather than being written here."""
        m = getattr(self, '_yaml_merged_keys', None)
        if m is None:
            m = set()
            self._yaml_merged_keys = m
        return m

    # -- mutation --------------------------------------------------------------------------
    def __setitem__(self, key: Any, value: Any) -> None:
        """Set an entry, keeping the comments already attached to that key."""
        if key in self:
            value = _keep_scalar_type(dict.__getitem__(self, key), value)
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            # Written here now, so the key is this mapping's own and the dump must emit it
            # instead of leaving it to the merge.
            merged.discard(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        """Delete an entry and the comments attached to it, so the key comes back clean."""
        dict.__delitem__(self, key)
        # Drop the record with the entry: a record left behind would come back the moment
        # the same key is added again, carrying comments about something else.
        self._ca_store().pop(key, None)
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.discard(key)

    def pop(self, key: Any, default: Any = NotNone) -> Any:
        """Remove one entry with its comments and return its value.

        Args:
            key: The entry to remove.
            default: What to return when the key is absent. Left out, a missing key raises.

        Returns:
            The value that was stored, or `default` when the key is absent and one was given.

        Raises:
            KeyError: The key is absent and no default was given.

        """
        try:
            value = dict.__getitem__(self, key)
        except KeyError:
            if default is NotNone:
                raise
            return default
        del self[key]
        return value

    def popitem(self) -> tuple[Any, Any]:
        """Remove the last entry with its comments and return it.

        Returns:
            The `(key, value)` pair that was last in document order.

        Raises:
            KeyError: The mapping is empty.

        """
        key, value = dict.popitem(self)
        self._ca_store().pop(key, None)
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.discard(key)
        return key, value

    def clear(self) -> None:
        """Remove every entry and every comment attached to one."""
        dict.clear(self)
        self._ca_store().clear()
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.clear()

    def update(self, other: Any = (), /, **kw: Any) -> None:
        """Set every entry of `other` and of `kw`, keeping the comments on existing keys.

        Args:
            other: A mapping, or an iterable of `(key, value)` pairs.
            kw: Further entries, set after `other`.

        """
        items = other.items() if hasattr(other, 'keys') else other
        for k, v in items:
            self[k] = v
        for k, v in kw.items():
            self[k] = v

    def __ior__(self, other: Any) -> Self:
        """Update in place from `other`, keeping the comments on the keys already here."""
        self.update(other)
        return self

    def setdefault(self, key: Any, default: Any = None) -> Any:
        """Return `key`'s value, adding the entry with `default` when it is absent.

        Args:
            key: The entry to look up.
            default: The value to store when the key is absent.

        Returns:
            The stored value, existing or newly added.

        """
        if key not in self:
            self[key] = default
        return dict.__getitem__(self, key)

    # `OrderedDict.move_to_end`'s signature, which ported code calls positionally.
    def move_to_end(self, key: Any, last: bool = True) -> None:  # noqa: FBT001, FBT002
        """Move one entry to the end, or to the front, taking its comments with it.

        Args:
            key: The entry to move.
            last: Move to the end. `False` moves to the front.

        Raises:
            KeyError: The key is absent.

        """
        value = dict.pop(self, key)
        if last:
            dict.__setitem__(self, key, value)
            return
        rest = list(dict.items(self))
        dict.clear(self)
        dict.__setitem__(self, key, value)
        for k, v in rest:
            dict.__setitem__(self, k, v)

    def insert(self, pos: int, key: Any, value: Any, comment: str | None = None) -> None:
        """Put an entry at one position, counted as the document will emit it.

        An existing key is moved rather than duplicated, and keeps its comments.

        Args:
            pos: The position the entry ends up at. A position past the last entry puts it
                at the end.
            key: The entry key.
            value: The value to store.
            comment: An end-of-line comment for the entry. A leading `#` is added when it
                is missing. `None` adds no comment.

        """
        self[key] = value
        self.move_to_end(key)
        for k in [k for k in self if k != key][pos:]:
            self.move_to_end(k)
        if comment is not None:
            self.yaml_add_eol_comment(comment, key=key)

    def rename(self, old: Any, new: Any) -> None:
        """Rename a key in place, keeping both its position and its comments.

        Args:
            old: The key to rename. Renaming a key to itself does nothing.
            new: The key to rename it to. Comments already held for `new` are overwritten
                when `old` carries any of its own.

        Raises:
            ValueError: `old` is not in the mapping.

        """
        if new == old:
            return
        pos = list(self).index(old)
        record = self._ca_store().pop(old, None)
        value = self.pop(old)
        self.insert(pos, new, value)
        if record is not None:
            self._ca_store()[new] = record

    # -- merge keys ------------------------------------------------------------------------
    def add_yaml_merge(self, value: Iterable[Mapping[Any, Any]]) -> None:
        """Record the mappings merged in through `<<` and expose their keys for lookup.

        A merged key that this mapping does not define itself becomes readable here, so
        `m['inherited']` works, while `non_merged_items` still reports only what this
        mapping owns. A key this mapping already has wins, and is left untouched.

        Args:
            value: The merged mappings, in the order they were written. A `MergeList` is
                kept as it is, so a `merge_pos` set on it survives.

        """
        merge = value if isinstance(value, MergeList) else MergeList(value)
        setattr(self, merge_attrib, merge)
        merged = self._merged()
        for m in merge:
            for k, v in m.items():
                if not dict.__contains__(self, k):
                    dict.__setitem__(self, k, v)
                    merged.add(k)

    def non_merged_items(self) -> Iterator[tuple[Any, Any]]:
        """Yield the entries this mapping owns, which is what a dump writes out.

        Yields:
            Each `(key, value)` written here, in document order, skipping the keys that
            arrived through a `<<` merge.

        """
        merged = getattr(self, '_yaml_merged_keys', None) or ()
        for k in dict.__iter__(self):
            if k not in merged:
                yield k, dict.__getitem__(self, k)

    # ruamel's signature: `mlget(key, default, list_ok)`, called positionally.
    def mlget(
        self,
        key: Any,
        default: Any = None,
        list_ok: bool = False,  # noqa: FBT001, FBT002
    ) -> Any:
        """Walk a path of keys, returning `default` at the first step that is missing.

        Args:
            key: A list of keys to follow one level at a time. Anything else is looked up
                as a single key, as `get` would.
            default: What to return when a step is missing.
            list_ok: Allow a step through something that is not a mapping, such as indexing
                a list with an integer key.

        Returns:
            The value at the end of the path, or `default`.

        Raises:
            TypeError: A step lands on something that is not a mapping and `list_ok` is
                `False`.

        Example:
            ```python
            m.mlget(['server', 'port'], default=8080)
            ```

        """
        if not isinstance(key, list):
            return self.get(key, default)
        value: Any = self
        for k in key:
            if not list_ok and not isinstance(value, dict):
                msg = f'{value!r} is not a mapping'
                raise TypeError(msg)
            try:
                value = value[k]
            except (KeyError, IndexError, TypeError):
                return default
        return value

    # -- copying ---------------------------------------------------------------------------
    def copy(self) -> CommentedMap:
        """Return a shallow copy that carries its own comments, tags and anchors.

        Returns:
            A new `CommentedMap` holding the same entries. Its trivia store is copied down
            to the record lists, so editing a comment on the copy leaves the original alone.

        """
        return self.copy_attributes(self.__class__(self))

    def __deepcopy__(self, memo: dict[int, Any]) -> CommentedMap:
        """Deep-copy the entries and the YAML attributes, comments included."""
        res = self.__class__()
        memo[id(self)] = res
        for k in self:
            res[k] = _copy.deepcopy(dict.__getitem__(self, k), memo)
        return self.copy_attributes(res, memo=memo)

    def __repr__(self) -> str:
        """Return the `dict` repr, so a mapping prints as its entries."""
        return dict.__repr__(self)


class CommentedSet(CommentedBase, set):
    """A YAML `!!set`, and a `set` in every other respect.

    Iteration follows document order rather than the hash order a `set` would give you, and
    comments are keyed by the member they were written against, so removing a member removes
    its comments and leaves everyone else's alone.

    Args:
        values: The members, in the order they should be written.

    """

    def __init__(self, values: Iterable[Any] = ()) -> None:
        """Build a set whose document order is the order `values` arrives in."""
        values = list(values)
        set.__init__(self, values)
        self._yaml_order = values

    def _sync(self) -> list[Any]:
        """Rebuild the document order from the real members and drop orphaned records."""
        # Recomputed on every read rather than maintained on every mutation: `update`, `|=`
        # and the other set operations are not overridden, so members can appear or vanish
        # without this class hearing about it, and a maintained order would drift. Known
        # members keep their recorded position and anything new goes to the end.
        order: list[Any] = []
        seen: set[Any] = set()
        for x in getattr(self, order_attrib, ()):
            if x not in seen and set.__contains__(self, x):
                seen.add(x)
                order.append(x)
        for x in set.__iter__(self):
            if x not in seen:
                seen.add(x)
                order.append(x)
        self._yaml_order = order
        store = getattr(self, trivia_attrib, None)
        if store:
            for gone in [k for k in store if k not in seen]:
                del store[gone]
        return order

    def _ca_order(self) -> Iterable[Any]:
        return self._sync()

    def add(self, value: Any) -> None:
        """Add a member at the end of the document order.

        A member that is already there keeps its position and its comments.

        Args:
            value: The member to add.

        """
        if value not in self:
            set.add(self, value)
            self._sync().append(value)
        else:
            set.add(self, value)

    def discard(self, value: Any) -> None:
        """Remove a member and its comments, doing nothing when it is not there.

        Args:
            value: The member to remove.

        """
        set.discard(self, value)
        self._sync()

    def remove(self, value: Any) -> None:
        """Remove a member and its comments.

        Args:
            value: The member to remove.

        Raises:
            KeyError: The member is not in the set.

        """
        set.remove(self, value)
        self._sync()

    def __iter__(self) -> Iterator[Any]:
        """Iterate in document order, not in the hash order a `set` would give."""
        return iter(self._sync())

    def __repr__(self) -> str:
        """Return `CommentedSet([...])` with the members in document order."""
        return f'CommentedSet({self._sync()!r})'


class CommentedKeyMap(CommentedBase, tuple, Mapping):
    """A mapping standing as a mapping key: a `tuple` of pairs, so it hashes.

    It reads like a `Mapping` and compares equal to a `dict` with the same entries, but
    nothing can be set or deleted, so its comments stay attached to the keys they were
    loaded against. Lookup walks the pairs, so it costs one pass over the entries.
    """

    __slots__ = ()
    __hash__ = tuple.__hash__

    def __new__(cls, *args: Any, **kw: Any) -> Self:
        """Build the key map from whatever `dict` accepts, and freeze it as a `tuple`."""
        return tuple.__new__(cls, dict(*args, **kw).items())

    def __init__(self, *args: Any, **kw: Any) -> None:
        """Do nothing: `__new__` has already stored the pairs."""

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        """Return the value for `key`, raising `KeyError` when there is none."""
        for k, v in tuple.__iter__(self):
            if k == key:
                return v
        raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        """Iterate over the keys, as a `Mapping` does, not over the pairs."""
        return (k for k, _ in tuple.__iter__(self))

    def __len__(self) -> int:
        """Return the number of entries."""
        return tuple.__len__(self)

    def __contains__(self, key: Any) -> bool:
        """Report whether `key` is one of the keys, walking the pairs to find out."""
        return any(k == key for k, _ in tuple.__iter__(self))

    def __eq__(self, other: object) -> bool:
        """Compare as a mapping against any `Mapping`, and as a `tuple` against the rest.

        So a key map equals a plain `dict` with the same entries, whatever order the two
        were built in, while `(('a', 1),) == CommentedKeyMap(a=1)` still holds.
        """
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other)
        return tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        """Negate `__eq__`, keeping `NotImplemented` as it is."""
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def _ca_order(self) -> Iterable[Any]:
        return list(self)

    @classmethod
    def fromkeys(cls, keys: Iterable[Any], value: Any = None) -> CommentedKeyMap:
        """Build a key map from `keys`, every one of them mapped to `value`.

        Args:
            keys: The keys, in order. A repeated key is kept once, at its first position.
            value: The value every key gets.

        Returns:
            A new `CommentedKeyMap` with no comments attached.

        """
        return cls(dict.fromkeys(keys, value))

    def __repr__(self) -> str:
        """Return `CommentedKeyMap({...})` around the entries as a `dict`."""
        return f'CommentedKeyMap({dict(self.items())!r})'


class TaggedScalar(CommentedBase, str):
    """A scalar carrying a tag no registered class claims, so it round-trips as written.

    It is a `str`, so it compares and formats like the text it holds, while the tag and the
    quoting style ride along and come back out on dump exactly as they went in.

    Args:
        value: The scalar text.
        style: The style indicator the source wrote it with, one of `'`, `"`, `|` or `>`,
            or `None` for a plain scalar.
        tag: The tag, as a `Tag` or as the suffix of one.

    """

    __slots__ = ()

    # `style` and `tag` are consumed by `__init__`; `__new__` sees the same call and must
    # accept them, so that `TaggedScalar('x', tag='!t')` reaches the right one.
    def __new__(
        cls,
        value: str = '',
        style: str | None = None,  # noqa: ARG004
        tag: Tag | str | None = None,  # noqa: ARG004
    ) -> Self:
        """Build the immutable `str` half; the tag and the style are `__init__`'s work."""
        return str.__new__(cls, value)

    def __init__(
        self,
        value: str = '',  # noqa: ARG002
        style: str | None = None,
        tag: Tag | str | None = None,
    ) -> None:
        """Record the style and the tag on a scalar `__new__` has already built."""
        self.style = style
        if tag is not None:
            self.tag = tag

    @property
    def value(self) -> str:
        """The scalar text.

        Read only. Assigning to it raises `TypeError`, because a `str` cannot change in
        place; build a new `TaggedScalar` instead.
        """
        return str.__str__(self)

    @value.setter
    def value(self, _: Any) -> None:
        msg = 'TaggedScalar is immutable; build a new one'
        raise TypeError(msg)

    def __repr__(self) -> str:
        """Return `TaggedScalar(value=..., style=..., tag=...)`."""
        return f'TaggedScalar(value={self.value!r}, style={self.style!r}, tag={self.tag!r})'
