"""Tests for the container object model (DESIGN.md 4.1 / 2.1).

Runs with nothing but ``python/yamluna/comments.py`` importable::

    PYTHONPATH=python .venv/bin/pytest tests/test_comments.py
"""

from __future__ import annotations

import copy
import json
import pickle
from collections.abc import Mapping
from typing import Any

import pytest

from yamluna.comments import (
    C_ELEM_EOL,
    C_KEY_PRE,
    C_VALUE_EOL,
    C_VALUE_POST,
    Anchor,
    Comment,
    CommentedKeyMap,
    CommentedKeySeq,
    CommentedMap,
    CommentedSeq,
    CommentedSet,
    CommentMark,
    CommentToken,
    Format,
    LineCol,
    Tag,
    TaggedScalar,
)


# --------------------------------------------------------------------------- helpers


def seq(*values: Any, comments: dict[int, str] | None = None) -> CommentedSeq:
    s = CommentedSeq(values)
    for idx, text in (comments or {}).items():
        s.yaml_add_eol_comment(text, idx)
    return s


def cmap(comments: dict[str, str] | None = None, **items: Any) -> CommentedMap:
    m = CommentedMap(items)
    for key, text in (comments or {}).items():
        m.yaml_add_eol_comment(text, key)
    return m


def eol(node: Any, key: Any) -> str | None:
    """The end-of-line comment text of one entry, or None."""
    record = node.ca.items.get(key)
    if record is None:
        return None
    token = record[node._ca_eol_slot]
    return None if token is None else token.value


def eols(node: Any) -> dict[Any, str | None]:
    return {k: eol(node, k) for k in node._ca_order() if eol(node, k) is not None}


# --------------------------------------------------------------------------- builtins


def test_containers_subclass_the_builtins() -> None:
    assert isinstance(CommentedMap(a=1), dict)
    assert isinstance(CommentedSeq([1]), list)
    assert isinstance(CommentedSet(['a']), set)
    assert isinstance(CommentedKeySeq(('a', 1)), tuple)
    assert isinstance(CommentedKeyMap({'a': 1}), tuple)
    assert isinstance(CommentedKeyMap({'a': 1}), Mapping)
    assert isinstance(TaggedScalar('x'), str)


def test_equality_with_plain_builtins() -> None:
    assert CommentedMap(a=1) == {'a': 1}
    assert {'a': 1} == CommentedMap(a=1)
    assert CommentedSeq([1, 2]) == [1, 2]
    assert CommentedSet(['a']) == {'a'}
    assert CommentedKeySeq(('a', 1)) == ('a', 1)
    assert CommentedKeyMap({'a': 1}) == {'a': 1}
    assert TaggedScalar('x') == 'x'


def test_json_dumps() -> None:
    doc = cmap(a=1, b=seq(1, 2), c=TaggedScalar('t'))
    doc['a'] = 1
    assert json.loads(json.dumps(doc)) == {'a': 1, 'b': [1, 2], 'c': 't'}


def test_arbitrary_attributes() -> None:
    for node in (CommentedMap(), CommentedSeq(), CommentedSet(), TaggedScalar('x')):
        node.whatever = 42
        assert node.whatever == 42


def test_nested_containers_keep_working() -> None:
    doc = CommentedMap(outer=CommentedSeq([CommentedMap(inner=1)]))
    assert doc['outer'][0]['inner'] == 1
    assert doc == {'outer': [{'inner': 1}]}


# --------------------------------------------------------------------------- copy / pickle


def test_deepcopy_keeps_comments_and_is_independent() -> None:
    original = cmap(comments={'a': 'for a'}, a=1, b=2)
    original['list'] = seq('x', 'y', comments={1: 'for y'})
    clone = copy.deepcopy(original)

    assert clone == original
    assert eol(clone, 'a') == '# for a'
    assert eol(clone['list'], 1) == '# for y'

    clone.yaml_add_eol_comment('clone only', 'b')
    del clone['a']
    assert eol(original, 'a') == '# for a'
    assert eol(original, 'b') is None


def test_deepcopy_of_seq_keeps_comments() -> None:
    s = seq('a', 'b', 'c', comments={0: 'zero', 2: 'two'})
    clone = copy.deepcopy(s)
    assert eols(clone) == {0: '# zero', 2: '# two'}
    clone.insert(0, 'new')
    assert eols(clone) == {1: '# zero', 3: '# two'}
    assert eols(s) == {0: '# zero', 2: '# two'}


def test_pickle_roundtrip_keeps_comments() -> None:
    doc = cmap(comments={'a': 'for a'}, a=1, b=2)
    doc['list'] = seq('x', 'y', comments={1: 'for y'})
    doc.yaml_set_start_comment('header')

    back = pickle.loads(pickle.dumps(doc))
    assert back == doc
    assert eol(back, 'a') == '# for a'
    assert eol(back['list'], 1) == '# for y'
    assert back.ca.comment[1][0].value == '# header\n'


def test_pickle_seq_and_scalar() -> None:
    s = seq('a', 'b', comments={1: 'one'})
    back = pickle.loads(pickle.dumps(s))
    assert back == ['a', 'b'] and eols(back) == {1: '# one'}

    ts = TaggedScalar('42', style='"', tag='!Point')
    back_ts = pickle.loads(pickle.dumps(ts))
    assert back_ts == '42' and back_ts.style == '"' and back_ts.tag == '!Point'


def test_map_copy_is_shallow_but_comments_are_not_shared() -> None:
    original = cmap(comments={'a': 'for a'}, a=1)
    clone = original.copy()
    assert isinstance(clone, CommentedMap)
    assert eol(clone, 'a') == '# for a'
    clone.yaml_add_eol_comment('other', 'a', column=0)
    del clone['a']
    assert eol(original, 'a') == '# for a'


# ------------------------------------------------------- sequences: no index drift


def test_seq_insert_moves_comments_with_their_element() -> None:
    s = seq('a', 'b', 'c', comments={0: 'for a', 2: 'for c'})
    s.insert(0, 'z')
    assert s == ['z', 'a', 'b', 'c']
    assert eols(s) == {1: '# for a', 3: '# for c'}
    s.insert(2, 'mid')
    assert s == ['z', 'a', 'mid', 'b', 'c']
    assert eols(s) == {1: '# for a', 4: '# for c'}


def test_seq_delete_drops_only_that_comment() -> None:
    s = seq('a', 'b', 'c', comments={0: 'for a', 2: 'for c'})
    del s[0]
    assert s == ['b', 'c']
    assert eols(s) == {1: '# for c'}


def test_seq_pop_and_remove() -> None:
    s = seq('a', 'b', 'c', comments={1: 'for b', 2: 'for c'})
    assert s.pop(0) == 'a'
    assert eols(s) == {0: '# for b', 1: '# for c'}
    s.remove('b')
    assert s == ['c']
    assert eols(s) == {0: '# for c'}
    assert s.pop() == 'c'
    assert eols(s) == {}


def test_seq_reverse_and_sort_carry_comments() -> None:
    s = seq('a', 'b', 'c', comments={0: 'for a', 2: 'for c'})
    s.reverse()
    assert s == ['c', 'b', 'a']
    assert eols(s) == {0: '# for c', 2: '# for a'}
    s.sort()
    assert s == ['a', 'b', 'c']
    assert eols(s) == {0: '# for a', 2: '# for c'}
    s.sort(key=lambda x: x, reverse=True)
    assert s == ['c', 'b', 'a']
    assert eols(s) == {0: '# for c', 2: '# for a'}


def test_seq_slice_operations() -> None:
    s = seq('a', 'b', 'c', 'd', comments={0: 'for a', 3: 'for d'})
    del s[1:3]
    assert s == ['a', 'd']
    assert eols(s) == {0: '# for a', 1: '# for d'}

    s[0:1] = ['x', 'y']
    assert s == ['x', 'y', 'd']
    assert eols(s) == {2: '# for d'}  # the replaced element took its comment with it


def test_seq_append_extend_clear() -> None:
    s = seq('a', comments={0: 'for a'})
    s.append('b')
    s.extend(['c'])
    s += ['d']
    assert s == ['a', 'b', 'c', 'd']
    assert eols(s) == {0: '# for a'}
    s.yaml_add_eol_comment('for d', 3)
    assert eols(s) == {0: '# for a', 3: '# for d'}
    s.clear()
    assert s == [] and eols(s) == {}


def test_seq_setitem_keeps_the_slot_comment() -> None:
    s = seq('a', 'b', comments={0: 'slot 0'})
    s[0] = 'replaced'
    assert eols(s) == {0: '# slot 0'}


def test_seq_binding_survives_random_mutation() -> None:
    """Every element carries a comment naming it; no sequence of edits may separate them."""
    import random

    rng = random.Random(20240501)
    counter = iter(range(10_000))

    def fresh() -> str:
        return f'v{next(counter)}'

    s = CommentedSeq()
    for _ in range(400):
        op = rng.choice(['insert', 'append', 'extend', 'del', 'pop', 'remove', 'reverse', 'sort', 'delslice'])
        n = len(s)
        if op == 'insert':
            idx = rng.randint(0, n)
            s.insert(idx, fresh())
            s.yaml_add_eol_comment(s[idx], idx, column=0)
        elif op == 'append':
            s.append(fresh())
            s.yaml_add_eol_comment(s[-1], len(s) - 1, column=0)
        elif op == 'extend':
            start = len(s)
            s.extend([fresh(), fresh()])
            for i in range(start, len(s)):
                s.yaml_add_eol_comment(s[i], i, column=0)
        elif n and op == 'del':
            del s[rng.randrange(n)]
        elif n and op == 'pop':
            s.pop(rng.randrange(n))
        elif n and op == 'remove':
            s.remove(s[rng.randrange(n)])
        elif op == 'reverse':
            s.reverse()
        elif op == 'sort':
            s.sort(reverse=rng.random() < 0.5)
        elif n and op == 'delslice':
            start = rng.randrange(n)
            del s[start : start + rng.randint(1, 3)]
        assert eols(s) == {i: f'# {v}' for i, v in enumerate(s)}
        assert len(s._ca_store()) == len(s)


def test_seq_store_stays_the_same_length_as_the_list() -> None:
    s = seq('a', 'b', 'c')
    for op in (lambda: s.insert(1, 'x'), lambda: s.append('y'), lambda: s.pop(0), s.reverse):
        op()
        assert len(s._ca_store()) == len(s)


# ------------------------------------------------------- mappings: no stale records


def test_map_delete_does_not_resurrect_a_comment() -> None:
    """ruamel 0.19.1 keeps the record keyed by the removed key and re-attaches it."""
    m = cmap(comments={'k': 'for k'}, k=1, j=2)
    del m['k']
    m['k'] = 99
    assert eol(m, 'k') is None
    assert eol(m, 'j') is None


def test_map_pop_popitem_clear_drop_records() -> None:
    m = cmap(comments={'a': 'for a', 'b': 'for b'}, a=1, b=2, c=3)
    assert m.pop('a') == 1
    assert eol(m, 'a') is None
    assert m.pop('a', 'default') == 'default'
    with pytest.raises(KeyError):
        m.pop('a')
    assert m.popitem() == ('c', 3)
    m.clear()
    assert eols(m) == {}


def test_map_move_to_end_keeps_comments() -> None:
    m = cmap(comments={'a': 'for a', 'c': 'for c'}, a=1, b=2, c=3)
    m.move_to_end('a')
    assert list(m) == ['b', 'c', 'a']
    assert eols(m) == {'c': '# for c', 'a': '# for a'}
    m.move_to_end('c', last=False)
    assert list(m) == ['c', 'b', 'a']
    assert eols(m) == {'c': '# for c', 'a': '# for a'}


def test_map_rename_keeps_position_and_comment() -> None:
    m = cmap(comments={'b': 'for b'}, a=1, b=2, c=3)
    m.rename('b', 'beta')
    assert list(m) == ['a', 'beta', 'c']
    assert m['beta'] == 2
    assert eol(m, 'beta') == '# for b'
    assert eol(m, 'b') is None


def test_map_insert_positions_and_keeps_other_comments() -> None:
    m = cmap(comments={'a': 'for a', 'c': 'for c'}, a=1, c=3)
    m.insert(1, 'b', 2, comment='for b')
    assert list(m) == ['a', 'b', 'c']
    assert eols(m) == {'a': '# for a', 'b': '# for b', 'c': '# for c'}
    m.insert(0, 'c', 33)  # moving an existing key keeps its comment
    assert list(m) == ['c', 'a', 'b']
    assert m['c'] == 33
    assert eol(m, 'c') == '# for c'


def test_map_update_and_ior() -> None:
    m = cmap(comments={'a': 'for a'}, a=1)
    m.update({'b': 2}, c=3)
    m |= {'d': 4}
    assert m == {'a': 1, 'b': 2, 'c': 3, 'd': 4}
    assert eols(m) == {'a': '# for a'}
    m.update([('a', 11)])
    assert m['a'] == 11
    assert eol(m, 'a') == '# for a'  # same entry, so the comment stays


def test_map_setdefault_and_get() -> None:
    m = cmap(a=1)
    assert m.setdefault('a', 9) == 1
    assert m.setdefault('b', 9) == 9
    assert m.get('missing') is None
    assert m.get('missing', 'x') == 'x'


def test_map_mlget() -> None:
    m = CommentedMap(a=CommentedMap(b=CommentedMap(c=1)))
    assert m.mlget(['a', 'b', 'c']) == 1
    assert m.mlget(['a', 'nope', 'c'], default='d') == 'd'


def test_scalar_string_subclass_is_preserved_on_assignment() -> None:
    class Literal(str):
        pass

    m = CommentedMap(a=Literal('x'))
    m['a'] = 'plain'
    assert isinstance(m['a'], Literal)

    s = CommentedSeq([Literal('x')])
    s[0] = 'plain'
    assert isinstance(s[0], Literal)


# --------------------------------------------------------------------------- ca API


def test_ca_items_is_a_projection_not_a_second_store() -> None:
    s = seq('a', 'b', comments={0: 'zero'})
    first = s.ca.items
    s.insert(0, 'new')
    second = s.ca.items
    assert 0 in first and 0 not in second and 1 in second


def test_ca_items_writes_through_for_maps() -> None:
    m = CommentedMap(a=1)
    record = m.ca.items.setdefault('a', [None, [], None, None])
    record[C_KEY_PRE].append(CommentToken('# written\n', CommentMark(0)))
    assert m.ca.items['a'][C_KEY_PRE][0].value == '# written\n'


def test_ca_items_writes_through_for_sequences() -> None:
    s = CommentedSeq(['a', 'b'])
    record = s.ca.items.setdefault(1, [None, [], None, None])
    record[C_ELEM_EOL] = CommentToken('# written', CommentMark(4))
    assert s.ca.items[1][C_ELEM_EOL].value == '# written'

    s.ca.items[0] = [CommentToken('# zero', CommentMark(0)), None, None, None]
    assert eols(s) == {0: '# zero', 1: '# written'}

    s.ca.items.pop(0)
    assert eols(s) == {1: '# written'}
    with pytest.raises(IndexError):
        s.ca.items[7] = [None, None, None, None]


def test_yaml_set_start_comment() -> None:
    m = CommentedMap(a=1)
    m.yaml_set_start_comment('first\nsecond', indent=2)
    pre = m.ca.comment[1]
    assert [c.value for c in pre] == ['# first\n', '# second\n']
    assert [c.column for c in pre] == [2, 2]
    m.yaml_set_start_comment('# already hashed')
    assert [c.value for c in m.ca.comment[1]] == ['# already hashed\n']


def test_yaml_set_comment_before_after_key() -> None:
    m = CommentedMap(a=1, b=2)
    m.yaml_set_comment_before_after_key('b', before='why b\nis here', indent=2, after='trailing')
    record = m.ca.items['b']
    assert [c.value for c in record[C_KEY_PRE]] == ['# why b\n', '# is here\n']
    assert record[C_KEY_PRE][0].column == 2
    assert [c.value for c in record[C_VALUE_POST]] == ['# trailing\n']
    assert record[C_VALUE_POST][0].column == 4  # indent + 2


def test_yaml_set_comment_before_after_key_blank_line() -> None:
    m = CommentedMap(a=1)
    m.yaml_set_comment_before_after_key('a', before='\n')
    token = m.ca.items['a'][C_KEY_PRE][0]
    assert token.value == '\n' and token.is_blank_line


def test_yaml_end_comment_extend() -> None:
    m = CommentedMap(a=1)
    m.yaml_end_comment_extend([CommentToken('# end\n', CommentMark(0))])
    m.yaml_end_comment_extend([CommentToken('# more\n', CommentMark(0))])
    assert [c.value for c in m.ca.end] == ['# end\n', '# more\n']
    m.yaml_end_comment_extend([CommentToken('# only\n', CommentMark(0))], clear=True)
    assert [c.value for c in m.ca.end] == ['# only\n']
    m.yaml_end_comment_extend(None)
    assert len(m.ca.end) == 1


def test_eol_comment_column_follows_the_neighbours() -> None:
    m = cmap(a=1, b=2, c=3)
    m.yaml_add_eol_comment('for a', 'a', column=10)
    m.yaml_add_eol_comment('for b', 'b')
    assert m.ca.items['b'][C_VALUE_EOL].column == 10

    s = CommentedSeq(['a', 'b'])
    s.yaml_add_eol_comment('zero', 0, column=7)
    s.yaml_add_eol_comment('one', 1)
    assert s.ca.items[1][C_ELEM_EOL].column == 7


def test_eol_comment_on_the_node_itself() -> None:
    s = CommentedSeq(['a'])
    s.yaml_add_eol_comment('about the whole seq')
    assert s.ca.comment[0].value == '# about the whole seq'


def test_comment_contains_and_repr() -> None:
    m = cmap(comments={'a': 'needle here'}, a=1)
    assert 'needle' in m.ca
    assert 'haystack' not in m.ca
    assert 'items=' in repr(m.ca)


def test_comment_get_and_set() -> None:
    m = CommentedMap(a=1)
    assert m.ca.get('a', C_VALUE_EOL) is None
    token = CommentToken('# set', CommentMark(3))
    m.ca.set('a', C_VALUE_EOL, token)
    assert m.ca.get('a', C_VALUE_EOL) is token
    assert m.ca.items['a'][C_VALUE_EOL] is token


def test_detached_comment_has_empty_items() -> None:
    assert Comment().items == {}


# --------------------------------------------------------------------------- attributes


def test_anchor_format_linecol_tag() -> None:
    m = CommentedMap(a=1)

    m.yaml_set_anchor('base', always_dump=True)
    assert isinstance(m.anchor, Anchor)
    assert (m.anchor.value, m.anchor.always_dump) == ('base', True)
    assert m.yaml_anchor() is m.anchor

    assert isinstance(m.fa, Format)
    assert m.fa.flow_style(default=True) is True  # nothing set -> caller's default
    m.fa.set_flow_style()
    assert m.fa.flow_style(default=False) is True
    m.fa.set_block_style()
    assert m.fa.flow_style() is False

    assert isinstance(m.lc, LineCol)
    m.lc.line, m.lc.col = 3, 4
    m.lc.add_kv_line_col('a', (3, 0, 3, 3))
    assert m.lc.key('a') == (3, 0)
    assert m.lc.value('a') == (3, 3)

    assert m.tag.value is None and not m.tag
    m.tag = '!Circuit'
    assert isinstance(m.tag, Tag)
    assert m.tag == '!Circuit' and str(m.tag) == '!Circuit'
    m.tag = Tag(handle='!!', suffix='str', resolved='tag:yaml.org,2002:str')
    assert m.tag.value == 'tag:yaml.org,2002:str'
    assert m.tag.startswith('tag:')


def test_yaml_anchor_is_none_until_set() -> None:
    assert CommentedSeq().yaml_anchor() is None


def test_copy_attributes() -> None:
    src = cmap(comments={'a': 'for a'}, a=1)
    src.yaml_set_anchor('anch')
    src.fa.set_flow_style()
    dst = CommentedMap(a=1)
    src.copy_attributes(dst)
    assert dst.anchor.value == 'anch'
    assert dst.fa.flow_style() is True
    assert eol(dst, 'a') == '# for a'
    # copies, not aliases: mutating the target leaves the source alone
    dst.yaml_add_eol_comment('changed', 'a', column=0)
    del dst['a']
    assert eol(src, 'a') == '# for a'


# --------------------------------------------------------------------------- merge keys


def test_merge_keys() -> None:
    base = CommentedMap(x=1, y=2)
    m = cmap(comments={'y': 'own y'}, y=9)
    m.add_yaml_merge([base])

    assert m['x'] == 1  # visible through the merge
    assert m['y'] == 9  # own value wins
    assert list(m.non_merged_items()) == [('y', 9)]
    assert m.merge == [base]
    assert m.merge.merge_pos == 0
    assert eol(m, 'y') == '# own y'

    m['x'] = 5  # writing a merged key makes it ours
    assert list(m.non_merged_items()) == [('y', 9), ('x', 5)]


def test_merge_is_empty_by_default() -> None:
    m = CommentedMap(a=1)
    assert m.merge == []
    assert list(m.non_merged_items()) == [('a', 1)]


# --------------------------------------------------------------------------- other types


def test_commented_set() -> None:
    s = CommentedSet(['a', 'b', 'c'])
    assert list(s) == ['a', 'b', 'c']  # document order, not hash order
    s.yaml_add_eol_comment('for b', 'b')
    assert eol(s, 'b') == '# for b'

    s.add('d')
    assert list(s) == ['a', 'b', 'c', 'd']
    s.discard('b')
    assert list(s) == ['a', 'c', 'd']
    assert eol(s, 'b') is None  # the record went with the member

    s -= {'c'}  # a bulk set operator we do not override still reconciles
    assert list(s) == ['a', 'd']
    s.remove('a')
    assert list(s) == ['d']


def test_commented_key_seq_is_hashable_and_commented() -> None:
    key = CommentedKeySeq(('a', 1))
    key.yaml_add_eol_comment('first', 0)
    m = CommentedMap({key: 'value'})
    assert m[('a', 1)] == 'value'
    assert eol(key, 0) == '# first'
    assert hash(key) == hash(('a', 1))


def test_commented_key_map_is_hashable_and_mapping_shaped() -> None:
    key = CommentedKeyMap({'a': 1, 'b': 2})
    assert len(key) == 2
    assert list(key) == ['a', 'b']
    assert key['a'] == 1
    assert 'a' in key and 'z' not in key
    assert list(key.items()) == [('a', 1), ('b', 2)]
    assert list(key.values()) == [1, 2]
    assert key.get('z', 'd') == 'd'
    assert dict(key) == {'a': 1, 'b': 2}
    assert key == CommentedKeyMap({'a': 1, 'b': 2})
    assert key != CommentedKeyMap({'a': 1})
    assert {key: 'v'}[CommentedKeyMap({'a': 1, 'b': 2})] == 'v'
    with pytest.raises(KeyError):
        key['nope']
    key.yaml_add_eol_comment('for a', 'a')
    assert eol(key, 'a') == '# for a'
    assert CommentedKeyMap.fromkeys(['a', 'b'], 0) == {'a': 0, 'b': 0}


def test_tagged_scalar() -> None:
    ts = TaggedScalar('1,2', style="'", tag='!Point')
    assert ts == '1,2' and ts.value == '1,2'
    assert ts.style == "'"
    assert ts.tag == '!Point'
    assert ts.upper() == '1,2'.upper()
    assert 'TaggedScalar' in repr(ts)
    with pytest.raises(TypeError):
        ts.value = 'other'


def test_tagged_scalar_as_a_map_value_survives_copy() -> None:
    m = CommentedMap(a=TaggedScalar('x', tag='!T'))
    clone = copy.deepcopy(m)
    assert clone['a'] == 'x' and clone['a'].tag == '!T'


def test_comment_token_blank_line() -> None:
    assert CommentToken('\n', CommentMark(0)).is_blank_line
    assert not CommentToken('# x\n', CommentMark(0)).is_blank_line
    assert CommentToken('# x', column=4).column == 4


# --------------------------------------------------------------------------- differential


def test_we_do_not_drift_where_ruamel_does() -> None:
    """The headline fix, measured against the library we replace."""
    ruamel = pytest.importorskip('ruamel.yaml')
    yaml = ruamel.YAML()

    source = '- a  # for a\n- b  # for b\n- c  # for c\n'
    their_seq = yaml.load(source)
    their_seq.reverse()
    # ruamel keys by index, so reversing the elements leaves the comments behind
    assert their_seq[0] == 'c'
    assert their_seq.ca.items[0][0].value.strip() == '# for a'

    our_seq = seq('a', 'b', 'c', comments={0: 'for a', 1: 'for b', 2: 'for c'})
    our_seq.reverse()
    assert our_seq[0] == 'c'
    assert eol(our_seq, 0) == '# for c'

    their_map = yaml.load('k: 1  # for k\n')
    del their_map['k']
    their_map['k'] = 99
    # ruamel kept the record keyed by the deleted key
    assert their_map.ca.items['k'][2].value.strip() == '# for k'

    our_map = cmap(comments={'k': 'for k'}, k=1)
    del our_map['k']
    our_map['k'] = 99
    assert eol(our_map, 'k') is None
