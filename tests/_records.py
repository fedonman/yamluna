"""Builders for hand-writing FFI record trees (``yamluna._record``) in a test.

The extension does not exist yet, so every loader/dumper test builds its input by hand.
Doing that against the flat arena directly ("node 3's children are [4, 7]") is unreadable;
these helpers let you write the tree the way it looks in the YAML and hand the flattening
to :func:`doc`::

    from _records import doc, mapping, seq, scalar, comment, blank

    d = doc(mapping([('a', seq(['1', '2'])), ('b', scalar('x', STYLE_DOUBLE))]))

    d.nodes[d.root].kind            # KIND_MAPPING
    d.nodes[d.root].children        # [1, 2, 5, 6]  -- k, v, k, v

Rules of the API, all of them:

* A builder returns a :class:`Node` whose ``children`` still hold **Node objects**.  That
  is the one deliberate type lie here; :func:`doc` is what turns them into arena indices.
  Nothing but :func:`doc` should read ``.children`` off a builder result.
* Anywhere a child node is expected you may pass a ``str`` instead, and it becomes a plain
  scalar -- ``seq(['a', 'b'])`` is ``seq([scalar('a'), scalar('b')])``.
* Every builder forwards extra keywords straight to :class:`Node`, so ``anchor=``, ``tag=``,
  ``line=``, ``col=``, ``raw=``, ``merge=``, ``before=``, ``eol=``, ``inner=`` and
  ``after=`` all work: ``scalar('x', raw='"x"', style=STYLE_DOUBLE, eol=comment('# hi'))``.
* ``raw`` defaults to ``None`` -- a hand-built node is a *constructed* node, not a loaded
  one, and the emitter is allowed to restyle it.  Pass ``raw=`` to model a loaded node.
* :func:`doc` flattens depth-first, pre-order: the root is always index 0, then each
  child's whole subtree in turn.  A Node object reused in two places is emitted once and
  both parents point at the same index (that is how you build a shared subtree).

Run ``python tests/_records.py`` for the self-check at the bottom.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from yamluna._record import (
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
    EmitOptions,
    Node,
    Trivia,
)

__all__ = [
    'KIND_ALIAS',
    'KIND_MAPPING',
    'KIND_SCALAR',
    'KIND_SEQUENCE',
    'STYLE_BLOCK',
    'STYLE_DOUBLE',
    'STYLE_FLOW',
    'STYLE_FOLDED',
    'STYLE_LITERAL',
    'STYLE_PLAIN',
    'STYLE_SINGLE',
    'Doc',
    'EmitOptions',
    'Node',
    'Trivia',
    'alias',
    'blank',
    'comment',
    'doc',
    'docs',
    'mapping',
    'scalar',
    'seq',
]

Child = Node | str


def scalar(value: str, style: int = STYLE_PLAIN, **kw: Any) -> Node:
    """A scalar node.  ``scalar('x', STYLE_DOUBLE, raw='"x"')``."""
    return Node(KIND_SCALAR, style, value=value, **kw)


def seq(items: Iterable[Child], style: int = STYLE_BLOCK, **kw: Any) -> Node:
    """A sequence node.  ``str`` items become plain scalars."""
    return Node(KIND_SEQUENCE, style, children=[_child(i) for i in items], **kw)


def mapping(pairs: Iterable[tuple[Child, Child]], style: int = STYLE_BLOCK, **kw: Any) -> Node:
    """A mapping node, from ``(key, value)`` pairs, flattened into ``k, v, k, v``.

    ``merge=`` takes positions in ``children`` -- ``mapping([('<<', alias('base'))],
    merge=[0])`` is ``<<: *base``.
    """
    children: list[Any] = []
    for key, value in pairs:
        children += (_child(key), _child(value))
    return Node(KIND_MAPPING, style, children=children, **kw)


def alias(name: str, **kw: Any) -> Node:
    """``*name``.  The referenced anchor lives in ``Node.anchor`` (see ``_record``)."""
    return Node(KIND_ALIAS, anchor=name, **kw)


def comment(text: str, own_line: bool = True, col: int = 0) -> Trivia:
    """A comment trivium.  ``text`` includes the ``#`` and excludes the line break."""
    return Trivia(text, own_line, col)


def blank(count: int = 1) -> Trivia:
    """A run of ``count`` blank lines."""
    return Trivia(blank_lines=count)


def doc(root: Node | str | None = None, **kw: Any) -> Doc:
    """Flatten a builder tree into a :class:`Doc`.

    Extra keywords go to :class:`Doc` (``version=``, ``tag_directives=``,
    ``explicit_start=``, ``explicit_end=``, ``leading=``, ``trailing=``).  ``doc()`` with no
    root is the empty document.
    """
    nodes: list[Node] = []
    index = None if root is None else _flatten(_child(root), nodes, {})
    return Doc(root=index, nodes=nodes, **kw)


def docs(*roots: Node | str) -> list[Doc]:
    """One :class:`Doc` per root -- what ``parse`` returns for a multi-document stream."""
    return [doc(r) for r in roots]


def _child(node: Child) -> Node:
    return scalar(node) if isinstance(node, str) else node


def _flatten(node: Node, nodes: list[Node], seen: dict[int, int]) -> int:
    """Append ``node``'s subtree to ``nodes`` pre-order; return ``node``'s index."""
    if id(node) in seen:
        return seen[id(node)]
    index = len(nodes)
    seen[id(node)] = index
    nodes.append(node)  # placeholder: children below must land after this index
    children = [_flatten(c, nodes, seen) for c in node.children]
    nodes[index] = _rewire(node, children)
    return index


def _rewire(n: Node, children: list[int]) -> Node:
    """``n`` with its Node children replaced by arena indices; the original is untouched."""
    return Node(
        n.kind,
        n.style,
        n.anchor,
        n.tag,
        n.value,
        n.raw,
        n.line,
        n.col,
        children,
        list(n.merge),
        list(n.before),
        n.eol,
        list(n.inner),
        list(n.after),
    )


def _selfcheck() -> None:
    d = doc(mapping([('a', seq(['1', '2'])), ('b', scalar('x', STYLE_DOUBLE, raw='"x"'))]))
    assert d.root == 0, d
    root = d.nodes[0]
    assert root.kind == KIND_MAPPING and root.children == [1, 2, 5, 6], root
    assert d.nodes[2].kind == KIND_SEQUENCE and d.nodes[2].children == [3, 4], d.nodes[2]
    assert [n.value for n in d.nodes] == [None, 'a', None, '1', '2', 'b', 'x'], d.nodes
    assert d.nodes[6].style == STYLE_DOUBLE and d.nodes[6].raw == '"x"'

    # builders are pure: the tree can be flattened twice, and equal trees compare equal
    tree = seq(['a', alias('base')])
    assert doc(tree) == doc(tree), doc(tree)
    assert doc(seq(['a'])) == doc(seq(['a']))
    assert doc(seq(['a'])) != doc(seq(['b']))
    assert isinstance(tree.children[0], Node), 'builder children stay Nodes'

    # a shared subtree is emitted once and referenced twice
    shared = seq(['1'])
    two = doc(seq([shared, shared]))
    assert two.nodes[0].children == [1, 1], two
    assert len(two.nodes) == 3, two

    # merge entries and trivia ride along
    m = doc(mapping([('<<', alias('base'))], merge=[0], before=[blank(1), comment('# hi')]))
    assert m.nodes[0].merge == [0] and m.nodes[2].anchor == 'base', m
    assert m.nodes[0].before == [Trivia(blank_lines=1), Trivia('# hi', True, 0)], m

    assert doc().root is None and doc().nodes == []
    assert doc(explicit_start=True, version=(1, 2)).version == (1, 2)
    assert len(docs('a', 'b')) == 2

    # repr elides defaults and names the constants
    assert repr(scalar('a')) == "Node(value='a')", repr(scalar('a'))
    assert repr(d.nodes[0]) == "Node(kind=MAPPING, style=BLOCK, children=[1, 2, 5, 6])"
    assert repr(comment('# hi', own_line=False)) == "Trivia(text='# hi', own_line=False)"
    assert repr(EmitOptions()).startswith('EmitOptions(map_indent=2, seq_indent=2, width=80')

    print('ok')


if __name__ == '__main__':
    _selfcheck()
