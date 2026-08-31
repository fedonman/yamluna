"""Round-trip container object model (DESIGN.md 4.1, 2.1).

The containers subclass the builtins -- ``isinstance(x, dict)``, ``json.dumps(x)``,
``copy.deepcopy(x)``, ``pickle`` and ``x == {'a': 1}`` all work, and user code can hang
arbitrary attributes off a node.

Trivia (comments / blank lines) is **not** keyed by sequence index.  Each container owns a
store in which a record is bound to the *entry* it was loaded for:

* :class:`CommentedSeq` / :class:`CommentedKeySeq` keep a list of records parallel to the
  elements, so a record travels with its element through ``insert``, ``del``, ``pop``,
  ``sort``, ``reverse`` and slice assignment.  This is the ruamel bug DESIGN.md 2.1 calls out.
* :class:`CommentedMap` / :class:`CommentedSet` / :class:`CommentedKeyMap` key the store by
  the mapping key, which for a ``dict`` *is* the entry's identity: it never shifts.  Deleting
  an entry drops its record (ruamel keeps it, so re-adding the key resurrects a stale
  comment), and :meth:`CommentedMap.rename` moves the record with the entry.

``.ca.items`` is a **projection** over that store, in ruamel's 4-list layout
``[key_eol, key_pre, value_eol, value_post]``, so code written against ruamel keeps working.
For the keyed containers the projection is the store itself; for the sequences it is a
write-through view keyed by the current index.

Bridge to the ``Trivia4`` slots the FFI carries (DESIGN.md 2.1 / 3), for whoever writes the
loader and the dumper:

===============  ==========================================================================
``before``       the *parent's* record for this entry, slot ``C_KEY_PRE``; ``ca.comment[1]``
                 for a document root
``eol``          the parent's record, slot ``C_VALUE_EOL`` (``C_ELEM_EOL`` in a sequence)
``inner``        this node's own ``ca.comment[1]``
``after``        this node's own ``ca.end``
===============  ==========================================================================

``Trivia::BlankLines(n)`` arrives as *n* :class:`CommentToken`\\ s whose value is blank
(:attr:`CommentToken.is_blank_line`); ``own_line`` is implied by the slot, ``col`` is
:attr:`CommentToken.column`.
"""

from __future__ import annotations

import copy as _copy
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, ClassVar, Final, Self

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

#: slots of a ``.ca.items`` record, ruamel's layout
C_KEY_EOL: Final = 0
C_KEY_PRE: Final = 1
C_VALUE_EOL: Final = 2
C_VALUE_POST: Final = 3
#: a sequence element has no key half: its own eol comment sits in slot 0
C_ELEM_EOL: Final = C_KEY_EOL
C_ELEM_PRE: Final = C_KEY_PRE
C_ELEM_POST: Final = C_VALUE_POST


class NotNone:
    """sentinel distinguishing "no key given" from ``None`` (a legal YAML key)"""


def _record() -> list[Any]:
    return [None, None, None, None]


def _copy_store(store: Any) -> Any:
    """Copy a trivia store down to the record lists, so a shallow copy cannot alias them."""

    def rec(r: Any) -> Any:
        return None if r is None else [list(x) if isinstance(x, list) else x for x in r]

    if isinstance(store, list):
        return [rec(r) for r in store]
    return {k: rec(r) for k, r in store.items()}


def _keep_scalar_type(old: Any, new: Any) -> Any:
    """Assigning a plain ``str`` over a scalar string keeps the scalar string's subclass.

    Avoids importing ``yamluna.scalarstring``: any ``str`` subclass qualifies.
    """
    if type(new) is str and isinstance(old, str) and type(old) is not str:
        try:
            return type(old)(new)
        except (TypeError, ValueError):
            return new
    return new


class CommentMark:
    """Where a comment starts. ``column`` is 0-based, as everywhere in yamluna."""

    __slots__ = ('column', 'line')

    def __init__(self, column: int = 0, line: int = 0) -> None:
        self.column = column
        self.line = line

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CommentMark):
            return NotImplemented
        return self.column == other.column and self.line == other.line

    def __repr__(self) -> str:
        return f'CommentMark({self.column}, {self.line})'


class CommentToken:
    """One piece of trivia: a comment, or (``value`` blank) a blank line.

    ``value`` is the text as it goes to the emitter, leading ``#`` included.  Own-line
    comments carry the trailing newline, end-of-line comments do not -- ruamel's convention.
    """

    __slots__ = ('end_mark', 'start_mark', 'value')

    def __init__(
        self,
        value: str,
        start_mark: CommentMark | None = None,
        end_mark: CommentMark | None = None,
        column: int | None = None,
    ) -> None:
        self.value = value
        self.start_mark = start_mark if start_mark is not None else CommentMark(column or 0)
        self.end_mark = end_mark

    @property
    def column(self) -> int:
        return self.start_mark.column

    @property
    def is_blank_line(self) -> bool:
        return not self.value.strip()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CommentToken):
            return NotImplemented
        return self.value == other.value and self.start_mark == other.start_mark

    def __repr__(self) -> str:
        return f'CommentToken({self.value!r}, col={self.column})'


class Anchor:
    """``&name`` on a node."""

    __slots__ = ('always_dump', 'value')
    attrib: ClassVar[str] = anchor_attrib

    def __init__(self, value: str | None = None, always_dump: bool = False) -> None:
        self.value = value
        self.always_dump = always_dump

    def __repr__(self) -> str:
        ad = ', (always dump)' if self.always_dump else ''
        return f'Anchor({self.value!r}{ad})'


class Tag:
    """A tag as written (``handle`` + ``suffix``) and as resolved (DESIGN.md 2)."""

    __slots__ = ('handle', 'resolved', 'suffix')
    attrib: ClassVar[str] = tag_attrib

    def __init__(
        self, handle: str | None = None, suffix: str | None = None, resolved: str | None = None
    ) -> None:
        self.handle = handle
        self.suffix = suffix
        self.resolved = resolved

    @property
    def value(self) -> str | None:
        """The tag as text: resolved if known, else as written."""
        if self.resolved is not None:
            return self.resolved
        if self.suffix is None:
            return None
        return f'{self.handle or ""}{self.suffix}'

    trval = value  # ruamel spelling

    def startswith(self, prefix: str) -> bool:
        return bool(self.value) and self.value.startswith(prefix)  # type: ignore[union-attr]

    def __bool__(self) -> bool:
        return self.value is not None

    def __str__(self) -> str:
        return self.value or ''

    def __hash__(self) -> int:
        return hash((self.handle, self.suffix, self.resolved))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other
        if isinstance(other, Tag):
            return self.value == other.value
        return NotImplemented

    def __repr__(self) -> str:
        return f'Tag({self.value!r})'


class Format:
    """Per-node flow/block preference."""

    __slots__ = ('_flow_style',)
    attrib: ClassVar[str] = format_attrib

    def __init__(self) -> None:
        self._flow_style: bool | None = None

    def set_flow_style(self) -> None:
        self._flow_style = True

    def set_block_style(self) -> None:
        self._flow_style = False

    def flow_style(self, default: bool | None = None) -> bool | None:
        """``None`` means "no preference set on this node", so the caller's default wins."""
        return default if self._flow_style is None else self._flow_style

    def __repr__(self) -> str:
        return f'Format({self._flow_style})'


class LineCol:
    """Load-time positions, 0-based (DESIGN.md 1.5)."""

    attrib: ClassVar[str] = line_col_attrib

    def __init__(self, line: int | None = None, col: int | None = None) -> None:
        self.line = line
        self.col = col
        self.data: dict[Any, tuple[int, int, int, int]] | None = None

    def add_kv_line_col(self, key: Any, data: Any) -> None:
        if self.data is None:
            self.data = {}
        self.data[key] = data

    add_idx_line_col = add_kv_line_col

    def key(self, k: Any) -> tuple[int, int] | None:
        return self._kv(k, 0, 1)

    def value(self, k: Any) -> tuple[int, int] | None:
        return self._kv(k, 2, 3)

    def item(self, idx: Any) -> tuple[int, int] | None:
        return self._kv(idx, 0, 1)

    def _kv(self, k: Any, x0: int, x1: int) -> tuple[int, int] | None:
        if self.data is None:
            return None
        # DIVERGENCES D7: ruamel raises KeyError for a key it never recorded a position for,
        # which makes `.lc.key(k)` unusable without a try/except on every call. Absent is None.
        data = self.data.get(k)
        if data is None:
            return None
        return data[x0], data[x1]

    def __repr__(self) -> str:
        return f'LineCol({self.line}, {self.col})'


class MergeList(list):
    """The mappings merged in through ``<<``, plus the position the ``<<`` key had."""

    merge_pos: int = 0


class Comment:
    """``.ca`` -- the comments attached to one node.

    ``items`` is a projection over the owner's identity-keyed trivia store, never a store of
    its own; see the module docstring.
    """

    __slots__ = ('_owner', '_post', '_pre', 'comment')
    attrib: ClassVar[str] = comment_attrib

    def __init__(self) -> None:
        self.comment: Any = None  # [eol, [pre]] for the node itself
        self._pre: list[CommentToken] | None = None
        self._post: list[CommentToken] = []
        self._owner: CommentedBase | None = None

    @property
    def items(self) -> Any:
        if self._owner is None:
            return {}
        return self._owner._ca_items()

    @property
    def end(self) -> list[CommentToken]:
        return self._post

    @end.setter
    def end(self, value: list[CommentToken]) -> None:
        self._post = value

    @property
    def pre(self) -> list[CommentToken] | None:
        return self._pre

    @pre.setter
    def pre(self, value: list[CommentToken] | None) -> None:
        self._pre = value

    def get(self, item: Any, pos: int) -> CommentToken | None:
        x = self.items.get(item)
        if x is None or len(x) <= pos:
            return None
        return x[pos]

    def set(self, item: Any, pos: int, value: Any) -> None:
        if self._owner is None:
            raise TypeError('Comment is not attached to a node')
        self._owner._ca_record(item)[pos] = value

    def __contains__(self, text: str) -> bool:
        """True if *text* occurs in any comment attached here (ruamel's semantics)."""
        return any(text in tok.value for tok in self._all_tokens())

    def _all_tokens(self) -> Iterator[CommentToken]:
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
        end = f',\n  end={self._post!r}' if self._post else ''
        return f'Comment(comment={self.comment!r},\n  items={dict(self.items)!r}{end})'


class _SeqCaItems(dict):
    """``.ca.items`` for a sequence: ``index -> record``, writes go back to the store."""

    __slots__ = ('_owner',)

    def __init__(self, owner: Any) -> None:
        self._owner = owner
        super().__init__((i, r) for i, r in enumerate(owner._ca_store()) if r is not None)

    def _idx(self, idx: int) -> int:
        n = len(self._owner._ca_store())
        i = idx + n if idx < 0 else idx
        if not 0 <= i < n:
            raise IndexError(f'sequence comment index out of range: {idx}')
        return i

    def __setitem__(self, idx: int, record: Any) -> None:
        i = self._idx(idx)
        self._owner._ca_store()[i] = record
        dict.__setitem__(self, i, record)

    def setdefault(self, idx: int, default: Any = None) -> Any:
        i = self._idx(idx)
        record = self._owner._ca_store()[i]
        if record is None:
            record = _record() if default is None else default
            self[i] = record
        return record

    def pop(self, idx: int, *default: Any) -> Any:
        i = idx + len(self._owner._ca_store()) if idx < 0 else idx
        store = self._owner._ca_store()
        if 0 <= i < len(store):
            store[i] = None
        return dict.pop(self, i, *default)

    def __delitem__(self, idx: int) -> None:
        i = self._idx(idx)
        dict.__delitem__(self, i)
        self._owner._ca_store()[i] = None

    def clear(self) -> None:
        store = self._owner._ca_store()
        store[:] = [None] * len(store)
        dict.clear(self)


class CommentedBase:
    """The per-node YAML attributes.

    ``.ca``, ``.lc``, ``.fa``, ``.anchor``, ``.tag`` and ``.merge``.
    """

    #: which slot of a record holds this container's end-of-line comment
    _ca_eol_slot: ClassVar[int] = C_VALUE_EOL

    # -- trivia store ---------------------------------------------------------------------
    # Keyed containers store ``{entry_key: record}``; the sequences override with a parallel
    # list.  Created lazily, so unpickling (which fills the container before restoring
    # ``__dict__``) cannot trip over a missing store.

    def _ca_store(self) -> Any:
        store = getattr(self, trivia_attrib, None)
        if store is None:
            store = {}
            setattr(self, trivia_attrib, store)
        return store

    def _ca_items(self) -> Any:
        """``.ca.items``: for keyed containers the store itself, so writes stick."""
        return self._ca_store()

    def _ca_record(self, key: Any) -> list[Any]:
        return self._ca_store().setdefault(key, _record())

    def _ca_order(self) -> Iterable[Any]:
        """The entry keys of ``.ca.items``, in document order."""
        return list(self._ca_store())

    # -- attributes -----------------------------------------------------------------------
    @property
    def ca(self) -> Comment:
        c = getattr(self, comment_attrib, None)
        if c is None:
            c = Comment()
            setattr(self, comment_attrib, c)
        c._owner = self  # rebind: a copied Comment must project its new owner's store
        return c

    @property
    def fa(self) -> Format:
        """format attribute -- ``set_flow_style()`` / ``set_block_style()``"""
        f = getattr(self, format_attrib, None)
        if f is None:
            f = Format()
            setattr(self, format_attrib, f)
        return f

    @property
    def lc(self) -> LineCol:
        lc = getattr(self, line_col_attrib, None)
        if lc is None:
            lc = LineCol()
            setattr(self, line_col_attrib, lc)
        return lc

    @property
    def anchor(self) -> Anchor:
        a = getattr(self, anchor_attrib, None)
        if a is None:
            a = Anchor()
            setattr(self, anchor_attrib, a)
        return a

    @property
    def tag(self) -> Tag:
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
        m = getattr(self, merge_attrib, None)
        if m is None:
            m = MergeList()
            setattr(self, merge_attrib, m)
        return m

    def yaml_anchor(self) -> Anchor | None:
        return getattr(self, anchor_attrib, None)

    def yaml_set_anchor(self, value: str | None, always_dump: bool = False) -> None:
        self.anchor.value = value
        self.anchor.always_dump = always_dump

    def copy_attributes(self, t: Any, memo: dict[int, Any] | None = None) -> Any:
        """Copy the YAML attributes (not the data) onto *t*; returns *t*."""
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
    def yaml_end_comment_extend(
        self, comment: Iterable[CommentToken] | None, clear: bool = False
    ) -> None:
        if comment is None:
            return
        if clear or self.ca.end is None:
            self.ca.end = []
        self.ca.end.extend(comment)

    def yaml_key_comment_extend(self, key: Any, comment: Any, clear: bool = False) -> None:
        r = self._ca_record(key)
        if clear or r[C_KEY_PRE] is None:
            r[C_KEY_PRE] = comment[1]
        elif comment[1]:
            r[C_KEY_PRE].extend(comment[1])
        r[C_KEY_EOL] = comment[0]

    def yaml_value_comment_extend(self, key: Any, comment: Any, clear: bool = False) -> None:
        r = self._ca_record(key)
        if clear or r[C_VALUE_POST] is None:
            r[C_VALUE_POST] = comment[1]
        elif comment[1]:
            r[C_VALUE_POST].extend(comment[1])
        r[C_VALUE_EOL] = comment[0]

    def _yaml_add_comment(self, comment: Any, key: Any = NotNone, value: Any = NotNone) -> None:
        if key is not NotNone:
            self.yaml_key_comment_extend(key, comment)
        elif value is not NotNone:
            self.yaml_value_comment_extend(value, comment)
        else:
            self.ca.comment = comment

    def _yaml_add_eol_comment(self, comment: Any, key: Any) -> None:
        if self._ca_eol_slot == C_VALUE_EOL:
            self._yaml_add_comment(comment, value=key)
        else:
            self._yaml_add_comment(comment, key=key)

    def _yaml_get_pre_comment(self) -> list[CommentToken]:
        if self.ca.comment is None:
            pre: list[CommentToken] = []
            self.ca.comment = [None, pre]
            return pre
        if self.ca.comment[1] is None:
            self.ca.comment[1] = []
        return self.ca.comment[1]

    def _yaml_clear_pre_comment(self) -> list[CommentToken]:
        pre: list[CommentToken] = []
        if self.ca.comment is None:
            self.ca.comment = [None, pre]
        else:
            self.ca.comment[1] = pre
        return pre

    def _yaml_get_column(self, key: Any) -> int | None:
        """Column of a neighbouring entry's eol comment, so new comments line up."""
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
        """Replace the comment block preceding this node. *comment* is given without ``#``."""
        pre = self._yaml_clear_pre_comment()
        if comment.endswith('\n'):
            comment = comment[:-1]
        mark = CommentMark(indent)
        for line in comment.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                line = '# ' + line
            pre.append(CommentToken(line + '\n', mark))

    def yaml_set_comment_before_after_key(
        self,
        key: Any,
        before: str | None = None,
        indent: int = 0,
        after: str | None = None,
        after_indent: int | None = None,
    ) -> None:
        """Set the own-line comments before the key and after its value, without ``#``."""
        if after_indent is None:
            after_indent = indent + 2
        if before and len(before) > 1 and before.endswith('\n'):
            before = before[:-1]
        if after and after.endswith('\n'):
            after = after[:-1]
        rec = self._ca_record(key)

        def token(text: str, column: int) -> CommentToken:
            # an empty line stays empty: a blank line is trivia, not a '# ' comment
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
            rec[C_VALUE_POST].extend(
                token(line, after_indent) for line in after.split('\n')
            )

    def yaml_add_eol_comment(
        self, comment: str, key: Any = NotNone, column: int | None = None
    ) -> None:
        """Set the end-of-line comment of the entry *key* (of this node if no key)."""
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
    """Trivia store for the sequence-shaped containers: a list parallel to the elements."""

    _ca_eol_slot: ClassVar[int] = C_ELEM_EOL

    def _ca_store(self) -> list[list[Any] | None]:
        store = getattr(self, trivia_attrib, None)
        if store is None:
            store = [None] * len(self)  # type: ignore[arg-type]
            setattr(self, trivia_attrib, store)
        return store

    def _ca_items(self) -> _SeqCaItems:
        return _SeqCaItems(self)

    def _ca_record(self, idx: int) -> list[Any]:
        store = self._ca_store()
        i = idx + len(store) if idx < 0 else idx
        if not 0 <= i < len(store):
            raise IndexError(f'sequence comment index out of range: {idx}')
        rec = store[i]
        if rec is None:
            store[i] = rec = _record()
        return rec

    def _ca_order(self) -> Iterable[Any]:
        return range(len(self._ca_store()))


class CommentedSeq(_SeqTrivia, list):
    """A YAML sequence. Comments follow their element through every mutation."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        list.__init__(self, *args, **kw)

    # -- mutation: the store is kept parallel to the elements ------------------------------
    def __setitem__(self, idx: Any, value: Any) -> None:
        if isinstance(idx, slice):
            values = list(value)
            list.__setitem__(self, idx, values)
            self._ca_store()[idx] = [None] * len(values)
            return
        value = _keep_scalar_type(list.__getitem__(self, idx), value)
        list.__setitem__(self, idx, value)  # the comment belongs to the slot, so it stays

    def __delitem__(self, idx: Any) -> None:
        store = self._ca_store()
        list.__delitem__(self, idx)
        del store[idx]

    def insert(self, idx: int, value: Any) -> None:
        store = self._ca_store()
        list.insert(self, idx, value)
        store.insert(idx, None)

    def append(self, value: Any) -> None:
        self._ca_store().append(None)
        list.append(self, value)

    def extend(self, values: Iterable[Any]) -> None:
        values = list(values)
        self._ca_store().extend([None] * len(values))
        list.extend(self, values)

    def __iadd__(self, values: Iterable[Any]) -> Self:
        self.extend(values)
        return self

    def __add__(self, other: Any) -> list[Any]:
        return list.__add__(self, other)

    def pop(self, idx: int = -1) -> Any:
        store = self._ca_store()
        value = list.pop(self, idx)
        del store[idx]
        return value

    def remove(self, value: Any) -> None:
        del self[list.index(self, value)]

    def clear(self) -> None:
        list.clear(self)
        self._ca_store().clear()

    def reverse(self) -> None:
        list.reverse(self)
        self._ca_store().reverse()

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        item = lambda i: list.__getitem__(self, i)  # noqa: E731
        keyf = item if key is None else (lambda i: key(item(i)))
        order = sorted(range(list.__len__(self)), key=keyf, reverse=reverse)
        store = self._ca_store()
        items = [list.__getitem__(self, i) for i in order]
        store[:] = [store[i] for i in order]
        list.__init__(self, items)

    # -- copying ---------------------------------------------------------------------------
    def __deepcopy__(self, memo: dict[int, Any]) -> CommentedSeq:
        res = self.__class__()
        memo[id(self)] = res
        for x in self:
            res.append(_copy.deepcopy(x, memo))
        return self.copy_attributes(res, memo=memo)

    def copy(self) -> CommentedSeq:
        return self.copy_attributes(self.__class__(self))

    def __repr__(self) -> str:
        return list.__repr__(self)


class CommentedKeySeq(_SeqTrivia, tuple):
    """A sequence used as a mapping key: immutable, therefore hashable."""

    __hash__ = tuple.__hash__

    def __repr__(self) -> str:
        return f'CommentedKeySeq({tuple.__repr__(self)})'


class CommentedMap(CommentedBase, dict):
    """A YAML mapping. The trivia store is keyed by the mapping key, which never shifts."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        dict.__init__(self, *args, **kw)

    def _ca_order(self) -> Iterable[Any]:
        return list(self)

    def _merged(self) -> set[Any]:
        """Keys that came in through a ``<<`` merge rather than being written here."""
        m = getattr(self, '_yaml_merged_keys', None)
        if m is None:
            m = set()
            self._yaml_merged_keys = m
        return m

    # -- mutation --------------------------------------------------------------------------
    def __setitem__(self, key: Any, value: Any) -> None:
        if key in self:
            value = _keep_scalar_type(dict.__getitem__(self, key), value)
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.discard(key)  # written here now, so it is our own key
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        dict.__delitem__(self, key)
        self._ca_store().pop(key, None)  # no stale record to resurrect later
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.discard(key)

    def pop(self, key: Any, default: Any = NotNone) -> Any:
        try:
            value = dict.__getitem__(self, key)
        except KeyError:
            if default is NotNone:
                raise
            return default
        del self[key]
        return value

    def popitem(self) -> tuple[Any, Any]:
        key, value = dict.popitem(self)
        self._ca_store().pop(key, None)
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.discard(key)
        return key, value

    def clear(self) -> None:
        dict.clear(self)
        self._ca_store().clear()
        merged = getattr(self, '_yaml_merged_keys', None)
        if merged:
            merged.clear()

    def update(self, other: Any = (), /, **kw: Any) -> None:
        items = other.items() if hasattr(other, 'keys') else other
        for k, v in items:
            self[k] = v
        for k, v in kw.items():
            self[k] = v

    def __ior__(self, other: Any) -> Self:
        self.update(other)
        return self

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return dict.__getitem__(self, key)

    def move_to_end(self, key: Any, last: bool = True) -> None:
        """As ``OrderedDict.move_to_end``. The entry's comments move with it."""
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
        """Insert (or move) *key* at position *pos*, as counted in the emitted document."""
        self[key] = value
        self.move_to_end(key)
        for k in [k for k in self if k != key][pos:]:
            self.move_to_end(k)
        if comment is not None:
            self.yaml_add_eol_comment(comment, key=key)

    def rename(self, old: Any, new: Any) -> None:
        """Rename a key in place, keeping its position *and* its comments."""
        if new == old:
            return
        pos = list(self).index(old)
        record = self._ca_store().pop(old, None)
        value = self.pop(old)
        self.insert(pos, new, value)
        if record is not None:
            self._ca_store()[new] = record

    # -- merge keys (``<<``) ---------------------------------------------------------------
    def add_yaml_merge(self, value: Iterable[Mapping[Any, Any]]) -> None:
        """Record the mappings merged in through ``<<`` and expose their keys for lookup."""
        merge = value if isinstance(value, MergeList) else MergeList(value)
        setattr(self, merge_attrib, merge)
        merged = self._merged()
        for m in merge:
            for k, v in m.items():
                if not dict.__contains__(self, k):
                    dict.__setitem__(self, k, v)
                    merged.add(k)

    def non_merged_items(self) -> Iterator[tuple[Any, Any]]:
        """The entries this mapping owns: what the emitter writes out."""
        merged = getattr(self, '_yaml_merged_keys', None) or ()
        for k in dict.__iter__(self):
            if k not in merged:
                yield k, dict.__getitem__(self, k)

    def mlget(self, key: Any, default: Any = None, list_ok: bool = False) -> Any:
        """Multi-level get: ``m.mlget(['a', 'b'])`` is ``m['a']['b']`` with a default."""
        if not isinstance(key, list):
            return self.get(key, default)
        value: Any = self
        for k in key:
            if not list_ok and not isinstance(value, dict):
                raise TypeError(f'{value!r} is not a mapping')
            try:
                value = value[k]
            except (KeyError, IndexError, TypeError):
                return default
        return value

    # -- copying ---------------------------------------------------------------------------
    def copy(self) -> CommentedMap:
        return self.copy_attributes(self.__class__(self))

    def __deepcopy__(self, memo: dict[int, Any]) -> CommentedMap:
        res = self.__class__()
        memo[id(self)] = res
        for k in self:
            res[k] = _copy.deepcopy(dict.__getitem__(self, k), memo)
        return self.copy_attributes(res, memo=memo)

    def __repr__(self) -> str:
        return dict.__repr__(self)


class CommentedSet(CommentedBase, set):
    """A YAML ``!!set``. Iterates in document order; the store is keyed by member."""

    def __init__(self, values: Iterable[Any] = ()) -> None:
        values = list(values)
        set.__init__(self, values)
        self._yaml_order = values

    def _sync(self) -> list[Any]:
        """Reconcile the recorded order with the real members. Cheap, and cannot desync."""
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
        if value not in self:
            set.add(self, value)
            self._sync().append(value)
        else:
            set.add(self, value)

    def discard(self, value: Any) -> None:
        set.discard(self, value)
        self._sync()

    def remove(self, value: Any) -> None:
        set.remove(self, value)
        self._sync()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._sync())

    def __repr__(self) -> str:
        return f'CommentedSet({self._sync()!r})'


class CommentedKeyMap(CommentedBase, tuple, Mapping):
    """A mapping used as a mapping key: tuple of ``(key, value)`` pairs, so it hashes."""

    __hash__ = tuple.__hash__

    def __new__(cls, *args: Any, **kw: Any) -> CommentedKeyMap:
        return tuple.__new__(cls, dict(*args, **kw).items())

    def __init__(self, *args: Any, **kw: Any) -> None:
        pass

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        for k, v in tuple.__iter__(self):
            if k == key:
                return v
        raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        return (k for k, _ in tuple.__iter__(self))

    def __len__(self) -> int:
        return tuple.__len__(self)

    def __contains__(self, key: Any) -> bool:
        return any(k == key for k, _ in tuple.__iter__(self))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other)
        return tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def _ca_order(self) -> Iterable[Any]:
        return list(self)

    @classmethod
    def fromkeys(cls, keys: Iterable[Any], value: Any = None) -> CommentedKeyMap:
        return cls(dict.fromkeys(keys, value))

    def __repr__(self) -> str:
        return f'CommentedKeyMap({dict(self.items())!r})'


class TaggedScalar(CommentedBase, str):
    """A scalar with a tag we do not construct: round-trips verbatim (DESIGN.md 5.4)."""

    def __new__(
        cls, value: str = '', style: str | None = None, tag: Tag | str | None = None
    ) -> TaggedScalar:
        return str.__new__(cls, value)

    def __init__(
        self, value: str = '', style: str | None = None, tag: Tag | str | None = None
    ) -> None:
        self.style = style
        if tag is not None:
            self.tag = tag

    @property
    def value(self) -> str:
        return str.__str__(self)

    @value.setter
    def value(self, _: Any) -> None:
        raise TypeError('TaggedScalar is immutable; build a new one')

    def __repr__(self) -> str:
        return f'TaggedScalar(value={self.value!r}, style={self.style!r}, tag={self.tag!r})'
