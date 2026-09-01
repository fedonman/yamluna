"""One helper, for reaching through an API that answers `X | None`.

`TagRegistry.resolve`, `TagRegistry.registration_for` and `MarkedYAMLError.problem_mark`
all answer `None` for an input they do not recognise, and a test that knows the input is
recognised still has to say so. Writing `reg.resolve('!Circuit').cls` says it by crashing
with `AttributeError: 'NoneType' object has no attribute 'cls'`, which names neither the
call that returned `None` nor the test's actual expectation.

```python
from _expect import found

assert found(reg.resolve('!Circuit')).cls is Circuit
```
"""

from __future__ import annotations

from typing import TypeVar

__all__ = ['found']

_T = TypeVar('_T')


def found(value: _T | None) -> _T:
    """Return `value`, failing the test when it is `None`.

    Args:
        value: The answer from a call that returns `None` for an input it does not
            recognise.

    Returns:
        `value`, with the `None` ruled out for both the reader and the type checker.

    Raises:
        AssertionError: `value` is `None`.

    """
    assert value is not None, 'expected a value, got None'
    return value
