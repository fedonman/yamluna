"""YAML boolean scalars.

`bool` cannot be subclassed, so the round-trip boolean subclasses `int`, which is `bool`'s
own base. ruamel does the same. It compares equal to `True` and `False` and works in an `if`,
but `x is True` is false, so test with `==` or `bool(x)`.

The point of the class is the spelling. YAML 1.1 has eleven ways to write true, and a
document written with `on` must not come back as `true`, so the source lexeme rides along.
Loading gives you one of these only when the spelling differs from what a builtin would
write: `true` and `false` come back as a plain `bool`, while `True`, `TRUE`, `yes` and `on`
come back as `ScalarBoolean`.

```python
data['debug'] = ScalarBoolean(True, lexeme='yes')
```

```yaml
debug: yes
```
"""

from __future__ import annotations

from typing import Any, Self

from yamluna.scalarstring import _Anchored

__all__ = ['ScalarBoolean', 'from_lexeme']

# YAML 1.1's boolean spellings. 1.2's core schema is the `true`/`false` subset. Which of
# these actually resolves to a boolean is the resolver's decision, not this module's; here
# they are only recognised so an existing document survives a round trip.
_TRUE = frozenset(['y', 'Y', 'yes', 'Yes', 'YES', 'true', 'True', 'TRUE', 'on', 'On', 'ON'])
_FALSE = frozenset(['n', 'N', 'no', 'No', 'NO', 'false', 'False', 'FALSE', 'off', 'Off', 'OFF'])


class ScalarBoolean(_Anchored, int):
    """A boolean that remembers whether the source said `true`, `True`, `yes` or `on`.

    Args:
        value: Anything with a truth value. It is stored as `bool(value)`.
        lexeme: The source text. When present `lexeme()` returns it unchanged, which is what
            keeps the original spelling in the dumped document.
        anchor: An anchor name to attach, marked to be written even when nothing aliases
            this scalar.

    Example:
        ```python
        data['debug'] = ScalarBoolean(True, lexeme='on')
        ```

        ```yaml
        debug: on
        ```
    """

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
        """Parse a boolean lexeme.

        Args:
            text: One of YAML 1.1's boolean spellings: `y`, `yes`, `true`, `on`, `n`, `no`,
                `false`, `off`, in lower, title or upper case.

        Returns:
            A `ScalarBoolean` with the matching truth value whose `lexeme()` returns `text`
            byte for byte.

        Raises:
            ValueError: `text` is not one of the recognised spellings.
        """
        if text in _TRUE:
            return cls(True, lexeme=text)
        if text in _FALSE:
            return cls(False, lexeme=text)
        raise ValueError(f'not a boolean lexeme: {text!r}')

    def lexeme(self) -> str:
        """Return the source text verbatim, or `true` / `false`.

        Returns:
            The spelling the source used, when this boolean was loaded from a document.
            Otherwise `true` or `false`, whichever matches the value.
        """
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
