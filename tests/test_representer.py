"""Tests for `yamluna.representer`: a Python tree in, FFI records out.

The strongest tests here are the round trips at the end of the file: build a record tree,
construct it into Python objects, represent it back, then assert the records are identical.
A per-direction test can pass while the two directions disagree about which slot a comment
lives in; a round trip cannot. Those tests skip until `yamluna.constructor` exists, and
everything above them tests this module on its own.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from _records import (
    KIND_ALIAS,
    KIND_MAPPING,
    KIND_SCALAR,
    KIND_SEQUENCE,
    STYLE_BLOCK,
    STYLE_DOUBLE,
    STYLE_FLOW,
    STYLE_FOLDED,
    STYLE_LITERAL,
    STYLE_PLAIN,
    STYLE_SINGLE,
    Doc,
    Node,
    Trivia,
    alias,
    blank,
    comment,
    doc,
    mapping,
    scalar,
    seq,
)
from yamluna import scalarbool, scalarfloat, scalarint, timestamp
from yamluna.comments import (
    C_ELEM_EOL,
    C_ELEM_POST,
    C_ELEM_PRE,
    C_KEY_EOL,
    C_KEY_PRE,
    C_VALUE_EOL,
    C_VALUE_POST,
    CommentedMap,
    CommentedSeq,
    CommentedSet,
    CommentMark,
    CommentToken,
    Tag,
    TaggedScalar,
)
from yamluna.error import RepresenterError
from yamluna.registry import TagRegistry
from yamluna.representer import represent, represent_all
from yamluna.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    SingleQuotedScalarString,
)

# -- helpers ------------------------------------------------------------------------------


def root(data: Any, **kw: Any) -> Node:
    """Returns the root node of `represent(data)`.

    Args:
        data: The Python tree to represent.
        kw: Keyword options for `represent`, such as `registry` or `version`.

    Returns:
        The document's root record node.
    """
    d = represent(data, **kw)
    assert d.root == 0, d
    return d.nodes[0]


def one(data: Any, **kw: Any) -> Node:
    """Returns the value node of `represent({'k': data})`, to test one scalar in isolation.

    Args:
        data: The value to put under the key `k`.
        kw: Keyword options for `represent`, such as `version`.

    Returns:
        The record node written for `data`.
    """
    d = represent({'k': data}, **kw)
    return d.nodes[d.nodes[0].children[1]]


def token(text: str, column: int = 0) -> CommentToken:
    """Returns a `CommentToken` holding `text`, starting at `column`."""
    return CommentToken(text, CommentMark(column))


# -- scalars ------------------------------------------------------------------------------


class TestLexemePreservation:
    """A loaded scalar writes back its source lexeme; a built one is formatted."""

    @pytest.mark.parametrize(
        'lexeme',
        ['0x1f', '0X1F', '0o755', '0b1010', '1_000_000', '+5', '007', '-0x10'],
    )
    def test_int_lexeme(self, lexeme: str) -> None:
        node = one(scalarint.from_lexeme(lexeme))
        assert (node.raw, node.value, node.style) == (lexeme, lexeme, STYLE_PLAIN)

    @pytest.mark.parametrize('lexeme', ['1.0e+3', '.5', '3.', '1_000.5', '-.inf', '.nan', '+2.5'])
    def test_float_lexeme(self, lexeme: str) -> None:
        node = one(scalarfloat.from_lexeme(lexeme))
        assert (node.raw, node.value) == (lexeme, lexeme)

    @pytest.mark.parametrize('lexeme', ['yes', 'on', 'True', 'FALSE', 'n'])
    def test_bool_lexeme(self, lexeme: str) -> None:
        node = one(scalarbool.from_lexeme(lexeme))
        assert (node.raw, node.value) == (lexeme, lexeme)

    @pytest.mark.parametrize('lexeme', ['2002-12-14', '2001-12-14t21:59:43.10-05:00'])
    def test_timestamp_lexeme(self, lexeme: str) -> None:
        node = one(timestamp.from_lexeme(lexeme))
        assert (node.raw, node.value) == (lexeme, lexeme)

    def test_string_lexeme_and_style(self) -> None:
        node = one(SingleQuotedScalarString('a b', lexeme="'a b'"))
        assert (node.raw, node.value, node.style) == ("'a b'", 'a b', STYLE_SINGLE)

    def test_block_scalar_lexeme(self) -> None:
        loaded = LiteralScalarString('one\ntwo\n', lexeme='|\n  one\n  two\n')
        node = one(loaded)
        assert node.raw == '|\n  one\n  two\n'
        assert (node.value, node.style) == ('one\ntwo\n', STYLE_LITERAL)

    def test_constructed_values_have_no_raw(self) -> None:
        """With no lexeme the emitter formats the value, and formatting loses nothing.

        ruamel turns `1_000.5` into `01000.5` and `+12` into `12`, which is a lost digit
        separator, a fabricated leading zero and a dropped sign.
        """
        assert one(scalarint.ScalarInt(1000)).raw is None
        assert one(scalarint.ScalarInt(1000)).value == '1000'
        assert one(scalarint.HexInt(255)).value == '0xff'
        # The sign goes in front of the base prefix. ruamel writes '0x-1F', which matches
        # neither the YAML 1.1 nor the 1.2 integer production and only ruamel reads back.
        assert one(scalarint.HexInt(-31, caps=True)).value == '-0x1F'
        assert one(scalarfloat.ScalarFloat(1.5)).value == '1.5'
        assert one(scalarbool.ScalarBoolean(True)).value == 'true'
        assert one(LiteralScalarString('a\n')).raw is None

    def test_edited_string_drops_the_lexeme_but_keeps_the_style(self) -> None:
        edited = SingleQuotedScalarString('a b', lexeme="'a b'").replace('b', 'c')
        node = one(edited)
        assert (node.raw, node.value, node.style) == (None, 'a c', STYLE_SINGLE)


class TestBuiltinScalars:
    """Trees users build by hand: no lexeme anywhere, so style is chosen here."""

    def test_plain_types(self) -> None:
        assert (one(True).value, one(True).style) == ('true', STYLE_PLAIN)
        assert one(False).value == 'false'
        assert one(None).value == ''
        assert one(7).value == '7'
        assert one(-3).value == '-3'
        assert one(1.5).value == '1.5'
        assert one(float('inf')).value == '.inf'
        assert one(float('nan')).value == '.nan'
        assert one(datetime.date(2002, 12, 14)).value == '2002-12-14'
        assert one(datetime.datetime(2002, 12, 14, 1, 2, 3)).value == '2002-12-14T01:02:03'

    def test_root_none_is_a_null_document(self) -> None:
        assert root(None).value == 'null'

    @pytest.mark.parametrize('text', ['hello', 'a b c', 'x-y', 'inf', '_100', 'a:b', 'a#b', '-x'])
    def test_stays_plain(self, text: str) -> None:
        assert one(text).style == STYLE_PLAIN, text
        assert one(text).value == text

    @pytest.mark.parametrize(
        'text',
        [
            'true', 'True', 'FALSE', 'null', 'Null', '~',  # resolver words
            '1', '1.0', '0x1f', '+12', '1_000', '.inf', '.nan',  # numbers
            '2002-12-14', '2001-12-14t21:59:43.10-05:00',  # timestamps
            '', ' x', 'x ',  # empty / whitespace at an edge
            '- x', '#c', ': ', '? x', ', x', '[x', '{x', '&a', '*a', '!t', '|x', '>x',
            "'x", '"x', '%d', '@x', '`x',  # indicators
            'a: b', 'a #c', 'a:',  # a plain scalar cannot contain ': ' or ' #' or end in ':'
        ],
    )
    def test_gets_quoted(self, text: str) -> None:
        node = one(text)
        assert node.style == STYLE_SINGLE, (text, node)
        assert node.value == text  # the value is untouched: only the style changed

    @pytest.mark.parametrize('text', ['a\nb', 'a\tb', '\tx', 'x\x00y', 'a\x7f'])
    def test_control_characters_force_double_quotes(self, text: str) -> None:
        assert one(text).style == STYLE_DOUBLE, text

    @pytest.mark.parametrize('text', ['yes', 'no', 'on', 'off', 'y', 'N'])
    def test_the_yaml_11_booleans_are_quoted_only_under_yaml_11(self, text: str) -> None:
        """Quoting asks the loader's own resolver, so the two directions cannot disagree."""
        assert one(text).style == STYLE_PLAIN, text
        assert one(text, version=(1, 1)).style == STYLE_SINGLE, text

    def test_plain_scalar_string_is_quoted_when_plain_would_lie(self) -> None:
        """An explicit plain style loses to not changing the value's type on reload."""
        assert one(PlainScalarString('plain')).style == STYLE_PLAIN
        assert one(PlainScalarString('true')).style == STYLE_SINGLE
        assert one(PlainScalarString('')).style == STYLE_SINGLE
        # ... but a *loaded* plain scalar re-emits its lexeme and is never second-guessed
        assert one(PlainScalarString('true', lexeme='true')).style == STYLE_PLAIN

    def test_scalar_string_styles(self) -> None:
        assert one(SingleQuotedScalarString('x')).style == STYLE_SINGLE
        assert one(DoubleQuotedScalarString('x')).style == STYLE_DOUBLE
        assert one(LiteralScalarString('x\n')).style == STYLE_LITERAL
        assert one(FoldedScalarString('x\n')).style == STYLE_FOLDED

    def test_unrepresentable(self) -> None:
        with pytest.raises(RepresenterError, match='cannot represent'):
            represent({'k': 1j})
        with pytest.raises(RepresenterError, match='register'):
            represent({'k': object()})


# -- structure ----------------------------------------------------------------------------


class TestStructure:
    def test_plain_dict_and_list(self) -> None:
        assert represent({'a': [1, 2], 'b': 'x'}) == doc(
            mapping([('a', seq(['1', '2'])), ('b', 'x')])
        )

    def test_empty_containers(self) -> None:
        assert represent({}) == doc(mapping([]))
        assert represent([]) == doc(seq([]))

    def test_arena_is_pre_order(self) -> None:
        d = represent({'a': {'b': 'c'}, 'd': 'e'})
        assert [n.value for n in d.nodes] == [None, 'a', None, 'b', 'c', 'd', 'e']

    def test_flow_style_follows_fa(self) -> None:
        m = CommentedMap({'a': 1})
        m.fa.set_flow_style()
        assert root(m).style == STYLE_FLOW
        m.fa.set_block_style()
        assert root(m).style == STYLE_BLOCK

    def test_default_flow_style_applies_only_where_fa_is_undecided(self) -> None:
        m = CommentedMap({'a': CommentedMap({'b': 1})})
        m['a'].fa.set_block_style()
        d = represent(m, default_flow_style=True)
        assert d.nodes[0].style == STYLE_FLOW
        assert d.nodes[2].style == STYLE_BLOCK

    def test_tuple_and_set(self) -> None:
        assert represent(('a', 'b')) == doc(seq(['a', 'b']))
        node = root(CommentedSet(['a', 'b']))
        assert node.kind == KIND_MAPPING and node.tag == ('!!', 'set', 'tag:yaml.org,2002:set')
        assert len(node.children) == 4

    def test_multiple_documents_each_get_their_own_arena(self) -> None:
        assert represent_all(['a', {'b': 'c'}]) == [doc('a'), doc(mapping([('b', 'c')]))]

    def test_document_flags(self) -> None:
        d = represent('a', version=(1, 2), explicit_start=True, explicit_end=True)
        assert (d.version, d.explicit_start, d.explicit_end) == ((1, 2), True, True)

    def test_line_and_col_come_back_from_lc(self) -> None:
        """A recorded position is what lets the emitter reproduce the source's own layout.

        A byte-identical `load` then `dump` needs every untouched node written where the
        source wrote it. A bare `str` or `int` has nowhere to keep a position of its own,
        so the parent's `.lc` is where every scalar's comes back from. A stale position
        cannot open a hole in the output: the emitter stops believing recorded lines at
        the first construct that does not land on one.
        """
        m = CommentedMap({'a': 1})
        m.lc.line, m.lc.col = 7, 3
        m.lc.add_kv_line_col('a', [7, 3, 7, 6])
        d = represent(m)
        assert (d.nodes[0].line, d.nodes[0].col) == (7, 3)  # the mapping, from its own .lc
        assert (d.nodes[1].line, d.nodes[1].col) == (7, 3)  # the key
        assert (d.nodes[2].line, d.nodes[2].col) == (7, 6)  # the value, a bare int

    def test_a_tree_with_no_recorded_positions_has_none(self) -> None:
        """Nothing is invented: a tree the user built is laid out, not echoed."""
        d = represent({'a': 1})
        assert all((n.line, n.col) == (0, 0) for n in d.nodes)


# -- anchors ------------------------------------------------------------------------------


def assert_no_dangling_alias(written: Doc) -> None:
    """Asserts that every `*name` has its `&name` ahead of it.

    Args:
        written: The represented document to check.

    Raises:
        AssertionError: An alias has no anchor before it, or carries a tag.
    """
    # A node's index in the arena is its position in the document, so one pass in index
    # order is the same walk the emitter makes.
    seen: set[str] = set()
    for node in written.nodes:
        if node.kind == KIND_ALIAS:
            assert node.anchor in seen, f'*{node.anchor} has no anchor before it'
            assert node.tag is None, f'*{node.anchor} carries a tag, which is not YAML'
        elif node.anchor is not None:
            seen.add(node.anchor)


class TestAnchors:
    def test_shared_subtree_becomes_an_alias(self) -> None:
        shared = CommentedMap({'x': 1})
        shared.yaml_set_anchor('b')
        d = represent(CommentedMap({'base': shared, 'use': shared}))
        assert d == doc(mapping([('base', mapping([('x', '1')], anchor='b')), ('use', alias('b'))]))

    def test_shared_subtree_without_an_anchor_gets_a_generated_name(self) -> None:
        shared: dict[str, Any] = {'x': 1}
        d = represent({'base': shared, 'use': shared})
        assert d.nodes[2].anchor == 'id001'
        assert d.nodes[6] == Node(KIND_ALIAS, anchor='id001')

    def test_generated_names_avoid_the_ones_already_in_use(self) -> None:
        taken = CommentedMap({'x': 1})
        taken.yaml_set_anchor('id001')
        shared: dict[str, Any] = {'y': 2}
        d = represent({'a': taken, 'b': taken, 'c': shared, 'd': shared})
        assert [n.anchor for n in d.nodes if n.anchor] == ['id001', 'id001', 'id002', 'id002']

    def test_an_anchor_used_once_is_kept(self) -> None:
        """An anchor used once is kept: it is source text, so dropping it is not a round trip.

        ruamel drops any anchor referenced fewer than twice.
        """
        only = CommentedMap({'y': 2})
        only.yaml_set_anchor('unused')  # always_dump left False
        assert root(CommentedMap({'other': only})).children[1] == 2
        assert represent(CommentedMap({'other': only})).nodes[2].anchor == 'unused'

    def test_always_dump_is_honoured(self) -> None:
        node = CommentedSeq([1])
        node.yaml_set_anchor('a', always_dump=True)
        assert root(CommentedMap({'k': node})) is not None
        assert represent(CommentedMap({'k': node})).nodes[2].anchor == 'a'

    def test_anchored_scalars(self) -> None:
        shared = SingleQuotedScalarString('v', anchor='s')
        d = represent(CommentedMap({'a': shared, 'b': shared}))
        assert d.nodes[2].anchor == 's' and d.nodes[2].value == 'v'
        assert d.nodes[4] == Node(KIND_ALIAS, anchor='s')

    def test_an_alias_to_a_null_comes_back_from_the_parent(self, construct: Any) -> None:
        """An alias to a null comes back from the record its parent parked.

        In `a: &n` over `b: *n` both keys hold the one `None`, so identity cannot tell
        that `b` is an alias. The parent parked an alias record for `b`, and that record
        is what says so, but only while `&n` is still somewhere ahead of it in the
        document.
        """
        source = doc(mapping([('a', scalar('', raw='', anchor='n')), ('b', alias('n'))]))
        tree = construct(source)
        assert tree == {'a': None, 'b': None}
        written = represent(tree)
        assert written.nodes[2].anchor == 'n' and written.nodes[4] == alias('n')
        assert_no_dangling_alias(written)

    @pytest.mark.parametrize('edit', [
        pytest.param(lambda t: t.pop('a'), id='anchor-deleted'),
        pytest.param(lambda t: t.__setitem__('a', 'x'), id='anchor-replaced'),
    ])
    def test_an_alias_is_dropped_when_its_anchor_goes(
        self, construct: Any, edit: Any
    ) -> None:
        """An alias whose anchor left the tree falls back to the plain `None` it holds.

        A `*name` with no `&name` ahead of it is not YAML, so losing the alias beats
        emitting a dangling one.
        """
        tree = construct(doc(mapping([('a', scalar('', raw='', anchor='n')), ('b', alias('n'))])))
        edit(tree)
        written = represent(tree)
        assert all(n.kind != KIND_ALIAS for n in written.nodes)
        assert_no_dangling_alias(written)

    def test_equal_but_distinct_scalars_are_not_aliased(self) -> None:
        """Interned ints and strings must not turn into aliases of each other."""
        d = represent({'a': 1000, 'b': 1000, 'c': 'shared', 'd': 'shared'})
        assert [n.kind for n in d.nodes[1:]] == [KIND_SCALAR] * 8

    def test_recursion_terminates(self) -> None:
        m: dict[str, Any] = {}
        m['self'] = m
        d = represent(m)
        assert d.nodes[0].anchor == 'id001'
        assert d.nodes[2] == Node(KIND_ALIAS, anchor='id001')

    def test_mutual_recursion(self) -> None:
        a: dict[str, Any] = {}
        b: dict[str, Any] = {'a': a}
        a['b'] = b
        d = represent([a, b])
        assert [n.kind for n in d.nodes] == [
            KIND_SEQUENCE, KIND_MAPPING, KIND_SCALAR, KIND_MAPPING, KIND_SCALAR,
            KIND_ALIAS, KIND_ALIAS,
        ]


class TestMergeKeys:
    def base(self) -> CommentedMap:
        base = CommentedMap({'x': 1, 'y': 2})
        base.yaml_set_anchor('b')
        return base

    def test_merge_is_re_emitted_not_expanded(self) -> None:
        base = self.base()
        derived = CommentedMap({'y': 3})
        derived.add_yaml_merge([base])
        d = represent(CommentedMap({'base': base, 'derived': derived}))

        merged = d.nodes[d.nodes[0].children[3]]
        assert merged.merge == [0], merged
        assert d.nodes[merged.children[0]].value == '<<'
        assert d.nodes[merged.children[1]] == Node(KIND_ALIAS, anchor='b')
        assert [d.nodes[i].value for i in merged.children[2:]] == ['y', '3']
        assert 'x' not in [d.nodes[i].value for i in merged.children]

    def test_merge_position_is_kept(self) -> None:
        base = self.base()
        derived = CommentedMap({'y': 3, 'z': 4})
        derived.add_yaml_merge([base])
        derived.merge.merge_pos = 1
        d = represent(CommentedMap({'base': base, 'derived': derived}))
        merged = d.nodes[d.nodes[0].children[3]]
        assert merged.merge == [2]
        assert [d.nodes[i].value for i in merged.children] == ['y', '3', '<<', None, 'z', '4']

    def test_two_merges_become_a_flow_sequence_of_aliases(self) -> None:
        first, second = self.base(), CommentedMap({'z': 9})
        second.yaml_set_anchor('c')
        derived = CommentedMap()
        derived.add_yaml_merge([first, second])
        d = represent(CommentedMap({'a': first, 'b': second, 'd': derived}))
        merged = d.nodes[d.nodes[0].children[5]]
        value = d.nodes[merged.children[1]]
        assert value.kind == KIND_SEQUENCE and value.style == STYLE_FLOW
        assert [d.nodes[i].anchor for i in value.children] == ['b', 'c']


# -- trivia -------------------------------------------------------------------------------


class TestTrivia:
    def test_mapping_entry_slots(self) -> None:
        m = CommentedMap({'a': 1})
        record = m._ca_record('a')
        record[C_KEY_PRE] = [token('# before a\n'), token('# and more\n')]
        record[C_KEY_EOL] = token('# after the key', 3)
        record[C_VALUE_EOL] = token('# eol', 7)
        record[C_VALUE_POST] = [token('# after the value\n', 2)]
        d = represent(m)

        key, value = d.nodes[1], d.nodes[2]
        assert key.before == [Trivia('# before a'), Trivia('# and more')]
        assert key.eol == Trivia('# after the key', own_line=False, col=3)
        assert value.eol == Trivia('# eol', own_line=False, col=7)
        assert value.after == [Trivia('# after the value', col=2)]

    def test_sequence_element_slots(self) -> None:
        s = CommentedSeq(['one', 'two'])
        s._ca_record(1)[C_ELEM_PRE] = [token('# about two\n')]
        s._ca_record(1)[C_ELEM_EOL] = token('# eol two', 6)
        s._ca_record(0)[C_ELEM_POST] = [token('# after one\n')]
        d = represent(s)
        assert d.nodes[1].after == [Trivia('# after one')]
        assert d.nodes[2].before == [Trivia('# about two')]
        assert d.nodes[2].eol == Trivia('# eol two', own_line=False, col=6)

    def test_blank_lines_are_counted(self) -> None:
        m = CommentedMap({'a': 1})
        m._ca_record('a')[C_KEY_PRE] = [
            token('\n'), token('\n'), token('# c\n'), token('\n'),
        ]
        assert d_before(represent(m)) == [
            Trivia(blank_lines=2), Trivia('# c'), Trivia(blank_lines=1)
        ]

    def test_a_single_multi_newline_token_is_counted_too(self) -> None:
        m = CommentedMap({'a': 1})
        m._ca_record('a')[C_KEY_PRE] = [token('\n\n\n')]
        assert d_before(represent(m)) == [Trivia(blank_lines=3)]

    def test_own_line_versus_eol(self) -> None:
        m = CommentedMap({'a': 1})
        m._ca_record('a')[C_KEY_PRE] = [token('# own line\n')]
        m._ca_record('a')[C_VALUE_EOL] = token('# eol')
        d = represent(m)
        assert d.nodes[1].before[0].own_line is True
        assert d.nodes[2].eol.own_line is False

    def test_collection_leading_and_after(self) -> None:
        """A value's own `ca.comment[1]` is its `before`: `.ca` has no separate slot."""
        nested = CommentedMap({'b': 1})
        nested.ca.comment = [None, [token('# leading\n')]]
        nested.ca.end = [token('# trailing\n')]
        d = represent(CommentedMap({'a': nested}))
        assert d.nodes[2].before == [Trivia('# leading')]
        assert d.nodes[2].inner == []
        assert d.nodes[2].after == [Trivia('# trailing')]

    def test_collection_inner_outside_a_mapping_value(self) -> None:
        """Everywhere else the two are distinct, and `ca.comment[1]` stays `inner`."""
        element = CommentedMap({'b': 1})
        element.ca.comment = [None, [token('# inside\n')]]
        parent = CommentedSeq([element])
        parent._ca_record(0)[C_ELEM_PRE] = [token('# before\n')]
        d = represent(parent)
        assert d.nodes[1].before == [Trivia('# before')]
        assert d.nodes[1].inner == [Trivia('# inside')]

    def test_root_leading_comments_go_before_the_root(self) -> None:
        m = CommentedMap({'a': 1})
        m.yaml_set_start_comment('lead')
        node = root(m)
        assert node.before == [Trivia('# lead')]
        assert node.inner == []

    def test_node_eol_from_its_own_ca(self) -> None:
        """`yaml_add_eol_comment` with no key writes ca.comment[0]; it must still be emitted."""
        m = CommentedMap({'a': 1})
        m.yaml_add_eol_comment('note')
        assert root(m).eol == Trivia('# note', own_line=False)

    def test_comments_survive_a_mutation_that_moves_the_entry(self) -> None:
        """The record follows the entry that moved, seen from the representer's side.

        ruamel keys by index, so an insert relabels the following element's comment, and a
        rename or a `move_to_end` scatters comments across the document.
        """
        s = CommentedSeq(['one', 'two'])
        s._ca_record(0)[C_ELEM_PRE] = [token('# about one\n')]
        s.insert(0, 'zero')
        d = represent(s)
        assert d.nodes[1].before == []  # the new item carries nothing
        assert d.nodes[2].before == [Trivia('# about one')]

    def test_representing_does_not_mutate_the_object(self) -> None:
        """A dump is a read: nothing here may create `.ca` or grow it.

        ruamel's representer writes to `.ca` while dumping, so serialising an object
        changes it.
        """
        m = CommentedMap({'a': [1, 2], 'b': 'x'})
        before = {k: repr(v) for k, v in vars(m).items()}
        for _ in range(3):
            represent(m)
        assert {k: repr(v) for k, v in vars(m).items()} == before
        assert not hasattr(m, '_yaml_comment'), 'represent() created a Comment'
        assert not hasattr(m['a'], '_yaml_trivia'), 'represent() created a trivia store'


def d_before(document: Doc) -> list[Trivia]:
    """Returns the `before` trivia of the first key of a one-entry mapping."""
    return document.nodes[1].before


# -- tags ---------------------------------------------------------------------------------


# The tag source these test classes register under. A registration with no explicit
# source takes the first component of the class's `__module__`, which here is the name of
# this module.
SOURCE = __name__.partition('.')[0]


class Circuit:
    def __init__(self, qubits: int = 2) -> None:
        self.qubits = qubits


class Gate:
    def __init__(self, name: str = 'h') -> None:
        self.name = name


class Hooked:
    yaml_tag = 'Hooked'

    def __init__(self, value: str = 'v') -> None:
        self.value = value

    @classmethod
    def to_yaml(cls, representer: Any, node: Hooked) -> int:
        return representer.represent_scalar('!Hooked', node.value)


class Boxed:
    def __init__(self, items: list[int] | None = None) -> None:
        self.items = items or [1, 2]

    @classmethod
    def to_yaml(cls, representer: Any, node: Boxed) -> int:
        return representer.represent_sequence('!Boxed', node.items, flow_style=True)


class TestTags:
    def registry(self, *classes: type, **kw: Any) -> TagRegistry:
        r = TagRegistry()
        for cls in classes:
            r.register_class(cls, **kw)
        return r

    def test_single_source_gets_the_primary_handle(self) -> None:
        d = represent({'main': Circuit(2)}, registry=self.registry(Circuit))
        assert d.tag_directives == [('!', f'tag:{SOURCE}/')]
        assert d.nodes[2].tag == ('!', 'Circuit', f'tag:{SOURCE}/Circuit')
        assert [d.nodes[i].value for i in d.nodes[2].children] == ['qubits', '2']

    def test_no_registered_class_means_no_directive(self) -> None:
        assert represent({'a': 1}, registry=self.registry(Circuit)).tag_directives == []

    def test_two_sources(self) -> None:
        registry = TagRegistry()
        registry.register_class(Circuit, source='libx')
        registry.register_class(Gate, source='liby')
        d = represent({'a': Circuit(), 'b': Gate(), 'c': Gate()}, registry=registry)
        assert d.tag_directives == [('!', 'tag:liby/'), ('!libx!', 'tag:libx/')]
        assert d.nodes[2].tag == ('!libx!', 'Circuit', 'tag:libx/Circuit')
        assert d.nodes[6].tag == ('!', 'Gate', 'tag:liby/Gate')

    def test_directives_are_per_document(self) -> None:
        docs = represent_all([{'a': Circuit()}, {'b': 1}], registry=self.registry(Circuit))
        assert docs[0].tag_directives == [('!', f'tag:{SOURCE}/')]
        assert docs[1].tag_directives == []

    def test_to_yaml_hook(self) -> None:
        d = represent({'h': Hooked('x')}, registry=self.registry(Hooked))
        assert d.nodes[2] == Node(KIND_SCALAR, STYLE_PLAIN, tag=('!', 'Hooked', '!Hooked'),
                                  value='x')

    def test_to_yaml_hook_building_a_collection(self) -> None:
        d = represent({'b': Boxed([7])}, registry=self.registry(Boxed))
        assert d.nodes[2].kind == KIND_SEQUENCE and d.nodes[2].style == STYLE_FLOW
        assert d.nodes[2].tag == ('!', 'Boxed', '!Boxed')
        assert [d.nodes[i].value for i in d.nodes[2].children] == ['7']

    def test_a_hook_that_returns_the_wrong_thing_is_an_error(self) -> None:
        class Bad:
            @classmethod
            def to_yaml(cls, representer: Any, node: Any) -> Any:
                return 'not an index'

        with pytest.raises(RepresenterError, match='represent_'):
            represent({'x': Bad()}, registry=self.registry(Bad))

    def test_default_representation_is_the_instance_dict(self) -> None:
        d = represent({'c': Circuit(3)}, registry=self.registry(Circuit))
        assert [d.nodes[i].value for i in d.nodes[2].children] == ['qubits', '3']

    def test_an_unregistered_tag_round_trips_as_written(self) -> None:
        m = CommentedMap({'a': 1})
        m.tag = Tag('!', 'Unknown', '!Unknown')
        assert root(m).tag == ('!', 'Unknown', '!Unknown')

    def test_tagged_scalar(self) -> None:
        node = one(TaggedScalar('some', tag=Tag('!', 'mytag', '!mytag')))
        assert (node.value, node.tag) == ('some', ('!', 'mytag', '!mytag'))

    def test_a_string_tag_is_split_into_handle_and_suffix(self) -> None:
        m = CommentedMap({'a': 1})
        m.tag = '!Foo'
        assert root(m).tag == ('!', 'Foo', '!Foo')


# -- the round trip -----------------------------------------------------------------------

def _tag(suffix: str) -> tuple[str, str, str]:
    """Returns the `!!suffix` form of a `tag:yaml.org,2002:` tag, as the loader records it."""
    return ('!!', suffix, f'tag:yaml.org,2002:{suffix}')


_STR = _tag('str')
_BINARY = _tag('binary')

# One entry per feature above. Each is a record tree that a load could have produced, so
# `construct` followed by `represent` has to return it unchanged.
ROUND_TRIP: dict[str, Doc] = {
    'scalar': doc('hello'),
    'mapping': doc(mapping([('a', '1'), ('b', 'x')])),
    'sequence': doc(seq(['1', '2', '3'])),
    'nested': doc(mapping([('a', seq([mapping([('b', 'c')])]))])),
    'empty-collections': doc(mapping([('a', mapping([])), ('b', seq([]))])),
    'flow': doc(mapping([('a', seq(['1'], STYLE_FLOW))], STYLE_BLOCK)),
    'quoted': doc(mapping([
        ('a', scalar('x y', STYLE_SINGLE, raw="'x y'")),
        ('b', scalar('q\n', STYLE_DOUBLE, raw='"q\\n"')),
    ])),
    'block-scalars': doc(mapping([
        ('a', scalar('one\ntwo\n', STYLE_LITERAL, raw='|\n  one\n  two\n')),
        ('b', scalar('folded text\n', STYLE_FOLDED, raw='>\n  folded text\n')),
    ])),
    'lexemes': doc(mapping([
        ('i', scalar('0x1f', raw='0x1f')),
        ('f', scalar('1_000.5', raw='1_000.5')),
        ('b', scalar('yes', raw='yes')),
        ('t', scalar('2002-12-14', raw='2002-12-14')),
    ])),
    # The tag, the anchor and the lexeme of a scalar whose Python value can hold none of
    # them: `!!str 123` is a bare `str`, `!!binary` a `bytes`, `&empty` an anchored `None`.
    # The parent parks the record (constructor.SOURCE_ATTRIB) and the representer reads it
    # back, so what the loader preserved is not reformatted on the way out.
    'standard-tags': doc(mapping([
        ('str', scalar('123', raw='123', tag=_STR)),
        ('int', scalar('42', STYLE_DOUBLE, raw='"42"', tag=_tag('int'))),
        ('float', scalar('1.5', STYLE_DOUBLE, raw='"1.5"', tag=_tag('float'))),
        ('bool', scalar('true', STYLE_DOUBLE, raw='"true"', tag=_tag('bool'))),
        # not `null` as the key: that is a plain scalar, and it resolves to `None`.
        ('nil', scalar('', STYLE_DOUBLE, raw='""', tag=_tag('null'))),
        ('non-specific', scalar('plain', raw='plain', tag=('', '!', '!'))),
    ])),
    'binary': doc(mapping([
        ('block', scalar('aGVs\nbG8=\n', STYLE_LITERAL, raw='|\n  aGVs\n  bG8=', tag=_BINARY)),
        ('quoted', scalar('aGVsbG8=', STYLE_DOUBLE, raw='"aGVsbG8="', tag=_BINARY)),
        ('empty', scalar('', STYLE_DOUBLE, raw='""', tag=_BINARY)),
    ])),
    'tagged-properties': doc(mapping([
        ('tag-first', scalar('v', raw='v', tag=_STR, anchor='ta', tag_first=True)),
        ('anchor-first', scalar('v', raw='v', tag=_STR, anchor='at')),
        ('anchored-null', scalar('', raw='', anchor='empty')),
    ])),
    'tagged-in-a-sequence': doc(seq([
        scalar('1', raw='1', tag=_tag('str')),
        scalar('aGk=', STYLE_DOUBLE, raw='"aGk="', tag=_BINARY),
    ])),
    'comments': doc(mapping([
        (scalar('a', before=[comment('# about a')]), scalar('1', eol=comment('# eol a', False, 6))),
        ('b', '2'),
    ])),
    'blank-lines': doc(mapping([
        ('a', '1'),
        (scalar('b', before=[blank(2), comment('# about b')]), '2'),
    ])),
    'anchor-alias': doc(mapping([
        ('base', mapping([('x', '1')], anchor='b')),
        ('use', alias('b')),
    ])),
    'unused-anchor': doc(mapping([('other', mapping([('y', '2')], anchor='unused'))])),
    'merge': doc(mapping([
        ('base', mapping([('x', '1')], anchor='b')),
        ('derived', mapping([('<<', alias('b')), ('y', '3')], merge=[0])),
    ])),
}


def normalise(document: Doc) -> Doc:
    """Clears `raw` wherever the value alone reproduces it.

    A plain scalar whose builtin re-renders its lexeme exactly comes back as a bare `str`,
    `int` or `bool`, which has nowhere to keep a lexeme and needs none: `value` is the
    lexeme. Applied to both sides of a comparison, so a real difference still fails.

    Args:
        document: The document to normalise. It is modified in place.

    Returns:
        The same document.
    """
    for node in document.nodes:
        if node.style == STYLE_PLAIN and node.raw == node.value:
            node.raw = None
    return document


@pytest.fixture(scope='session')
def construct() -> Any:
    """Returns a callable that constructs a `Doc` into a Python tree.

    Skips the test until `yamluna.constructor` exists. The callable loads with
    `preserve_quotes=True`, so a quoted scalar keeps the class that carries its lexeme.

    Returns:
        A function taking a `Doc` and keyword options for `construct`.
    """
    module = pytest.importorskip('yamluna.constructor', reason='constructor.py is not written yet')

    def build(document: Doc, **kw: Any) -> Any:
        return module.construct(document, preserve_quotes=True, **kw)

    return build


def assert_round_trips(original: Doc, construct: Any, **kw: Any) -> None:
    """Asserts that `original` survives being constructed and represented again.

    Args:
        original: The record document a load could have produced.
        construct: The `construct` fixture.
        kw: Keyword options for `represent`, such as `registry`.

    Raises:
        AssertionError: The represented records differ from `original`.
    """
    assert normalise(represent(construct(original), **kw)) == normalise(original)


@pytest.mark.parametrize('name', sorted(ROUND_TRIP))
def test_records_survive_construct_then_represent(name: str, construct: Any) -> None:
    """The symmetry test: records -> Python -> records must be the identity."""
    assert_round_trips(ROUND_TRIP[name], construct)


def test_round_trip_with_trivia_on_every_slot(construct: Any) -> None:
    original = doc(mapping([
        (scalar('a', before=[blank(1), comment('# about a')]),
         scalar('1', eol=comment('# eol a', False, 7))),
        (scalar('b', eol=comment('# on the key', False, 3)),
         mapping([('c', '2')],
                 before=[comment('# leading b')], after=[comment('# after b', col=2)])),
    ]))
    assert_round_trips(original, construct)


def test_round_trip_of_a_registered_class(construct: Any) -> None:
    registry = TagRegistry()
    registry.register_class(Circuit)
    tagged = mapping([('qubits', '2')], tag=('!', 'Circuit', f'tag:{SOURCE}/Circuit'))
    original = doc(mapping([('main', tagged)]), tag_directives=[('!', f'tag:{SOURCE}/')])
    tree = construct(original, registry=registry)
    assert isinstance(tree['main'], Circuit) and tree['main'].qubits == 2
    assert normalise(represent(tree, registry=registry)) == normalise(original)


def test_an_edited_value_drops_the_parked_tag_and_lexeme(construct: Any) -> None:
    """The staleness guard: what the parent parked describes the value that was loaded.

    Overwrite the entry and the record no longer applies. The new value is formatted from
    scratch, tag and all, exactly as it would be in a tree nobody loaded.
    """
    tree = construct(doc(mapping([
        ('kept', scalar('123', raw='123', tag=_STR)),
        ('edited', scalar('456', raw='456', tag=_STR)),
        ('binary', scalar('aGk=', STYLE_DOUBLE, raw='"aGk="', tag=_BINARY)),
    ])))
    tree['edited'] = 'a string'
    tree['binary'] = b'bye'
    written = represent(tree).nodes
    assert (written[2].tag, written[2].raw) == (_STR, '123')
    assert (written[4].tag, written[4].raw) == (None, None)
    assert written[4].value == 'a string'
    # `bytes` is always `!!binary`; what it loses is the spelling of the payload it replaced.
    assert (written[6].tag, written[6].raw) == (_BINARY, None)
    assert written[6].value == 'Ynll'


def test_round_trip_of_an_unregistered_tag(construct: Any) -> None:
    assert_round_trips(
        doc(mapping([('a', mapping([('x', '1')], tag=('!', 'Unknown', '!Unknown')))])), construct
    )


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
