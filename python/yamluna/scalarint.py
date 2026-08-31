"""YAML integer scalars: ``int`` subclasses that remember how they were written.

`0x1F`, `0o755`, `0b1010`, `1_000_000`, `+5` and `007` all mean an integer and all have to
come back out of a round trip byte-for-byte, so each instance keeps the source lexeme.  The
ruamel-shaped formatting fields (``_width``, ``_underscore``, plus ``_sign``, which ruamel
drops and thereby loses ``+5``) are parsed out of the lexeme as well: they are what
``lexeme()`` falls back to for a value that was built in Python rather than loaded.

``int`` forbids ``__slots__`` on its subclasses, so these carry a ``__dict__`` — which also
means user code can hang attributes off them, as it can with ruamel's.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Self

from yamluna.scalarstring import _Anchored

__all__ = ['BinaryInt', 'HexInt', 'OctalInt', 'ScalarInt', 'from_lexeme']

_INT_RE = re.compile(
    r"""^(?P<sign>[-+]?)
         (?: 0(?P<base>[bBxXoO])(?P<body>[0-9a-fA-F_]+)
           | (?P<dec>[0-9_]+) )$""",
    re.VERBOSE,
)

#: Format fields copied onto the result of in-place arithmetic.  ``_lexeme`` is deliberately
#: not among them: the value changed, so the source text no longer describes it.
_FMT_FIELDS = ('_width', '_underscore', '_sign', '_caps')


def _split_underscores(body: str) -> list[Any] | None:
    """ruamel's ``[step, leading, trailing]`` description of `body`'s underscores."""
    core = body.rstrip('_')
    if '_' not in core:
        return None if '_' not in body else [0, body.startswith('_'), True]
    return [len(core) - core.rindex('_') - 1, body.startswith('_'), body.endswith('_')]


def _insert_underscores(digits: str, underscore: list[Any] | None) -> str:
    if not underscore:
        return digits
    step, leading, trailing = underscore
    if step:
        pos = len(digits) - step
        while pos > 0:
            digits = digits[:pos] + '_' + digits[pos:]
            pos -= step
    if leading:
        digits = '_' + digits
    if trailing:
        digits += '_'
    return digits


class ScalarInt(_Anchored, int):
    """A decimal integer, with its width, underscores and explicit ``+`` preserved."""

    prefix: ClassVar[str] = ''

    def __new__(
        cls,
        value: Any = 0,
        *,
        width: int | None = None,
        underscore: list[Any] | None = None,
        sign: str = '',
        lexeme: str | None = None,
        anchor: str | None = None,
    ) -> Self:
        self = int.__new__(cls, value)
        self._width = width
        self._underscore = underscore
        self._sign = sign
        self._lexeme = lexeme
        if anchor is not None:
            self.yaml_set_anchor(anchor, always_dump=True)
        return self

    @classmethod
    def from_lexeme(cls, text: str) -> ScalarInt:
        """Parse an integer lexeme into the matching subclass.  ``.lexeme()`` returns `text`."""
        return from_lexeme(text)

    def _digits(self) -> str:
        return format(abs(int(self)), 'd')

    def lexeme(self) -> str:
        """The source form verbatim, or a rendering of the formatting fields."""
        if self._lexeme is not None:
            return self._lexeme
        digits = self._digits()
        if self._width is not None:
            digits = digits.rjust(self._width, '0')
        sign = '-' if self < 0 else self._sign
        return f'{sign}{self.prefix}{_insert_underscores(digits, self._underscore)}'

    def _derived(self, value: int) -> Self:
        result = type(self)(value)
        for name in _FMT_FIELDS:
            if hasattr(self, name):
                setattr(result, name, getattr(self, name))
        return result

    # In-place arithmetic keeps the formatting (0x0f + 1 dumps as 0x10), as in ruamel.
    def __iadd__(self, other: Any) -> Self:
        return self._derived(int(self) + other)

    def __isub__(self, other: Any) -> Self:
        return self._derived(int(self) - other)

    def __imul__(self, other: Any) -> Self:
        return self._derived(int(self) * other)

    def __ifloordiv__(self, other: Any) -> Self:
        return self._derived(int(self) // other)

    def __ipow__(self, other: Any) -> Self:
        return self._derived(int(self) ** other)

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.lexeme()})'


class BinaryInt(ScalarInt):
    """``0b1010``."""

    prefix = '0b'

    def _digits(self) -> str:
        return format(abs(int(self)), 'b')


class OctalInt(ScalarInt):
    """``0o755``."""

    prefix = '0o'

    def _digits(self) -> str:
        return format(abs(int(self)), 'o')


class HexInt(ScalarInt):
    """``0x1f`` / ``0x1F`` — `caps` records which case the source used."""

    prefix = '0x'

    def __new__(cls, value: Any = 0, *, caps: bool = False, **kw: Any) -> Self:
        self = super().__new__(cls, value, **kw)
        self._caps = caps
        return self

    @property
    def caps(self) -> bool:
        return self._caps

    def _digits(self) -> str:
        return format(abs(int(self)), 'X' if self._caps else 'x')


_BY_BASE: dict[str, tuple[type[ScalarInt], int]] = {
    'b': (BinaryInt, 2),
    'x': (HexInt, 16),
    'o': (OctalInt, 8),
}


def from_lexeme(text: str) -> ScalarInt:
    """Build the right ``ScalarInt`` subclass from an integer lexeme.

    >>> from_lexeme('0o755').lexeme()
    '0o755'
    """
    m = _INT_RE.match(text)
    if m is None:
        raise ValueError(f'not an integer lexeme: {text!r}')
    sign = m['sign']
    body = m['body'] or m['dec']
    digits = body.replace('_', '')
    kw: dict[str, Any] = {
        'underscore': _split_underscores(body),
        'sign': sign,
        'lexeme': text,
    }
    if m['base'] is None:
        cls, base = ScalarInt, 10
    else:
        cls, base = _BY_BASE[m['base'].lower()]
        if cls is HexInt:
            kw['caps'] = any(c in 'ABCDEF' for c in digits)
    # Leading zeros are the only thing a width has to describe; ruamel does the same.
    if len(digits) > 1 and digits[0] == '0':
        kw['width'] = len(digits)
    value = int(digits, base)
    return cls(-value if sign == '-' else value, **kw)


if __name__ == '__main__':
    cases = {'0o755': 493, '0b1010': 10, '0x1F': 31, '1_000': 1000, '+5': 5,
             '-0x10': -16, '007': 7, '9' * 40: int('9' * 40)}
    for lexeme, value in cases.items():
        got = from_lexeme(lexeme)
        assert got.lexeme() == lexeme, (lexeme, got.lexeme())
        assert int(got) == value, (lexeme, int(got))
    x = from_lexeme('0x0f')
    x += 1
    assert x.lexeme() == '0x10', x.lexeme()
    print('scalarint ok')
