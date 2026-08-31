"""Round-trip acceptance tests for the Python scalar types (DESIGN.md §4.1).

The contract under test: build the type from a raw lexeme, ask for the lexeme back, get the
original bytes.  Numbers are where round-trip libraries quietly lose data, so they get the
widest coverage.
"""

from __future__ import annotations

import copy
import datetime
import math
import pickle

import pytest
from yamluna import scalarbool, scalarfloat, scalarint, scalarstring
from yamluna.scalarbool import ScalarBoolean
from yamluna.scalarfloat import ScalarFloat
from yamluna.scalarint import BinaryInt, HexInt, OctalInt, ScalarInt
from yamluna.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    ScalarString,
    SingleQuotedScalarString,
    preserve_literal,
    walk_tree,
)
from yamluna.timestamp import TimeStamp

# --------------------------------------------------------------------------- integers

INT_LEXEMES = {
    '0': 0,
    '5': 5,
    '+5': 5,
    '-5': -5,
    '007': 7,
    '1_000': 1000,
    '1_000_000': 1_000_000,
    '1_0_000': 10_000,
    '-1_000': -1000,
    '0x1F': 31,
    '0x1f': 31,
    '0xdeadBEEF': 0xDEADBEEF,
    '0x00ff': 255,
    '0x_1f': 31,
    '-0x10': -16,
    '+0x10': 16,
    '0o755': 0o755,
    '0o0644': 0o644,
    '0b1010': 0b1010,
    '0b1010_1010': 0b1010_1010,
    '9' * 40: int('9' * 40),
    '-' + '1234567890' * 4: -int('1234567890' * 4),
}


@pytest.mark.parametrize(('lexeme', 'value'), INT_LEXEMES.items(), ids=list(INT_LEXEMES))
def test_int_lexeme_round_trip(lexeme: str, value: int) -> None:
    got = scalarint.from_lexeme(lexeme)
    assert got.lexeme() == lexeme
    assert int(got) == value
    assert got == value


def test_int_subclass_dispatch() -> None:
    assert type(scalarint.from_lexeme('1_000')) is ScalarInt
    assert type(scalarint.from_lexeme('0x1f')) is HexInt
    assert type(scalarint.from_lexeme('0o755')) is OctalInt
    assert type(scalarint.from_lexeme('0b1010')) is BinaryInt
    assert isinstance(scalarint.from_lexeme('0x1f'), int)


def test_int_formats_without_a_lexeme() -> None:
    assert ScalarInt(1000).lexeme() == '1000'
    assert ScalarInt(5, sign='+').lexeme() == '+5'
    assert ScalarInt(1_000_000, underscore=[3, False, False]).lexeme() == '1_000_000'
    assert ScalarInt(7, width=3).lexeme() == '007'
    assert HexInt(31).lexeme() == '0x1f'
    assert HexInt(31, caps=True).lexeme() == '0x1F'
    assert HexInt(255, width=4).lexeme() == '0x00ff'
    assert OctalInt(0o755).lexeme() == '0o755'
    assert BinaryInt(0b1010).lexeme() == '0b1010'
    assert ScalarInt(-16).lexeme() == '-16'
    assert HexInt(-16).lexeme() == '-0x10'


def test_int_caps_is_remembered() -> None:
    assert scalarint.from_lexeme('0x1F').caps is True
    assert scalarint.from_lexeme('0x1f').caps is False
    assert scalarint.from_lexeme('0x10').caps is False


def test_int_in_place_arithmetic_keeps_the_format_but_drops_the_lexeme() -> None:
    x = scalarint.from_lexeme('0x0f')
    x += 1
    assert isinstance(x, HexInt)
    assert x.lexeme() == '0x10'
    y = scalarint.from_lexeme('1_000')
    y *= 2
    assert y.lexeme() == '2_000'


def test_int_rejects_non_integers() -> None:
    with pytest.raises(ValueError, match='not an integer lexeme'):
        scalarint.from_lexeme('1.5')


# --------------------------------------------------------------------------- floats

FLOAT_LEXEMES = [
    '0.0',
    '1.0',
    '3.',
    '.5',
    '-.5',
    '+.5',
    '1e3',
    '1E3',
    '1.0e+3',
    '1.0E+3',
    '+1.5e-3',
    '-1.5e-3',
    '1_000.0',
    '1_000_000.000_1',
    '0.000_1',
    '685.230_15e+03',
    '12e03',
    '-0.0',
    '00.5',
]


@pytest.mark.parametrize('lexeme', FLOAT_LEXEMES)
def test_float_lexeme_round_trip(lexeme: str) -> None:
    got = scalarfloat.from_lexeme(lexeme)
    assert got.lexeme() == lexeme
    assert got == float(lexeme.replace('_', ''))
    assert isinstance(got, float)


@pytest.mark.parametrize(('lexeme', 'value'), [
    ('.inf', math.inf),
    ('.Inf', math.inf),
    ('.INF', math.inf),
    ('inf', math.inf),
    ('-.inf', -math.inf),
    ('+.inf', math.inf),
])
def test_float_infinities_round_trip(lexeme: str, value: float) -> None:
    got = scalarfloat.from_lexeme(lexeme)
    assert got.lexeme() == lexeme
    assert got == value


@pytest.mark.parametrize('lexeme', ['.nan', '.NaN', '.NAN', 'nan'])
def test_float_nan_round_trips(lexeme: str) -> None:
    got = scalarfloat.from_lexeme(lexeme)
    assert got.lexeme() == lexeme
    assert math.isnan(got)


def test_float_layout_fields_match_ruamel() -> None:
    f = scalarfloat.from_lexeme('1.0e+3')
    assert f._exp == 'e'
    assert f._prec == 1
    assert f._e_sign is True
    assert f._e_width == 2
    assert f._m_sign is False
    half = scalarfloat.from_lexeme('.5')
    assert half._prec == 0
    assert half._width == 2
    assert scalarfloat.from_lexeme('-1.5e-3')._m_sign == '-'
    assert scalarfloat.from_lexeme('00.5')._m_lead0 == 2


def test_float_formats_without_a_lexeme() -> None:
    assert ScalarFloat(1.5).lexeme() == '1.5'
    assert ScalarFloat(math.inf).lexeme() == '.inf'
    assert ScalarFloat(-math.inf).lexeme() == '-.inf'
    assert ScalarFloat(math.nan).lexeme() == '.nan'


def test_float_rejects_non_floats() -> None:
    with pytest.raises(ValueError, match='not a float lexeme'):
        scalarfloat.from_lexeme('abc')


# --------------------------------------------------------------------------- booleans

BOOL_LEXEMES = {
    'true': True, 'True': True, 'TRUE': True, 'yes': True, 'Yes': True, 'YES': True,
    'on': True, 'On': True, 'ON': True, 'y': True, 'Y': True,
    'false': False, 'False': False, 'FALSE': False, 'no': False, 'NO': False,
    'off': False, 'Off': False, 'OFF': False, 'n': False, 'N': False,
}


@pytest.mark.parametrize(('lexeme', 'value'), BOOL_LEXEMES.items(), ids=list(BOOL_LEXEMES))
def test_bool_lexeme_round_trip(lexeme: str, value: bool) -> None:
    got = scalarbool.from_lexeme(lexeme)
    assert got.lexeme() == lexeme
    assert bool(got) is value
    assert got == value
    assert (not got) is (not value)


def test_bool_without_a_lexeme() -> None:
    assert ScalarBoolean(True).lexeme() == 'true'
    assert ScalarBoolean(False).lexeme() == 'false'
    assert ScalarBoolean(3) == 1  # normalised, like bool()


def test_bool_rejects_non_booleans() -> None:
    with pytest.raises(ValueError, match='not a boolean lexeme'):
        scalarbool.from_lexeme('maybe')


# --------------------------------------------------------------------------- timestamps

TIMESTAMP_LEXEMES = [
    '2002-12-14',
    '2001-12-14t21:59:43.10-05:00',
    '2001-12-14 21:59:43.10 -5',
    '2001-12-15T02:59:43.10Z',
    '2001-12-15 2:59:43.10',
    '2001-12-15T02:59:43.1Z',
    '2011-01-24 03:29:00',
    '2001-12-14T21:59:43-0500',
]


@pytest.mark.parametrize('lexeme', TIMESTAMP_LEXEMES)
def test_timestamp_lexeme_round_trip(lexeme: str) -> None:
    got = TimeStamp.from_lexeme(lexeme)
    assert got.lexeme() == lexeme
    assert str(got) == lexeme
    assert isinstance(got, datetime.datetime)
    assert isinstance(got, datetime.date)


def test_timestamp_values() -> None:
    ts = TimeStamp.from_lexeme('2001-12-14t21:59:43.10-05:00')
    assert (ts.year, ts.month, ts.day) == (2001, 12, 14)
    assert (ts.hour, ts.minute, ts.second, ts.microsecond) == (21, 59, 43, 100000)
    assert ts.utcoffset() == datetime.timedelta(hours=-5)
    assert ts == TimeStamp.from_lexeme('2001-12-15T02:59:43.10Z')
    assert TimeStamp.from_lexeme('2002-12-14')._yaml['date_only'] is True
    assert TimeStamp.from_lexeme('2001-12-15 2:59:43.10')._yaml['t'] is False
    assert TimeStamp.from_lexeme('2001-12-15T2:59:43.10')._yaml['t'] is True


def test_timestamp_fraction_rounds_and_carries() -> None:
    assert TimeStamp.from_lexeme('2001-12-15T02:59:43.1234565Z').microsecond == 123457
    carried = TimeStamp.from_lexeme('2001-12-15T02:59:43.9999995Z')
    assert (carried.second, carried.microsecond) == (44, 0)
    assert carried.lexeme() == '2001-12-15T02:59:43.9999995Z'


def test_timestamp_without_a_lexeme() -> None:
    assert TimeStamp(2001, 12, 14, 21, 59, 43).lexeme() == '2001-12-14 21:59:43'
    replaced = TimeStamp.from_lexeme('2001-12-14 21:59:43').replace(year=2002)
    assert isinstance(replaced, TimeStamp)
    assert replaced.lexeme() == '2002-12-14 21:59:43'  # edited: the lexeme is dropped


def test_timestamp_rejects_non_timestamps() -> None:
    with pytest.raises(ValueError, match='not a timestamp lexeme'):
        TimeStamp.from_lexeme('yesterday')


# --------------------------------------------------------------------------- strings

def test_string_styles_round_trip() -> None:
    cases = [
        (PlainScalarString, 'abc', 'abc'),
        (SingleQuotedScalarString, "it's", "'it''s'"),
        (DoubleQuotedScalarString, 'a\nb', r'"a\nb"'),
        (LiteralScalarString, 'a\nb\n', '|\n  a\n  b\n'),
        (FoldedScalarString, 'a b\n', '>\n  a\n  b\n'),
    ]
    for cls, value, raw in cases:
        s = cls(value, lexeme=raw)
        assert s == value
        assert s.lexeme() == raw
        assert isinstance(s, str)
        assert isinstance(s, ScalarString)


def test_string_from_lexeme_picks_the_style() -> None:
    assert type(scalarstring.from_lexeme('abc')) is PlainScalarString
    assert type(scalarstring.from_lexeme("'a'")) is SingleQuotedScalarString
    assert type(scalarstring.from_lexeme('"a"', 'a')) is DoubleQuotedScalarString
    assert type(scalarstring.from_lexeme('|\n  a\n', 'a\n')) is LiteralScalarString
    assert type(scalarstring.from_lexeme('>\n  a\n', 'a\n')) is FoldedScalarString
    assert scalarstring.from_lexeme("'it''s'") == "it's"
    assert scalarstring.from_lexeme("'it''s'").lexeme() == "'it''s'"
    with pytest.raises(ValueError, match='cooked value required'):
        scalarstring.from_lexeme('"a\\nb"')


def test_string_style_indicators() -> None:
    assert PlainScalarString.style == ''
    assert SingleQuotedScalarString.style == "'"
    assert DoubleQuotedScalarString.style == '"'
    assert LiteralScalarString.style == '|'
    assert FoldedScalarString.style == '>'


def test_string_edit_keeps_the_class_and_drops_the_lexeme() -> None:
    s = DoubleQuotedScalarString('a b', lexeme='"a b"')
    out = s.replace(' ', '-')
    assert isinstance(out, DoubleQuotedScalarString)
    assert out == 'a-b'
    assert out.lexeme() is None


def test_string_extras() -> None:
    folded = FoldedScalarString('a b\n')
    assert folded.fold_pos is None
    folded.fold_pos = [1]
    assert folded.fold_pos == [1]
    literal = LiteralScalarString('a\n')
    assert literal.comment is None
    literal.comment = '# hi'
    assert literal.comment == '# hi'
    assert PlainScalarString('x').lc is None


def test_preserve_literal_and_walk_tree() -> None:
    assert isinstance(preserve_literal('a\r\nb'), LiteralScalarString)
    assert preserve_literal('a\r\nb') == 'a\nb'

    data = {'a': 'one\ntwo', 'b': ['x\ny', 'z'], 'c': {'d': 'p\nq'}}
    walk_tree(data)
    assert isinstance(data['a'], LiteralScalarString)
    assert isinstance(data['b'][0], LiteralScalarString)
    assert not isinstance(data['b'][1], LiteralScalarString)
    assert isinstance(data['c']['d'], LiteralScalarString)

    other = {'a': 'x:y'}
    walk_tree(other, map={':': SingleQuotedScalarString})
    assert isinstance(other['a'], SingleQuotedScalarString)


# --------------------------------------------------------------------------- shared

ANCHORED = [
    lambda: scalarstring.from_lexeme('abc'),
    lambda: scalarint.from_lexeme('0x1f'),
    lambda: scalarfloat.from_lexeme('1.5'),
    lambda: ScalarBoolean.from_lexeme('yes'),
    lambda: TimeStamp.from_lexeme('2002-12-14'),
]


@pytest.mark.parametrize('make', ANCHORED, ids=['str', 'int', 'float', 'bool', 'timestamp'])
def test_every_scalar_carries_anchor_lc_and_comment(make) -> None:
    obj = make()
    assert obj.yaml_anchor() is None
    obj.yaml_set_anchor('base')
    assert obj.anchor.value == 'base'
    assert obj.yaml_anchor() is None  # not always_dump
    assert obj.yaml_anchor(any=True).value == 'base'
    obj.yaml_set_anchor('base', always_dump=True)
    assert obj.yaml_anchor().value == 'base'

    assert obj.lc is None
    obj.lc = (3, 7)
    assert obj.lc == (3, 7)
    assert obj.comment is None
    obj.comment = '# x'
    assert obj.comment == '# x'


@pytest.mark.parametrize('make', ANCHORED, ids=['str', 'int', 'float', 'bool', 'timestamp'])
def test_copy_and_pickle_keep_the_lexeme(make) -> None:
    obj = make()
    assert copy.deepcopy(obj).lexeme() == obj.lexeme()
    assert pickle.loads(pickle.dumps(obj)).lexeme() == obj.lexeme()
