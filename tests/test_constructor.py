"""Tests for records -> Python tree (DESIGN.md 3 -> 4.1).

Runs with no Rust extension: every input is a hand-built record tree from
``tests/_records.py``::

    PYTHONPATH=python .venv/bin/pytest tests/test_constructor.py
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from _records import (
    STYLE_DOUBLE,
    STYLE_FLOW,
    STYLE_FOLDED,
    STYLE_LITERAL,
    STYLE_SINGLE,
    alias,
    blank,
    comment,
    doc,
    docs,
    mapping,
    scalar,
    seq,
)
from yamluna.comments import (
    C_KEY_EOL,
    C_KEY_PRE,
    C_VALUE_EOL,
    C_VALUE_POST,
    CommentedKeyMap,
    CommentedKeySeq,
    CommentedMap,
    CommentedSeq,
    CommentedSet,
    TaggedScalar,
)
from yamluna.constructor import UNRESOLVED, construct, construct_all, resolve
from yamluna.error import (
    ComposerError,
    ConstructorError,
    DuplicateKeyError,
    DuplicateKeyFutureWarning,
)
from yamluna.registry import TagRegistry
from yamluna.scalarbool import ScalarBoolean
from yamluna.scalarfloat import ScalarFloat
from yamluna.scalarint import BinaryInt, HexInt, OctalInt, ScalarInt
from yamluna.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    SingleQuotedScalarString,
)
from yamluna.timestamp import TimeStamp


def tree(root: Any, **options: Any) -> Any:
    """The Python tree for a single-document record built by ``_records``."""
    return construct(doc(root), **options)


def values(tokens: Any) -> list[str]:
    return [t.value for t in tokens or ()]


# --------------------------------------------------------------------- core-schema scalars


@pytest.mark.parametrize(
    ('lexeme', 'kind', 'expected'),
    [
        # null
        ('', type(None), None),
        ('~', type(None), None),
        ('null', type(None), None),
        ('Null', type(None), None),
        ('NULL', type(None), None),
        # bool: only the canonical spellings may drop to a builtin
        ('true', bool, True),
        ('false', bool, False),
        # int: a bare int only where str(int) reproduces the lexeme
        ('0', int, 0),
        ('12345', int, 12345),
        ('-42', int, -42),
        # float always keeps its layout
        ('3.14159', ScalarFloat, 3.14159),
        ('6.02e23', ScalarFloat, 6.02e23),
        ('-1.6E-19', ScalarFloat, -1.6e-19),
        ('.5', ScalarFloat, 0.5),
        ('5.', ScalarFloat, 5.0),
        ('0.0', ScalarFloat, 0.0),
        ('1_000.000_1', ScalarFloat, 1000.0001),
    ],
)
def test_resolve_plain_values(lexeme: str, kind: type, expected: Any) -> None:
    got = resolve(lexeme)
    assert type(got) is kind
    assert got == expected


@pytest.mark.parametrize(
    ('lexeme', 'kind'),
    [
        # DIVERGENCES B8: ruamel drops the '+'
        ('+7', ScalarInt),
        ('1_000_000', ScalarInt),
        ('017', ScalarInt),
        ('0755', ScalarInt),
        ('0x1F_FF', HexInt),
        ('0o755', OctalInt),
        ('0b1010_0101', BinaryInt),
        # DIVERGENCES D2: ruamel refuses a capital X/O/B
        ('0XABCDEF', HexInt),
        ('0O17', OctalInt),
        ('0B101', BinaryInt),
        ('-0x1F', HexInt),
        # DIVERGENCES B7: the separator survives, and nothing zero-pads
        ('1_000.5', ScalarFloat),
        ('+2.5', ScalarFloat),
        ('3.14000', ScalarFloat),
        ('1.0e+3', ScalarFloat),
        # booleans that a bare bool cannot spell
        ('True', ScalarBoolean),
        ('TRUE', ScalarBoolean),
        ('False', ScalarBoolean),
        # timestamps
        ('2002-12-14', TimeStamp),
        ('2002-1-4', TimeStamp),
        ('2001-12-14t21:59:43.10-05:00', TimeStamp),
        ('2001-12-14 21:59:43.10 -5', TimeStamp),
    ],
)
def test_resolve_keeps_the_source_lexeme(lexeme: str, kind: type) -> None:
    """The lexeme is the truth: never a reformat of the cooked value."""
    got = resolve(lexeme)
    assert type(got) is kind
    assert got.lexeme() == lexeme


def test_resolve_specific_values() -> None:
    assert resolve('0x1F_FF') == 0x1FFF
    assert resolve('0755') == 755  # decimal under 1.2, and '0755' still round-trips
    assert resolve('+7') == 7
    assert resolve('1_000.5') == 1000.5
    assert resolve('TRUE') is not True and resolve('TRUE') == True  # noqa: E712
    assert bool(resolve('False')) is False


@pytest.mark.parametrize('lexeme', ['.inf', '+.Inf', '-.INF'])
def test_resolve_infinities(lexeme: str) -> None:
    got = resolve(lexeme)
    assert math.isinf(got) and got.lexeme() == lexeme


@pytest.mark.parametrize('lexeme', ['.nan', '.NaN', '.NAN'])
def test_resolve_nan(lexeme: str) -> None:
    got = resolve(lexeme)
    assert math.isnan(got) and got.lexeme() == lexeme


@pytest.mark.parametrize(
    'lexeme',
    ['hello', '1.2.3', '190:20:30', '_100', '0x', 'yes', 'on', 'off', 'no', 'y', '0b1012'],
)
def test_resolve_leaves_strings_alone(lexeme: str) -> None:
    assert resolve(lexeme) is UNRESOLVED


@pytest.mark.parametrize(
    ('lexeme', 'expected'), [('yes', True), ('on', True), ('no', False), ('off', False)]
)
def test_yaml_11_boolean_spellings(lexeme: str, expected: bool) -> None:
    """`yes`/`on` are booleans only under an explicit %YAML 1.1, and keep their spelling."""
    got = resolve(lexeme, version=(1, 1))
    assert type(got) is ScalarBoolean
    assert bool(got) is expected and got.lexeme() == lexeme

    d = doc(mapping([('flag', lexeme)]), version=(1, 1))
    assert construct(d)['flag'].lexeme() == lexeme
    assert construct(doc(mapping([('flag', lexeme)])))['flag'] == lexeme


def test_scalars_through_a_document() -> None:
    d = doc(
        mapping(
            [
                ('i', '42'),
                ('plus', '+12'),
                ('hex', scalar('0x1f', raw='0x1f')),
                ('f', '1_000.5'),
                ('b', 'true'),
                ('B', 'True'),
                ('n', 'null'),
                ('empty', ''),
                ('when', '2002-12-14'),
            ]
        )
    )
    m = construct(d)
    assert m['i'] == 42 and type(m['i']) is int
    assert m['plus'].lexeme() == '+12'
    assert m['hex'].lexeme() == '0x1f' and m['hex'] == 31
    assert m['f'].lexeme() == '1_000.5'
    assert m['b'] is True
    assert m['B'].lexeme() == 'True'
    assert m['n'] is None and m['empty'] is None
    assert m['when'].lexeme() == '2002-12-14'


def test_raw_wins_over_the_cooked_value() -> None:
    """A loaded node resolves from ``raw``; ``value`` is only the cooked text."""
    node = scalar('1000.5', raw='1_000.5')
    assert tree(mapping([('x', node)]))['x'].lexeme() == '1_000.5'


# ------------------------------------------------------------------------- scalar strings


@pytest.mark.parametrize(
    ('style', 'cls'),
    [(STYLE_LITERAL, LiteralScalarString), (STYLE_FOLDED, FoldedScalarString)],
)
def test_block_scalars_always_keep_their_class(style: int, cls: type) -> None:
    for preserve in (False, True):
        got = tree(mapping([('x', scalar('a\nb\n', style))]), preserve_quotes=preserve)['x']
        assert type(got) is cls and got == 'a\nb\n'


def test_quoted_scalars_honour_preserve_quotes() -> None:
    src = mapping(
        [
            ('sq', scalar('single', STYLE_SINGLE, raw="'single'")),
            ('dq', scalar('double\n', STYLE_DOUBLE, raw='"double\\n"')),
            ('plain', 'hello'),
        ]
    )
    off = tree(src)
    assert type(off['sq']) is str and off['sq'] == 'single'
    assert type(off['dq']) is str and off['dq'] == 'double\n'

    on = tree(src, preserve_quotes=True)
    assert type(on['sq']) is SingleQuotedScalarString
    assert type(on['dq']) is DoubleQuotedScalarString
    assert on['sq'].lexeme() == "'single'" and on['dq'].lexeme() == '"double\\n"'
    assert type(on['plain']) is str  # a plain scalar stays a plain str either way


def test_a_quoted_scalar_is_never_resolved() -> None:
    m = tree(
        mapping(
            [
                ('a', scalar('0x1F', STYLE_DOUBLE)),
                ('b', scalar('true', STYLE_SINGLE)),
                ('c', scalar('2002-12-14', STYLE_DOUBLE)),
            ]
        )
    )
    assert m == {'a': '0x1F', 'b': 'true', 'c': '2002-12-14'}
    assert all(isinstance(v, str) for v in m.values())


def test_a_multi_line_plain_scalar_is_a_string() -> None:
    node = scalar('this is a multi-line plain key', raw='this is a\nmulti-line plain key')
    assert tree(mapping([('k', node)]))['k'] == 'this is a multi-line plain key'


# ----------------------------------------------------------------------- anchors & aliases


def test_alias_is_the_same_object() -> None:
    """``doc['use'] is doc['base']`` -- the identity user code depends on."""
    base = mapping([('x', '1')], anchor='b')
    m = tree(mapping([('base', base), ('use', alias('b')), ('again', alias('b'))]))
    assert m['use'] is m['base']
    assert m['again'] is m['base']
    assert m['base']['x'] == 1


def test_alias_to_a_sequence_and_to_a_scalar() -> None:
    m = tree(
        mapping(
            [
                ('items', seq(['one', 'two'], anchor='items')),
                ('copy', alias('items')),
                ('scalar', scalar('value', STYLE_DOUBLE, anchor='s')),
                ('ref', alias('s')),
            ]
        )
    )
    assert m['copy'] is m['items']
    assert m['ref'] is m['scalar']
    assert m['scalar'].anchor.value == 's'


def test_recursive_anchor_self_mapping() -> None:
    """DIVERGENCES B1 / corpus anchors-recursive: ruamel cannot load this at all."""
    node = mapping([('name', 'node'), ('next', alias('sm'))], anchor='sm')
    m = tree(mapping([('self_map', node)]))
    assert m['self_map']['next'] is m['self_map']
    assert m['self_map']['next']['next']['next'] is m['self_map']
    assert m['self_map']['name'] == 'node'


def test_recursive_anchor_self_sequence() -> None:
    node = seq(['item', alias('ss')], anchor='ss')
    m = tree(mapping([('self_seq', node)]))
    assert m['self_seq'][1] is m['self_seq']
    assert m['self_seq'][0] == 'item'
    assert len(m['self_seq']) == 2


def test_mutually_recursive_anchors() -> None:
    peer = mapping([('peer', alias('ma'))], anchor='mb')
    a = mapping([('peer', peer)], anchor='ma')
    m = tree(mapping([('a', a), ('b', alias('mb'))]))
    assert m['a']['peer']['peer'] is m['a']
    assert m['b'] is m['a']['peer']


def test_deeply_nested_self_reference() -> None:
    node = mapping(
        [('level1', mapping([('level2', mapping([('back', alias('ds'))]))]))], anchor='ds'
    )
    m = tree(mapping([('deep_self', node)]))
    assert m['deep_self']['level1']['level2']['back'] is m['deep_self']


def test_anchor_is_recorded_and_always_dumped() -> None:
    """A source anchor is source text, so it is emitted however often it is used (B1)."""
    m = tree(mapping([('unused', mapping([('y', '2')], anchor='unused'))]))
    assert m['unused'].anchor.value == 'unused'
    assert m['unused'].anchor.always_dump is True


def test_anchored_builtins_are_promoted_so_the_anchor_survives() -> None:
    m = tree(
        mapping(
            [
                ('s', scalar('hello', anchor='a1')),
                ('i', scalar('42', anchor='a2')),
                ('b', scalar('true', anchor='a3')),
            ]
        )
    )
    assert type(m['s']) is PlainScalarString and m['s'].anchor.value == 'a1'
    assert type(m['i']) is ScalarInt and m['i'] == 42 and m['i'].anchor.value == 'a2'
    assert type(m['b']) is ScalarBoolean and m['b'] == 1 and m['b'].anchor.value == 'a3'


def test_undefined_alias_raises() -> None:
    with pytest.raises(ComposerError, match='undefined alias'):
        tree(mapping([('a', alias('nope'))]))


def test_anchors_do_not_leak_between_documents() -> None:
    stream = docs(mapping([('a', scalar('1', anchor='x'))]), mapping([('b', alias('x'))]))
    with pytest.raises(ComposerError, match='undefined alias'):
        construct_all(stream)


# ---------------------------------------------------------------------------- merge keys


def _merge_docs() -> Any:
    base = mapping([('a', '1'), ('b', '2')], anchor='base')
    derived = mapping([('<<', alias('base')), ('b', 'overridden')], merge=[0])
    return doc(mapping([('base', base), ('derived', derived)]))


def test_merge_is_readable_through_normal_lookup() -> None:
    m = construct(_merge_docs())
    d = m['derived']
    assert d['a'] == 1  # comes from the merge
    assert d['b'] == 'overridden'  # the local entry wins
    assert 'a' in d and d.get('a') == 1


def test_merge_is_not_expanded_into_the_owned_entries() -> None:
    """A dump must re-emit ``<<: *base``, so the merged keys are not this node's own."""
    d = construct(_merge_docs())['derived']
    assert list(d.non_merged_items()) == [('b', 'overridden')]
    assert '<<' not in d


def test_merge_records_the_source_mapping_by_identity() -> None:
    m = construct(_merge_docs())
    assert m['derived'].merge[0] is m['base']
    assert m['derived'].merge.merge_pos == 0


def test_merge_of_a_sequence_of_aliases() -> None:
    base = mapping([('a', '1'), ('b', '2')], anchor='base')
    extra = mapping([('b', '20'), ('c', '3')], anchor='extra')
    merged = mapping(
        [('<<', seq([alias('base'), alias('extra')], STYLE_FLOW)), ('d', '4')], merge=[0]
    )
    m = tree(mapping([('base', base), ('extra', extra), ('merged', merged)]))
    assert m['merged']['a'] == 1
    assert m['merged']['b'] == 2  # first merge wins over the later one
    assert m['merged']['c'] == 3
    assert list(m['merged'].non_merged_items()) == [('d', 4)]
    assert len(m['merged'].merge) == 2


def test_merge_after_local_keys_keeps_merge_pos() -> None:
    base = mapping([('a', '1')], anchor='base')
    last = mapping([('x', '0'), ('<<', alias('base'))], merge=[2])
    m = tree(mapping([('base', base), ('merge_last', last)]))
    assert m['merge_last'] == {'x': 0, 'a': 1}
    assert list(m['merge_last'].non_merged_items()) == [('x', 0)]
    assert m['merge_last'].merge.merge_pos == 1


def test_a_local_key_always_beats_the_merged_one() -> None:
    base = mapping([('a', '1')], anchor='base')
    for children, merge in (
        ([('<<', alias('base')), ('a', '99')], [0]),
        ([('a', '99'), ('<<', alias('base'))], [2]),
    ):
        m = tree(mapping([('base', base), ('d', mapping(children, merge=merge))]))
        assert m['d']['a'] == 99


def test_duplicate_merge_keys_are_always_an_error() -> None:
    base = mapping([('a', '1')], anchor='b1')
    other = mapping([('c', '3')], anchor='b2')
    bad = mapping([('<<', alias('b1')), ('<<', alias('b2'))], merge=[0, 2])
    d = doc(mapping([('b1', base), ('b2', other), ('bad', bad)]))
    for allow in (False, True):
        with pytest.raises(DuplicateKeyError, match='duplicate merge key'):
            construct(d, allow_duplicate_keys=allow)


def test_merging_a_non_mapping_is_an_error() -> None:
    bad = mapping([('<<', seq(['1'], anchor='s'))], merge=[0])
    with pytest.raises(ConstructorError, match='expected a mapping for merging'):
        tree(bad)


# ------------------------------------------------------------------------- duplicate keys


def test_duplicate_key_raises_by_default() -> None:
    d = doc(mapping([('a', '1'), ('b', '2'), ('a', '3')]))
    with pytest.raises(DuplicateKeyError, match="duplicate key 'a'"):
        construct(d)


def test_duplicate_key_warns_and_the_last_one_wins() -> None:
    """DIVERGENCES D5: ruamel keeps the *first* value and says nothing."""
    d = doc(mapping([('a', '1'), ('b', '2'), ('a', '3')]))
    with pytest.warns(DuplicateKeyFutureWarning, match='last value wins'):
        m = construct(d, allow_duplicate_keys=True)
    assert m['a'] == 3
    assert list(m) == ['a', 'b']


def test_duplicate_key_error_names_both_positions() -> None:
    d = doc(
        mapping(
            [
                (scalar('a', line=0, col=0), scalar('1', line=0, col=3)),
                (scalar('a', line=4, col=2), scalar('3', line=4, col=5)),
            ]
        )
    )
    with pytest.raises(DuplicateKeyError) as excinfo:
        construct(d)
    message = str(excinfo.value)
    assert 'line 1, column 1' in message and 'line 5, column 3' in message


def test_keys_that_resolve_alike_are_duplicates() -> None:
    d = doc(mapping([(scalar('quoted', STYLE_DOUBLE), '1'), ('quoted', '2')]))
    with pytest.raises(DuplicateKeyError):
        construct(d)


# -------------------------------------------------------------------------------- tags


class Circuit:
    def __init__(self, qubits: int = 0) -> None:
        self.qubits = qubits


class Custom:
    def __init__(self, a: Any = None) -> None:
        self.a = a

    @classmethod
    def from_yaml(cls, constructor: Any, node: Any) -> Custom:
        return cls(a=node.value)


def test_registered_class_by_bare_tag() -> None:
    registry = TagRegistry()
    registry.register_class(Circuit)
    node = mapping([('qubits', '2')], tag=('!', 'Circuit', '!Circuit'))
    got = tree(mapping([('main', node)]), registry=registry)['main']
    assert type(got) is Circuit and got.qubits == 2


def test_registered_class_through_a_tag_directive() -> None:
    registry = TagRegistry()
    registry.register_class(Circuit, source='libx')
    d = doc(
        mapping([('main', mapping([('qubits', '2')], tag=('!', 'Circuit', 'tag:libx/Circuit')))]),
        tag_directives=[('!', 'tag:libx/')],
    )
    assert type(construct(d, registry=registry)['main']) is Circuit


def test_registered_class_uses_its_from_yaml_hook() -> None:
    registry = TagRegistry()
    registry.register_class(Custom)
    got = tree(
        mapping([('k', scalar('hi', tag=('!', 'Custom', '!Custom')))]), registry=registry
    )['k']
    assert type(got) is Custom and got.a == 'hi'


def test_a_tag_in_our_namespace_with_no_class_raises() -> None:
    registry = TagRegistry()
    registry.register_class(Circuit, source='libx')
    d = doc(
        mapping([('x', mapping([('a', '1')], tag=('!', 'Gate', 'tag:libx/Gate')))]),
        tag_directives=[('!', 'tag:libx/')],
    )
    with pytest.raises(ConstructorError, match='Gate'):
        construct(d, registry=registry)


def test_an_ambiguous_bare_tag_raises_and_names_every_candidate() -> None:
    """DESIGN 5.4.2: never guess -- the ruamel bug this library exists to not have."""
    registry = TagRegistry()
    for module in ('libx.circuits', 'liby.gates'):
        cls = type('Circuit', (), {})
        cls.__module__ = module
        registry.register_class(cls)
    node = mapping([('a', '1')], tag=('!', 'Circuit', '!Circuit'))
    with pytest.raises(ConstructorError) as excinfo:
        tree(mapping([('x', node)]), registry=registry)
    message = str(excinfo.value)
    assert 'libx.circuits.Circuit' in message
    assert 'liby.gates.Circuit' in message
    assert 'will not guess' in message


def test_unregistered_tags_round_trip_untouched() -> None:
    m = tree(
        mapping(
            [
                ('a', mapping([('x', '1')], tag=('!', 'Unknown', '!Unknown'))),
                ('b', scalar('scalar', tag=('!', 'Un2', '!Un2'))),
                ('c', seq(['1', '2'], STYLE_FLOW, tag=('!', 'Un3', '!Un3'))),
            ]
        )
    )
    assert type(m['a']) is CommentedMap and m['a'].tag.value == '!Unknown'
    assert type(m['b']) is TaggedScalar and m['b'].tag.value == '!Un2'
    assert m['b'].value == 'scalar'
    assert type(m['c']) is CommentedSeq and m['c'].tag.value == '!Un3'


def test_unregistered_tagged_scalar_keeps_its_style() -> None:
    m = tree(mapping([('b', scalar('x', STYLE_DOUBLE, tag=('!', 'T', '!T')))]))
    assert m['b'].style == '"'


@pytest.mark.parametrize(
    ('suffix', 'value', 'expected'),
    [
        ('null', '', None),
        ('bool', 'true', True),
        ('int', '42', 42),
        ('str', '123', '123'),
        ('binary', 'aGVsbG8=', b'hello'),
    ],
)
def test_standard_yaml_org_scalar_tags(suffix: str, value: str, expected: Any) -> None:
    """A `!!` tag forces the type instead of resolving it: `!!int "42"` is 42, not '42'."""
    tag = ('!!', suffix, f'tag:yaml.org,2002:{suffix}')
    got = tree(mapping([('x', scalar(value, STYLE_DOUBLE, tag=tag))]))['x']
    assert got == expected and type(got) is type(expected)


@pytest.mark.parametrize(
    ('suffix', 'value', 'cls'),
    [('float', '1.5', ScalarFloat), ('timestamp', '2002-12-14', TimeStamp)],
)
def test_standard_tags_that_keep_their_lexeme(suffix: str, value: str, cls: type) -> None:
    tag = ('!!', suffix, f'tag:yaml.org,2002:{suffix}')
    got = tree(mapping([('x', scalar(value, STYLE_DOUBLE, tag=tag))]))['x']
    assert type(got) is cls and got.lexeme() == value


def test_binary_from_a_block_scalar() -> None:
    payload = 'aGVsbG8g\nd29ybGQ=\n'
    tag = ('!!', 'binary', 'tag:yaml.org,2002:binary')
    assert tree(mapping([('p', scalar(payload, STYLE_LITERAL, tag=tag))]))['p'] == b'hello world'


def test_bad_binary_is_a_constructor_error() -> None:
    tag = ('!!', 'binary', 'tag:yaml.org,2002:binary')
    with pytest.raises(ConstructorError, match='base64'):
        tree(mapping([('p', scalar('!!!not base64!!!', STYLE_DOUBLE, tag=tag))]))


def test_a_yaml_set_becomes_a_commented_set() -> None:
    node = mapping([('a', ''), ('b', '')], tag=('!!', 'set', 'tag:yaml.org,2002:set'))
    got = tree(mapping([('s', node)]))['s']
    assert type(got) is CommentedSet and list(got) == ['a', 'b']


def test_the_non_specific_tag_resolves_by_content() -> None:
    m = tree(mapping([('x', scalar('plain', tag=('!', '', '!')))]))
    assert m['x'] == 'plain'


# ------------------------------------------------------------------------- complex keys


def test_sequence_key_stays_hashable() -> None:
    m = tree(mapping([(seq(['a', 'b'], STYLE_FLOW), 'sequence key')]))
    key = next(iter(m))
    assert type(key) is CommentedKeySeq
    assert hash(key) == hash(('a', 'b'))
    assert m[('a', 'b')] == 'sequence key'


def test_mapping_key_stays_hashable() -> None:
    m = tree(mapping([(mapping([('x', '1'), ('y', '2')], STYLE_FLOW), 'mapping key')]))
    key = next(iter(m))
    assert type(key) is CommentedKeyMap
    assert m[CommentedKeyMap({'x': 1, 'y': 2})] == 'mapping key'
    assert dict(key.items()) == {'x': 1, 'y': 2}


def test_nested_containers_inside_a_key_are_hashable_too() -> None:
    inner = seq(['b', 'c'], STYLE_FLOW)
    m = tree(mapping([(seq(['a', inner], STYLE_FLOW), 'v')]))
    key = next(iter(m))
    assert hash(key)  # would raise for a CommentedSeq
    assert type(key[1]) is CommentedKeySeq


def test_a_block_scalar_key_and_an_empty_value() -> None:
    m = tree(
        mapping(
            [
                (scalar('a literal\nkey\n', STYLE_LITERAL), 'block scalar key'),
                ('an explicit key with no value', ''),
            ]
        )
    )
    assert m['a literal\nkey\n'] == 'block scalar key'
    assert m['an explicit key with no value'] is None


# ------------------------------------------------------------------------------ trivia


def test_mapping_entry_trivia_lands_in_the_right_slots() -> None:
    key = scalar(
        'alpha',
        before=[comment('# about alpha')],
        eol=comment('# after the key', own_line=False, col=8),
    )
    value = scalar(
        '1',
        eol=comment('# eol alpha', own_line=False, col=11),
        after=[comment('# below the value', col=2)],
    )
    m = tree(mapping([(key, value)]))
    record = m.ca.items['alpha']
    assert values(record[C_KEY_PRE]) == ['# about alpha\n']
    assert record[C_KEY_EOL].value == '# after the key'
    assert record[C_KEY_EOL].column == 8
    assert record[C_VALUE_EOL].value == '# eol alpha'
    assert values(record[C_VALUE_POST]) == ['# below the value\n']
    assert record[C_VALUE_POST][0].column == 2


def test_blank_lines_are_first_class() -> None:
    """DIVERGENCES A7: ruamel smuggles them into another node's comment text."""
    key = scalar('b', before=[blank(3), comment('# about b')])
    m = tree(mapping([('a', '1'), (key, '2')]))
    tokens = m.ca.items['b'][C_KEY_PRE]
    assert [t.is_blank_line for t in tokens] == [True, True, True, False]
    assert len(tokens) == 4 and tokens[-1].value == '# about b\n'


def test_sequence_element_trivia_uses_the_element_slots() -> None:
    one = scalar('one', before=[comment('# about one')], eol=comment('# eol', own_line=False))
    s = tree(seq([one, 'two']))
    assert values(s.ca.items[0][C_KEY_PRE]) == ['# about one\n']
    assert s.ca.items[0][C_KEY_EOL].value == '# eol'
    assert 1 not in s.ca.items


def test_sequence_trivia_travels_with_its_element() -> None:
    """The whole point of the identity-keyed store (DIVERGENCES A2/A3)."""
    items = [scalar(t, before=[comment(f'# about {t}')]) for t in ('one', 'two', 'three')]
    s = tree(seq(items))
    s.insert(0, 'zero')
    assert 0 not in s.ca.items
    assert values(s.ca.items[1][C_KEY_PRE]) == ['# about one\n']
    del s[1]
    assert values(s.ca.items[1][C_KEY_PRE]) == ['# about two\n']


def test_collection_inner_and_after_trivia() -> None:
    inner = mapping([('a', '1')], inner=[comment('# inside')], after=[comment('# below')])
    m = tree(mapping([('outer', inner)]))
    assert values(m['outer'].ca.comment[1]) == ['# inside\n']
    assert values(m['outer'].ca.end) == ['# below\n']


def test_document_leading_and_trailing_trivia() -> None:
    root = mapping([('a', '1')], inner=[comment('# inner')], after=[comment('# after root')])
    d = doc(
        root,
        leading=[blank(2), comment('# lead')],
        trailing=[comment('# the very end')],
    )
    m = construct(d)
    assert values(m.ca.comment[1]) == ['\n', '\n', '# lead\n', '# inner\n']
    assert values(m.ca.end) == ['# after root\n', '# the very end\n']


def test_document_trailing_comment_survives(  # DIVERGENCES A9 / B3
) -> None:
    m = construct(doc(mapping([('a', '1')]), trailing=[comment('# after end marker')]))
    assert values(m.ca.end) == ['# after end marker\n']


def test_the_root_eol_comment() -> None:
    root = seq(['1'], STYLE_FLOW, eol=comment('# the whole doc', own_line=False, col=6))
    m = construct(doc(root))
    assert m.ca.comment[0].value == '# the whole doc'


def test_merge_entry_trivia_is_keyed_by_the_merge_key() -> None:
    base = mapping([('a', '1')], anchor='base')
    merged = mapping(
        [
            (
                '<<',
                alias('base', eol=comment('# a comment on the merge entry', own_line=False)),
            ),
            ('y', '8'),
        ],
        merge=[0],
    )
    m = tree(mapping([('base', base), ('m', merged)]))
    record = m['m'].ca.items['<<']
    assert record[C_VALUE_EOL].value == '# a comment on the merge entry'


# ---------------------------------------------------------------- .lc / .fa / .anchor / .tag


def test_line_col_of_containers_and_entries() -> None:
    key = scalar('alpha', line=3, col=2)
    value = scalar('1', line=3, col=9)
    m = tree(mapping([(key, value)], line=3, col=2))
    assert (m.lc.line, m.lc.col) == (3, 2)
    assert m.lc.key('alpha') == (3, 2)
    assert m.lc.value('alpha') == (3, 9)


def test_line_col_of_sequence_items() -> None:
    s = tree(seq([scalar('one', line=1, col=2), scalar('two', line=2, col=2)], line=1, col=0))
    assert s.lc.item(0) == (1, 2)
    assert s.lc.item(1) == (2, 2)


def test_flow_style_is_recorded_per_node() -> None:
    m = tree(mapping([('flow', seq(['1'], STYLE_FLOW)), ('block', seq(['2']))]))
    assert m['flow'].fa.flow_style() is True
    assert m['block'].fa.flow_style() is False
    assert m.fa.flow_style() is False


def test_tag_is_kept_on_containers() -> None:
    m = tree(mapping([('x', '1')], tag=('!', 'Thing', '!Thing')))
    assert m.tag.handle == '!' and m.tag.suffix == 'Thing'
    assert m.tag.value == '!Thing'


# ------------------------------------------------------------------------- documents


def test_empty_document_is_none() -> None:
    assert construct(doc()) is None
    assert construct_all(docs()) == []


def test_multi_document_stream() -> None:
    assert construct_all(docs(mapping([('a', '1')]), seq(['x']))) == [{'a': 1}, ['x']]


def test_scalar_document_root() -> None:
    assert construct(doc('hello')) == 'hello'
    assert construct(doc('42')) == 42


def test_construct_returns_the_builtin_container_types() -> None:
    m = construct(doc(mapping([('a', seq(['1'])), ('b', mapping([('c', '2')]))])))
    assert isinstance(m, dict) and type(m) is CommentedMap
    assert isinstance(m['a'], list) and type(m['a']) is CommentedSeq
    assert m == {'a': [1], 'b': {'c': 2}}
