r"""YAML string scalars: one `str` subclass per scalar style.

Every scalar type in `yamluna` can carry the source lexeme, the characters exactly as they
were written, quotes and block header included. `lexeme()` gives that text back verbatim,
which is what makes an unmodified round trip byte-exact.

The string value itself is always the cooked one, with escapes resolved and block scalars
folded. Python never formats YAML text; the Rust core cooks a lexeme and the Rust emitter
writes one, so this module never re-implements an unescaper. That is why `from_lexeme` takes
the cooked value alongside the raw text for the styles that encode something.

Assign one of these classes to force a style on the next dump:

```python
data['note'] = LiteralScalarString('one\ntwo\n')
```

```yaml
note: |
  one
  two
```
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
    """Return the package's `Anchor` class, or a fallback when nothing supplies one."""
    # Two module names are tried because this package keeps `Anchor` in `yamluna.comments`
    # while ruamel keeps it in an `anchor` module of its own.
    for mod in ('yamluna.comments', 'yamluna.anchor'):
        try:
            return importlib.import_module(mod).Anchor  # type: ignore[no-any-return]
        except (ImportError, AttributeError):
            continue

    class Anchor:
        """`&name` on a node, defined here only when neither module has the class."""

        __slots__ = ('value', 'always_dump')
        attrib: ClassVar[str] = '_yaml_anchor'

        def __init__(self) -> None:
            self.value: str | None = None
            self.always_dump: bool = False

        def __repr__(self) -> str:
            return f'Anchor({self.value!r}{", (always dump)" if self.always_dump else ""})'

    return Anchor


Anchor = _anchor_class()


# `_Attr` and `_Anchored` live in this module because `ScalarString` is the canonical
# anchored scalar; the int, float, bool and timestamp modules import them from here.
class _Attr:
    """An optional attribute that reads as `None` until it is first assigned."""

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
    """`anchor`, `lc` and `comment`, shared by every scalar type.

    Subclasses that use `__slots__` must slot `_SCALAR_SLOTS`.
    """

    __slots__ = ()

    # The private names match the `*_attrib` constants in `yamluna.comments` wherever the
    # meaning matches, so generic loader code that writes `setattr(node, LineCol.attrib, ...)`
    # reaches a scalar as well as a collection.
    lc = _Attr('_yaml_line_col')
    """Source position of the scalar as a `LineCol`.

    Reads as `None` until something assigns it. Loading a document leaves it unset, as
    ruamel does for scalars.
    """

    comment = _Attr()
    """Comment(s) attached to the scalar.

    Reads as `None` until something assigns it. Loading a document leaves it unset; the
    comments around a scalar are kept on the collection that holds it.
    """

    @property
    def anchor(self) -> Any:
        """The scalar's `Anchor`, created empty on first access."""
        a = getattr(self, '_yaml_anchor', None)
        if a is None:
            a = Anchor()
            self._yaml_anchor = a  # type: ignore[attr-defined]
        return a

    def yaml_anchor(self, any: bool = False) -> Any:
        """Return the scalar's `Anchor`, or `None` when there is nothing to dump.

        Unlike reading `anchor`, this never creates one.

        Args:
            any: Return the anchor even when it is not marked to be dumped.

        Returns:
            The `Anchor`, or `None` when the scalar has never been given one, or has one
            whose `always_dump` is false and `any` is false.
        """
        a = getattr(self, '_yaml_anchor', None)
        if a is None:
            return None
        return a if (any or a.always_dump) else None

    def yaml_set_anchor(self, value: str | None, always_dump: bool = False) -> None:
        """Name the scalar's anchor, creating one if it has none.

        Args:
            value: The anchor name without its `&`. `None` clears the name.
            always_dump: Write `&value` even when no alias points at this scalar.
        """
        self.anchor.value = value
        self.anchor.always_dump = always_dump


# Slot names every scalar type needs. `int` subclasses cannot use `__slots__` at all
# ("nonempty __slots__ not supported for subtype of 'int'"), so those carry a `__dict__`.
#
# `_yaml_doc` is the constructor's `DOC_ATTRIB`: a document whose root is a scalar has
# nowhere else to keep its own `%YAML`, `%TAG`, `---` and `...`. `_yaml_node` is the
# constructor's `NODE_ATTRIB`: the record the scalar was loaded from, which is how the source
# facts this package carries but never reads (where the `&anchor` and the tag were written)
# ride back to the emitter.
_SCALAR_SLOTS = (
    '_yaml_anchor', '_yaml_line_col', '_comment', '_lexeme', '_yaml_doc', '_yaml_node',
)


class ScalarString(_Anchored, str):
    """A `str` that remembers which YAML scalar style wrote it.

    The base of the five style classes. It behaves as a plain scalar on its own; pick a
    subclass to force a style on the next dump.

    Args:
        value: The cooked string, with escapes already resolved.
        lexeme: The source text, quotes and block header included. Pass it only when it is
            what the source really said: the emitter reproduces it verbatim.
        anchor: An anchor name to attach, marked to be written even when nothing aliases
            this scalar.
    """

    __slots__ = _SCALAR_SLOTS

    style: ClassVar[str] = ''
    """The style indicator: `|`, `>`, `'`, `"`, or the empty string for plain."""

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
        """Return the source text verbatim, or `None` for a string not loaded from source.

        Returns:
            The characters the source used, quotes and block header included. `None` when
            this string was built or edited in Python, in which case the emitter renders it
            afresh from `style` and the cooked value.
        """
        return self._lexeme

    def replace(self, old: Any, new: Any, count: Any = -1, /) -> Self:
        """Return a copy with `old` replaced by `new`, keeping the style.

        Args:
            old: The substring to replace.
            new: The text to put in its place.
            count: Replace at most this many occurrences. `-1` replaces every one.

        Returns:
            A new instance of the same class. Its `lexeme()` is `None`, because the text no
            longer matches what the source said, so the emitter renders it afresh.
        """
        return type(self)(str.replace(self, old, new, count))


class LiteralScalarString(ScalarString):
    r"""A string written as a `|` block scalar, with its line breaks kept.

    A value that does not end in a newline gets the strip indicator, `|-`.

    `comment` is where the comment written after the `|` belongs; loading does not fill it
    in.

    Example:
        ```python
        data['note'] = LiteralScalarString('one\ntwo\n')
        ```

        ```yaml
        note: |
          one
          two
        ```
    """

    __slots__ = ()
    style = '|'


PreservedScalarString = LiteralScalarString


class FoldedScalarString(ScalarString):
    r"""A string written as a `>` block scalar, whose line breaks fold back into spaces.

    A value that does not end in a newline gets the strip indicator, `>-`.

    `comment` is where the comment written after the `>` belongs; loading does not fill it
    in.

    Example:
        ```python
        data['note'] = FoldedScalarString('one long paragraph\n')
        ```

        ```yaml
        note: >
          one long paragraph
        ```
    """

    __slots__ = ('_fold_pos',)
    style = '>'

    fold_pos = _Attr()
    """Offsets in the cooked value where the source folded a line.

    Reads as `None` until something assigns it. A scalar loaded from a file keeps its folds
    through its lexeme rather than through this list, so nothing in the package reads it.
    """


class SingleQuotedScalarString(ScalarString):
    """A string written inside single quotes, where the only escape is `''` for a quote.

    A scalar loaded from quoted source keeps its quotes on dump whatever the settings say,
    because its lexeme is reproduced verbatim. One you construct yourself is quoted only
    when the `YAML` instance has `preserve_quotes = True`; otherwise the emitter picks the
    cheapest style the value survives, usually plain.

    Example:
        ```python
        yaml.preserve_quotes = True
        data['who'] = SingleQuotedScalarString("it's")
        ```

        ```yaml
        who: 'it''s'
        ```
    """

    __slots__ = ()
    style = "'"


class DoubleQuotedScalarString(ScalarString):
    r"""A string written inside double quotes, with the full set of backslash escapes.

    A scalar loaded from quoted source keeps its quotes on dump whatever the settings say,
    because its lexeme is reproduced verbatim. One you construct yourself is quoted only
    when the `YAML` instance has `preserve_quotes = True`.

    Example:
        ```python
        yaml.preserve_quotes = True
        data['tabbed'] = DoubleQuotedScalarString('a\tb')
        ```

        ```yaml
        tabbed: "a\tb"
        ```
    """

    __slots__ = ()
    style = '"'


class PlainScalarString(ScalarString):
    """A string written without quotes, whenever the value can be written that way.

    The style is a request, not a guarantee. A value that would not read back as itself gets
    quoted anyway: an empty string, one with a leading or trailing space, a control
    character, a leading YAML indicator, a `: ` or a ` #` inside it, or anything the loader
    would resolve to a number, a boolean or a timestamp.

    Example:
        ```python
        data['greeting'] = PlainScalarString('hello')
        data['mapping-ish'] = PlainScalarString('a: b')
        ```

        ```yaml
        greeting: hello
        mapping-ish: 'a: b'
        ```
    """

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
    """Build the style class that matches the source text `raw`.

    Args:
        raw: The scalar exactly as the source wrote it, style indicator included.
        value: The cooked string. Required for double-quoted, literal and folded scalars,
            whose decoding the Rust core has already done. Plain and single-quoted text is
            decoded here, so `value` may be left out for those two.

    Returns:
        An instance of the class for the style `raw` starts with, carrying `raw` as its
        lexeme. Text with no recognised indicator becomes a `PlainScalarString`.

    Raises:
        ValueError: `value` is missing for a style this function does not decode.

    Example:
        ```pycon
        >>> from_lexeme("'it''s'").lexeme()
        "'it''s'"

        ```
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
    r"""Normalise the line breaks in `s` and mark it for dumping as a literal block.

    Args:
        s: The text. Both `\r\n` and a lone `\r` become `\n`.

    Returns:
        A `LiteralScalarString` holding the normalised text and no lexeme, so the emitter
        writes it as a `|` block.

    Example:
        ```python
        data['note'] = preserve_literal('one\r\ntwo\n')
        ```

        ```yaml
        note: |
          one
          two
        ```
    """
    return LiteralScalarString(s.replace('\r\n', '\n').replace('\r', '\n'))


def walk_tree(
    base: Any,
    map: dict[str, Callable[[str], Any]] | None = None,
) -> None:
    r"""Convert the strings in a loaded tree, in place and recursively.

    Args:
        base: The mapping or sequence to walk. Anything else is left untouched.
        map: An ordered mapping of substring to converter. For each string value the first
            key found in it wins and the converter's result replaces the string. Defaults to
            `{'\n': preserve_literal}`, which turns every multi-line string into a literal
            block scalar.

    Example:
        ```python
        walk_tree(data, map={'\n': preserve_literal, ':': SingleQuotedScalarString})
        ```
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
