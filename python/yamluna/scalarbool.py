"""YAML boolean scalars.

``bool`` cannot be subclassed, so — as in ruamel — the round-trip boolean subclasses ``int``,
which is ``bool``'s own base.  It compares equal to ``True``/``False`` and is usable in an
``if``, but ``x is True`` is false; test with ``==`` or ``bool(x)``.

The point of the class is the spelling: YAML 1.1 has eleven ways to write true and a document
that said ``on`` must not come back as ``true``, so the source lexeme rides along.
"""

from __future__ import annotations

from typing import Any, Self

from yamluna.scalarstring import _Anchored

__all__ = ['ScalarBoolean', 'from_lexeme']

#: YAML 1.1's boolean spellings.  1.2's core schema is the ``true``/``false`` subset; which
#: lexemes actually resolve to a boolean is the resolver's decision, not this module's — here
#: they are only recognised so an existing document survives a round trip.
_TRUE = frozenset('y Y yes Yes YES true True TRUE on On ON'.split())
_FALSE = frozenset('n N no No NO false False FALSE off Off OFF'.split())


class ScalarBoolean(_Anchored, int):
    """A boolean that remembers whether the source said ``true``, ``True``, ``yes`` or ``on``."""

    def __new__(
        cls,
        value: Any = False,
        *,
        lexeme: str | None = None,
        anchor: str | None = None,
    ) -> Self:
        self = int.__new__(cls, bool(value))
        self._lexeme = lexeme
        if anchor is not None:
            self.yaml_set_anchor(anchor, always_dump=True)
        return self

    @classmethod
    def from_lexeme(cls, text: str) -> Self:
        """Parse a boolean lexeme.  ``.lexeme()`` returns `text` byte-for-byte."""
        if text in _TRUE:
            return cls(True, lexeme=text)
        if text in _FALSE:
            return cls(False, lexeme=text)
        raise ValueError(f'not a boolean lexeme: {text!r}')

    def lexeme(self) -> str:
        """The source form verbatim, or ``true``/``false``."""
        if self._lexeme is not None:
            return self._lexeme
        return 'true' if self else 'false'

    def __repr__(self) -> str:
        return f'ScalarBoolean({self.lexeme()})'


from_lexeme = ScalarBoolean.from_lexeme


if __name__ == '__main__':
    for lexeme in ('true', 'True', 'TRUE', 'yes', 'on', 'y', 'false', 'FALSE', 'no', 'off'):
        got = from_lexeme(lexeme)
        assert got.lexeme() == lexeme, (lexeme, got.lexeme())
        assert bool(got) == (lexeme in _TRUE), lexeme
        assert got == (lexeme in _TRUE), lexeme
    assert ScalarBoolean(True).lexeme() == 'true'
    print('scalarbool ok')
