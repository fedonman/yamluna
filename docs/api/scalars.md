# Scalar types

Every scalar a load produces keeps its source lexeme: the characters exactly as the author
wrote them, quotes and block header included. A scalar you did not change is re-emitted from
that text, so its spelling survives whatever a re-formatter would have done to it:

```pycon
>>> from yamluna import YAML
>>> yaml = YAML()
>>> data = yaml.load('a: 1_000.5\nb: +12\nc: 0X1F\nd: 2001-12-14t21:59:43.10-05:00\n')
>>> data['a'], data['c']
(ScalarFloat(1_000.5), HexInt(0X1F))
>>> data['a'] + 0.5, data['c'] + 1
(1001.0, 32)
>>> print(yaml.dump(data), end='')
a: 1_000.5
b: +12
c: 0X1F
d: 2001-12-14t21:59:43.10-05:00
```

Assign a value of your own and the lexeme no longer applies, so the emitter spells it in the
style the class names. That is the other half of what these types are for: constructing one
is how you choose the style of a value you are writing. [Scalar styles and
types](../guide/scalars.md) works through the choices.

## Strings

One `str` subclass per YAML scalar style. The value itself is always the cooked one, with
escapes resolved and block scalars folded, so these compare and concatenate like any other
string. `PreservedScalarString` is ruamel's older name for `LiteralScalarString` and is the
same class, so `isinstance(s, LiteralScalarString)` catches both.

::: yamluna.ScalarString

::: yamluna.LiteralScalarString

::: yamluna.FoldedScalarString

::: yamluna.SingleQuotedScalarString

::: yamluna.DoubleQuotedScalarString

::: yamluna.PlainScalarString

::: yamluna.PreservedScalarString

## String helpers

::: yamluna.preserve_literal

::: yamluna.walk_tree

## Numbers, booleans and timestamps

`ScalarInt` and its three base subclasses are `int`s, `ScalarFloat` is a `float`,
`ScalarBoolean` is an `int` (Python has no other way to subclass `bool`), and `TimeStamp` is a
`datetime`. Arithmetic on any of them gives you a plain builtin back, except for the
in-place operators on `ScalarInt`, which keep the formatting.

::: yamluna.ScalarInt

::: yamluna.HexInt

::: yamluna.OctalInt

::: yamluna.BinaryInt

::: yamluna.ScalarFloat

::: yamluna.ScalarBoolean

::: yamluna.TimeStamp
