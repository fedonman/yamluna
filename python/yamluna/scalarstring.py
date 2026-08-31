"""YAML string scalars: one ``str`` subclass per scalar style (DESIGN.md §4.1).

Every scalar type in ``yamluna`` may carry the **source lexeme** — the characters exactly as
they were written, quotes and block header included.  ``.lexeme()`` hands it back verbatim,
which is what makes an unmodified round trip byte-exact (DESIGN.md §2.4 invariant).

The string value itself is always the *cooked* one (escapes resolved, block scalars folded);
decoding a lexeme into a cooked value is the Rust core's job (DESIGN.md §2, ``Node.raw`` vs
``Node.value``), so this module never re-implements an unescaper.  Consequently
``from_lexeme`` takes the cooked value alongside the raw text for the styles that actually
encode something.

This module also hosts the two helpers every other scalar module needs (``_Anchored``,
``_Attr``); they live here because ``ScalarString`` is the canonical anchored scalar.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, MutableMapping, MutableSequence
from typing import Any, ClassVar, Self

# `PreservedScalarString` is ruamel's old name for `LiteralScalarString`.
__all__ = [
    'DoubleQuotedScalarString',
    'FoldedScalarString',
    'LiteralScalarString',
    'PlainScalarString',
    'PreservedScalarString',
    'ScalarString',
    'SingleQuotedScalarString',
    'from_lexeme',
    'preserve_literal',
    'walk_tree',
]


def _anchor_class() -> type:
    """Locate the package's ``Anchor``, tolerating it not existing yet."""
    for mod in ('yamluna.comments', 'yamluna.anchor'):
        try:
            return importlib.import_module(mod).Anchor  # type: ignore[no-any-return]
        except (ImportError, AttributeError):
            continue

    class Anchor:
        """`&name` on a node.  Fallback definition, used only when the package has none."""

        __slots__ = ('value', 'always_dump')
        attrib: ClassVar[str] = '_yaml_anchor'

        def __init__(self) -> None:
            self.value: str | None = None
            self.always_dump: bool = False

        def __repr__(self) -> str:
            return f'Anchor({self.value!r}{", (always dump)" if self.always_dump else ""})'

    return Anchor


Anchor = _anchor_class()


class _Attr:
    """An optional attribute that reads as ``None`` until it is first assigned."""

    __slots__ = ('_name',)

    def __init__(self, name: str | None = None) -> None:
        self._name = name  # defaults to '_' + the attribute name

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = self._name or '_' + name

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        return self if obj is None else getattr(obj, self._name, None)

    def __set__(self, obj: Any, value: Any) -> None:
        setattr(obj, self._name, value)


class _Anchored:
    """`.anchor` / `.lc` / `.comment`, shared by every scalar type.

    Subclasses that use ``__slots__`` must slot `_SCALAR_SLOTS`.  The private names match
    ``comments.py``'s ``*_attrib`` constants where the meaning matches, so generic
    ``setattr(node, LineCol.attrib, ...)`` loader code works on scalars too.
    """

    __slots__ = ()

    lc = _Attr('_yaml_line_col')  # comments.line_col_attrib
    """`LineCol` of the scalar in its source document, set by the loader."""

    comment = _Attr()
    """Comment(s) attached to the scalar, set by the loader."""

    @property
    def anchor(self) -> Any:
        a = getattr(self, '_yaml_anchor', None)
        if a is None:
            a = Anchor()
            self._yaml_anchor = a  # type: ignore[attr-defined]
        return a

    def yaml_anchor(self, any: bool = False) -> Any:
        a = getattr(self, '_yaml_anchor', None)
        if a is None:
            return None
        return a if (any or a.always_dump) else None

    def yaml_set_anchor(self, value: str | None, always_dump: bool = False) -> None:
        self.anchor.value = value
        self.anchor.always_dump = always_dump


#: Slot names every scalar type needs.  ``int`` subclasses cannot use ``__slots__`` at all
#: ("nonempty __slots__ not supported for subtype of 'int'"), so they carry a ``__dict__``.
#:
#: ``_yaml_doc`` is ``constructor.DOC_ATTRIB``: a document whose root is a scalar has nowhere
#: else to keep its own ``%YAML``, ``%TAG``, ``---`` and ``...``.  ``_yaml_node`` is
#: ``constructor.NODE_ATTRIB``: the record the scalar was loaded from, which is where the
#: source facts this package carries but never reads (where the ``&anchor`` and the tag were
#: written) ride back to the emitter.
_SCALAR_SLOTS = (
    '_yaml_anchor', '_yaml_line_col', '_comment', '_lexeme', '_yaml_doc', '_yaml_node',
)


class ScalarString(_Anchored, str):
    """A ``str`` that remembers which YAML scalar style wrote it."""

    __slots__ = _SCALAR_SLOTS

    style: ClassVar[str] = ''
    """The style indicator: ``|``, ``>``, ``'``, ``"`` or ``''`` for plain."""

    def __new__(
        cls,
        value: str = '',
        *,
        lexeme: str | None = None,
        anchor: str | None = None,
    ) -> Self:
        self = str.__new__(cls, value)
        self._lexeme = lexeme
        if anchor is not None:
            self.yaml_set_anchor(anchor, always_dump=True)
        return self

    def lexeme(self) -> str | None:
        """The source form verbatim, or ``None`` if this string was not loaded from source.

        A string built or edited in Python has no lexeme: the Rust emitter re-renders it from
        ``style`` and the cooked value (DESIGN.md §0 — Python never formats YAML text).
        """
        return self._lexeme

    def replace(self, old: Any, new: Any, count: Any = -1, /) -> Self:
        # Keep the style, drop the lexeme: the text changed.
        return type(self)(str.replace(self, old, new, count))


class LiteralScalarString(ScalarString):
    """``|`` — newlines kept.  ``.comment`` is the comment after the ``|``."""

    __slots__ = ()
    style = '|'


PreservedScalarString = LiteralScalarString


class FoldedScalarString(ScalarString):
    """``>`` — newlines folded.  ``.comment`` is the comment after the ``>``."""

    __slots__ = ('_fold_pos',)
    style = '>'

    fold_pos = _Attr()
    """Offsets in the cooked value where the source folded a line, so folds survive a dump."""


class SingleQuotedScalarString(ScalarString):
    """``'...'`` — only ``''`` is an escape."""

    __slots__ = ()
    style = "'"


class DoubleQuotedScalarString(ScalarString):
    """``"..."`` — full backslash escapes."""

    __slots__ = ()
    style = '"'


class PlainScalarString(ScalarString):
    """Unquoted."""

    __slots__ = ()
    style = ''


_BY_STYLE: dict[str, type[ScalarString]] = {
    '|': LiteralScalarString,
    '>': FoldedScalarString,
    "'": SingleQuotedScalarString,
    '"': DoubleQuotedScalarString,
    '': PlainScalarString,
}


def from_lexeme(raw: str, value: str | None = None) -> ScalarString:
    """Build the right style class for the source text `raw`.

    `value` is the cooked string.  It may be omitted only for plain and single-quoted
    scalars, whose decoding is trivial; for the other styles the caller (the loader, which
    gets both from the Rust core) must supply it.
    """
    cls = _BY_STYLE.get(raw[:1], PlainScalarString)
    if value is None:
        if cls is PlainScalarString:
            value = raw
        elif cls is SingleQuotedScalarString:
            value = raw[1:-1].replace("''", "'")
        else:
            raise ValueError(f'cooked value required for a {cls.style!r} scalar')
    return cls(value, lexeme=raw)


def preserve_literal(s: str) -> LiteralScalarString:
    """Normalise line breaks and mark `s` for dumping as a literal block scalar."""
    return LiteralScalarString(s.replace('\r\n', '\n').replace('\r', '\n'))


def walk_tree(
    base: Any,
    map: dict[str, Callable[[str], Any]] | None = None,
) -> None:
    """Recursively convert strings in a loaded tree, in place.

    By default any string containing a newline becomes a `LiteralScalarString`.  Pass an
    ordered mapping of substring -> converter to do something else; the first key found in a
    string wins::

        walk_tree(data, map={'\\n': preserve_literal, ':': SingleQuotedScalarString})
    """
    if map is None:
        map = {'\n': preserve_literal}

    if isinstance(base, MutableMapping):
        items: Any = base.items()
    elif isinstance(base, MutableSequence):
        items = enumerate(base)
    else:
        return

    for key, value in list(items):
        if isinstance(value, str):
            for substring, convert in map.items():
                if substring in value:
                    base[key] = convert(value)
                    break
        else:
            walk_tree(value, map=map)
