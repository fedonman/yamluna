"""YAML float scalars: a `float` subclass that remembers how it was written.

`1.0e+3`, `.5`, `3.`, `1_000.0`, `-.inf` and `.nan` all parse to the same handful of doubles,
and none of those spellings can be recovered from the double afterwards, so the instance
keeps the source lexeme and `lexeme()` hands it straight back. Every float loaded from a
document is a `ScalarFloat`.

The description of the layout (`_width`, `_prec`, `_m_sign`, `_m_lead0`, `_exp`, `_e_width`,
`_e_sign`, `_underscore`) is parsed out too, in ruamel's shape, because ported code reads
those fields.

```python
data['ratio'] = ScalarFloat(1.5)
```

```yaml
ratio: 1.5
```
"""

from __future__ import annotations

import math
import re
from typing import IO, Any, Self, SupportsFloat, SupportsIndex

from yamluna.scalarstring import _SCALAR_SLOTS, _Anchored

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
    """Count the zeros before the first significant digit, ruamel's `_m_lead0`."""
    count = 0
    for ch in text:
        if ch == '0':
            count += 1
        elif ch != '.':
            break
    return count


class ScalarFloat(_Anchored, float):
    """A float that round-trips its source spelling.

    Args:
        value: Anything `float()` accepts.
        width: Number of characters in the lexeme, or in the mantissa when there is an
            exponent.
        prec: Index of the `.` in the mantissa, or `-1` when there is none.
        m_sign: The mantissa's sign character, or `False` when it had none.
        m_lead0: Zeros before the first significant digit of the mantissa.
        exp: The exponent marker the source used, `e` or `E`, or `None` for no exponent.
        e_width: Number of characters in the exponent.
        e_sign: Whether the exponent carried an explicit sign.
        underscore: `[step, leading, trailing]`, the spacing of `_` separators.
        lexeme: The source text. When present `lexeme()` returns it unchanged and the layout
            fields are not consulted.
        anchor: An anchor name to attach, marked to be written even when nothing aliases
            this scalar.

    """

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

    # ruamel's constructor: every layout field is one keyword argument.
    def __new__(  # noqa: PLR0913
        cls,
        value: str | bytes | SupportsFloat | SupportsIndex = 0.0,
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
        """Build the float, keeping `lexeme` and attaching `anchor` when given."""
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
        """Parse a float lexeme.

        Args:
            text: The float exactly as the source wrote it.

        Returns:
            A `ScalarFloat` whose `lexeme()` returns `text` byte for byte, with the layout
            fields filled in from the spelling.

        Raises:
            ValueError: `text` is not a float lexeme.

        """
        return from_lexeme(text)

    def lexeme(self) -> str:
        """Return the source text verbatim, or a plain rendering of the value.

        Returns:
            The text the source used, when this float was loaded from one. Otherwise
            `.nan`, `.inf`, `-.inf`, or `repr(value)`; a float built in Python is rendered
            from its value alone, so `width`, `prec` and the other layout fields you passed
            do not shape the output.

        """
        # ponytail: ruamel's field-driven float formatter is forty lines of mantissa surgery
        # that only pays off for a float built by hand with an explicit width or precision.
        # Write it when someone actually does that.
        if self._lexeme is not None:
            return self._lexeme
        value = float(self)
        if math.isnan(value):
            return '.nan'
        if math.isinf(value):
            return '-.inf' if value < 0 else '.inf'
        return repr(value)

    def __repr__(self) -> str:
        """Return `ScalarFloat(lexeme)`."""
        return f'{type(self).__name__}({self.lexeme()})'

    def dump(self, out: IO[str] | None = None) -> None:
        """Print the lexeme and every layout field, ruamel's debugging helper.

        Args:
            out: An open text file to print to. `None` prints to standard output.

        """
        print(
            f'ScalarFloat({self.lexeme()}| w:{self._width}, p:{self._prec}, '
            f's:{self._m_sign}, lz:{self._m_lead0}, _:{self._underscore}|{self._exp}'
            f', w:{self._e_width}, s:{self._e_sign})',
            file=out,
        )


def from_lexeme(text: str) -> ScalarFloat:
    """Build a `ScalarFloat` from a float lexeme.

    Args:
        text: The float exactly as the source wrote it. Accepts an optional sign, a mantissa
            with or without a `.`, an optional `e` or `E` exponent, underscores anywhere in
            the digits, and the YAML spellings of infinity and not-a-number (`.inf`,
            `-.inf`, `.nan`, and their upper and mixed case forms).

    Returns:
        A `ScalarFloat` whose `lexeme()` returns `text` byte for byte.

    Raises:
        ValueError: `text` is not a float lexeme.

    Example:
        ```pycon
        >>> from_lexeme('1.0e+3').lexeme()
        '1.0e+3'

        ```

    """
    if (m := _INF_RE.match(text)) is not None:
        return ScalarFloat(-math.inf if m['sign'] == '-' else math.inf, lexeme=text)
    if _NAN_RE.match(text) is not None:
        return ScalarFloat(math.nan, lexeme=text)

    m = _FLOAT_RE.match(text)
    if m is None:
        msg = f'not a float lexeme: {text!r}'
        raise ValueError(msg)
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
    # Run by hand as a self-check: `assert` is both the check and the report here, and
    # nobody runs a self-check under `python -O`.
    for lexeme in (
        '1.0e+3',
        '.5',
        '3.',
        '1e3',
        '1_000.0',
        '+1.5e-3',
        '-.inf',
        '.inf',
        '.nan',
        '0.0',
        '-0.5',
        '1.0E10',
    ):
        got = from_lexeme(lexeme)
        assert got.lexeme() == lexeme, (lexeme, got.lexeme())  # noqa: S101
        expected = float(lexeme.replace('_', '').replace('.inf', 'inf').replace('.nan', 'nan'))
        assert math.isnan(got) if math.isnan(expected) else got == expected, lexeme  # noqa: S101
    assert ScalarFloat(1.5).lexeme() == '1.5'  # noqa: S101
    assert ScalarFloat(math.inf).lexeme() == '.inf'  # noqa: S101
    print('scalarfloat ok')  # noqa: T201
