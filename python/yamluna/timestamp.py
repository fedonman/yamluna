"""YAML timestamp scalars: a ``datetime`` subclass that remembers its source spelling.

``2001-12-14t21:59:43.10-05:00``, ``2001-12-14 21:59:43.10 -5`` and ``2001-12-15T02:59:43.10Z``
are the same instant written three ways, and the separator, the space before the zone, the
zone's own shape and the number of fraction digits are all unrecoverable from the
``datetime`` — so the lexeme rides along and ``lexeme()`` hands it back byte-for-byte.

Divergence from ruamel: a date-only timestamp (``2002-12-14``) stays a `TimeStamp` here
instead of degrading to ``datetime.date``, which would drop the lexeme.  ``_yaml['date_only']``
records it, and `TimeStamp` is a ``datetime``, which is itself a ``date``, so ``isinstance``
checks in ported code still hold.
"""

from __future__ import annotations

import copy
import datetime
import re
from typing import Any, Self

from yamluna.scalarstring import _SCALAR_SLOTS, _Anchored

__all__ = ['TimeStamp', 'from_lexeme']

#: The YAML 1.1 timestamp form (https://yaml.org/type/timestamp.html).
_TIMESTAMP_RE = re.compile(
    r"""^(?P<year>[0-9]{4})-(?P<month>[0-9]{1,2})-(?P<day>[0-9]{1,2})
         (?:(?:(?P<t>[Tt])|[ \t]+)
            (?P<hour>[0-9]{1,2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})
            (?:\.(?P<fraction>[0-9]*))?
            (?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9]{1,2})
                              (?::?(?P<tz_minute>[0-9]{2}))?))?
         )?$""",
    re.VERBOSE,
)


class TimeStamp(_Anchored, datetime.datetime):
    """A ``datetime`` carrying the exact text it was parsed from."""

    __slots__ = ('_yaml', *_SCALAR_SLOTS)

    def __new__(cls, *args: Any, lexeme: str | None = None, **kw: Any) -> Self:
        self = datetime.datetime.__new__(cls, *args, **kw)
        self._lexeme = lexeme
        self._yaml: dict[str, Any] = {'t': False, 'tz': None, 'delta': 0, 'date_only': False}
        return self

    @classmethod
    def from_lexeme(cls, text: str) -> Self:
        """Parse a YAML timestamp.  ``.lexeme()`` returns `text` byte-for-byte."""
        m = _TIMESTAMP_RE.match(text)
        if m is None:
            raise ValueError(f'not a timestamp lexeme: {text!r}')

        micro, carry = 0, 0
        if fraction := m['fraction']:
            # Round half-up on the 7th digit, in integers so it is exact, and carry into the
            # seconds when .9999995 rounds over.
            digits = fraction[:7].ljust(7, '0')
            carry, micro = divmod(int(digits[:6]) + (int(digits[6]) > 4), 1_000_000)

        tzinfo = None
        if m['tz_sign']:
            offset = datetime.timedelta(
                hours=int(m['tz_hour']), minutes=int(m['tz_minute'] or 0),
            )
            tzinfo = datetime.timezone(-offset if m['tz_sign'] == '-' else offset)
        elif m['tz'] == 'Z':
            tzinfo = datetime.UTC

        self = cls(
            int(m['year']), int(m['month']), int(m['day']),
            int(m['hour'] or 0), int(m['minute'] or 0), int(m['second'] or 0),
            micro, tzinfo, lexeme=text,
        )
        if carry:
            self = self + datetime.timedelta(seconds=carry)
            self._lexeme = text
        self._yaml.update(t=m['t'] is not None, tz=m['tz'], date_only=m['hour'] is None)
        return self

    def lexeme(self) -> str:
        """The source form verbatim, or an ISO 8601 rendering."""
        if self._lexeme is not None:
            return self._lexeme
        if self._yaml['date_only']:
            return self.date().isoformat()
        return self.isoformat('T' if self._yaml['t'] else ' ')

    def __str__(self) -> str:
        return self.lexeme()

    def __repr__(self) -> str:
        return f'TimeStamp({self.lexeme()})'

    def __add__(self, other: Any) -> Any:  # datetime.__add__ bypasses __new__'s kwargs
        return _carry(self, datetime.datetime.__add__(self, other))

    def __deepcopy__(self, memo: Any) -> TimeStamp:
        return _carry(self, self, lexeme=True, yaml=copy.deepcopy(self._yaml, memo))

    def __reduce_ex__(self, protocol: int) -> tuple[Any, ...]:
        # datetime carries only the packed date bytes through pickling, and it overrides
        # __reduce_ex__ as well as __reduce__, so this is the hook that keeps the lexeme.
        _, args = datetime.datetime.__reduce_ex__(self, protocol)
        return (_unpickle, (args, self._lexeme, dict(self._yaml)))

    def __reduce__(self) -> tuple[Any, ...]:
        return self.__reduce_ex__(2)

    def replace(self, *args: Any, **kw: Any) -> TimeStamp:
        """As ``datetime.replace``, keeping the yaml formatting flags but not the lexeme."""
        return _carry(self, datetime.datetime.replace(self, *args, **kw))


def _carry(
    src: TimeStamp,
    result: datetime.datetime,
    *,
    lexeme: bool = False,
    yaml: dict[str, Any] | None = None,
) -> TimeStamp:
    """Rebuild `result` as a `TimeStamp` with `src`'s formatting metadata."""
    out = TimeStamp(
        result.year, result.month, result.day, result.hour, result.minute,
        result.second, result.microsecond, result.tzinfo, fold=result.fold,
        lexeme=src._lexeme if lexeme else None,
    )
    out._yaml = yaml if yaml is not None else dict(src._yaml)
    return out


def _unpickle(args: tuple[Any, ...], lexeme: str | None, yaml: dict[str, Any]) -> TimeStamp:
    ts = TimeStamp(*args, lexeme=lexeme)
    ts._yaml = yaml
    return ts


from_lexeme = TimeStamp.from_lexeme


if __name__ == '__main__':
    for text in (
        '2002-12-14',
        '2001-12-14t21:59:43.10-05:00',
        '2001-12-14 21:59:43.10 -5',
        '2001-12-15T02:59:43.1Z',
        '2001-12-15 2:59:43.10',
    ):
        ts = from_lexeme(text)
        assert ts.lexeme() == text, (text, ts.lexeme())
        assert str(ts) == text
        assert copy.deepcopy(ts).lexeme() == text
    a = from_lexeme('2001-12-14t21:59:43.10-05:00')
    b = from_lexeme('2001-12-15T02:59:43.10Z')
    assert a == b, (a.isoformat(), b.isoformat())
    assert from_lexeme('2001-12-15T02:59:43.9999995Z').second == 44
    print('timestamp ok')
