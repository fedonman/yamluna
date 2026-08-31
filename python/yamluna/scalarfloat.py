"""YAML float scalars: a ``float`` subclass that remembers how it was written.

``1.0e+3``, ``.5``, ``3.``, ``1_000.0``, ``-.inf`` and ``.nan`` all parse to the same handful
of doubles, and none of them can be recovered from the double afterwards — so the instance
keeps the source lexeme and ``lexeme()`` hands it straight back.

The ruamel-shaped description of the layout (``_width``, ``_prec``, ``_m_sign``, ``_m_lead0``,
``_exp``, ``_e_width``, ``_e_sign``, ``_underscore``) is parsed out too, because ported code
reads those fields.
"""

from __future__ import annotations

import math
import re
from typing import Any, Self

from yamluna.scalarstring import _Anchored, _SCALAR_SLOTS

__all__ = ['ScalarFloat', 'from_lexeme']

_FLOAT_RE = re.compile(
    r"""^(?P<sign>[-+]?)
         (?P<mantissa>[0-9_]*\.[0-9_]*|[0-9_]+)
         (?:(?P<exp>[eE])(?P<exponent>[-+]?[0-9_]+))?$""",
    re.VERBOSE,
)
_INF_RE = re.compile(r'^(?P<sign>[-+]?)\.?(?:inf|Inf|INF)$')
_NAN_RE = re.compile(r'^\.?(?:nan|NaN|NAN)$')


def _leading_zeros(text: str) -> int:
    """ruamel's ``_m_lead0``: zeros before the first significant digit."""
    count = 0
    for ch in text:
        if ch == '0':
            count += 1
        elif ch != '.':
            break
    return count


class ScalarFloat(_Anchored, float):
    """A float that round-trips its source spelling."""

    __slots__ = (
        *_SCALAR_SLOTS,
        '_width',
        '_prec',
        '_m_sign',
        '_m_lead0',
        '_exp',
        '_e_width',
        '_e_sign',
        '_underscore',
    )

    def __new__(
        cls,
        value: Any = 0.0,
        *,
        width: int | None = None,
        prec: int | None = None,
        m_sign: str | bool = False,
        m_lead0: int = 0,
        exp: str | None = None,
        e_width: int | None = None,
        e_sign: bool = False,
        underscore: list[Any] | None = None,
        lexeme: str | None = None,
        anchor: str | None = None,
    ) -> Self:
        self = float.__new__(cls, value)
        self._width = width
        self._prec = prec
        self._m_sign = m_sign
        self._m_lead0 = m_lead0
        self._exp = exp
        self._e_width = e_width
        self._e_sign = e_sign
        self._underscore = underscore
        self._lexeme = lexeme
        if anchor is not None:
            self.yaml_set_anchor(anchor, always_dump=True)
        return self

    @classmethod
    def from_lexeme(cls, text: str) -> ScalarFloat:
        """Parse a float lexeme.  ``.lexeme()`` returns `text` byte-for-byte."""
        return from_lexeme(text)

    def lexeme(self) -> str:
        """The source form verbatim, or a plain rendering of the value.

        ponytail: without a source lexeme the layout fields are ignored and the value is
        rendered with ``repr``.  ruamel's field-driven float formatter is forty lines of
        mantissa surgery that only matters for floats a user built by hand *with* explicit
        width/prec — write it when someone actually does that.
        """
        if self._lexeme is not None:
            return self._lexeme
        value = float(self)
        if math.isnan(value):
            return '.nan'
        if math.isinf(value):
            return '-.inf' if value < 0 else '.inf'
        return repr(value)

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.lexeme()})'

    def dump(self, out: Any = None) -> None:
        """Print the layout fields (ruamel's debugging helper)."""
        print(
            f'ScalarFloat({self.lexeme()}| w:{self._width}, p:{self._prec}, '
            f's:{self._m_sign}, lz:{self._m_lead0}, _:{self._underscore}|{self._exp}'
            f', w:{self._e_width}, s:{self._e_sign})',
            file=out,
        )


def from_lexeme(text: str) -> ScalarFloat:
    """Build a `ScalarFloat` from a float lexeme.

    >>> from_lexeme('1.0e+3').lexeme()
    '1.0e+3'
    """
    if (m := _INF_RE.match(text)) is not None:
        return ScalarFloat(-math.inf if m['sign'] == '-' else math.inf, lexeme=text)
    if _NAN_RE.match(text) is not None:
        return ScalarFloat(math.nan, lexeme=text)

    m = _FLOAT_RE.match(text)
    if m is None:
        raise ValueError(f'not a float lexeme: {text!r}')
    mantissa, sign = m['mantissa'], m['sign']
    kw: dict[str, Any] = {
        'lexeme': text,
        'm_sign': sign or False,
        'm_lead0': _leading_zeros(mantissa),
        'prec': mantissa.find('.'),
        'underscore': [0, False, False] if '_' in text else None,
    }
    if m['exp'] is None:
        # ruamel measures the whole lexeme here, sign included.
        kw['width'] = len(text)
    else:
        kw['width'] = len(mantissa)
        kw['exp'] = m['exp']
        kw['e_width'] = len(m['exponent'])
        kw['e_sign'] = m['exponent'][0] in '+-'
    return ScalarFloat(float(text.replace('_', '')), **kw)


if __name__ == '__main__':
    for lexeme in ('1.0e+3', '.5', '3.', '1e3', '1_000.0', '+1.5e-3', '-.inf', '.inf',
                   '.nan', '0.0', '-0.5', '1.0E10'):
        got = from_lexeme(lexeme)
        assert got.lexeme() == lexeme, (lexeme, got.lexeme())
        expected = float(lexeme.replace('_', '').replace('.inf', 'inf').replace('.nan', 'nan'))
        assert math.isnan(got) if math.isnan(expected) else got == expected, lexeme
    assert ScalarFloat(1.5).lexeme() == '1.5'
    assert ScalarFloat(math.inf).lexeme() == '.inf'
    print('scalarfloat ok')
