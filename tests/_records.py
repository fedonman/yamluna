"""Builders for hand-writing FFI record trees (`yamluna._record`) in a test.

The constructor and representer tests run with no Rust extension, so they build every
input by hand. Writing the flat arena out directly ("node 3's children are [4, 7]") is
unreadable; these helpers let you write the tree the way it looks in the YAML and hand
the flattening to `doc`:

```python
from _records import doc, mapping, seq, scalar, comment, blank

d = doc(mapping([('a', seq(['1', '2'])), ('b', scalar('x', STYLE_DOUBLE))]))

d.nodes[d.root].kind  # KIND_MAPPING
d.nodes[d.root].children  # [1, 2, 5, 6], which is k, v, k, v
```

Rules of the API, all of them:

* A builder returns a `Node` whose `children` still hold Node objects. That is the one
  deliberate type lie here, and `doc` is what turns them into arena indices. Nothing
  but `doc` should read `.children` off a builder result.
* Anywhere a child node is expected you may pass a `str` instead, and it becomes a
  plain scalar: `seq(['a', 'b'])` is `seq([scalar('a'), scalar('b')])`.
* Every builder forwards extra keywords straight to `Node`, so `anchor=`, `tag=`,
  `line=`, `col=`, `raw=`, `merge=`, `before=`, `eol=`, `inner=` and `after=` all work:
  `scalar('x', raw='"x"', style=STYLE_DOUBLE, eol=comment('# hi'))`.
* `raw` defaults to `None`, because a hand-built node is a constructed node rather than
  a loaded one and the emitter is allowed to restyle it. Pass `raw=` to model a loaded
  node.
* `doc` flattens depth-first, pre-order: the root is always index 0, then each child's
  whole subtree in turn. A Node object reused in two places is emitted once and both
  parents point at the same index, which is how you build a shared subtree.

Run `python tests/_records.py` for the self-check at the bottom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from collections.abc import Iterable

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
    """Build a scalar node, as in `scalar('x', STYLE_DOUBLE, raw='"x"')`.

    Args:
        value: The cooked value, with escapes resolved and a block scalar folded.
        style: One of `STYLE_PLAIN`, `STYLE_SINGLE`, `STYLE_DOUBLE`, `STYLE_LITERAL`,
            `STYLE_FOLDED`.
        **kw: Passed straight to `Node`, so `raw=`, `anchor=`, `eol=` and the rest work.

    Returns:
        A `Node` of kind `KIND_SCALAR`.

    """
    return Node(KIND_SCALAR, style, value=value, **kw)


def seq(items: Iterable[Child], style: int = STYLE_BLOCK, **kw: Any) -> Node:
    """Build a sequence node from its items.

    Args:
        items: The children. A `str` item becomes a plain scalar.
        style: `STYLE_BLOCK` or `STYLE_FLOW`.
        **kw: Passed straight to `Node`.

    Returns:
        A `Node` of kind `KIND_SEQUENCE` whose `children` still hold Node objects, until
        `doc` flattens them.

    """
    # `children` is `list[Any]` for the same reason it is in `mapping`: these hold Node
    # objects until `doc` flattens them into arena indices.
    children: list[Any] = [_child(i) for i in items]
    return Node(KIND_SEQUENCE, style, children=children, **kw)


def mapping(pairs: Iterable[tuple[Child, Child]], style: int = STYLE_BLOCK, **kw: Any) -> Node:
    """Build a mapping node from `(key, value)` pairs, flattened into `k, v, k, v`.

    Args:
        pairs: The entries in source order. A `str` key or value becomes a plain scalar.
        style: `STYLE_BLOCK` or `STYLE_FLOW`.
        **kw: Passed straight to `Node`. `merge=` takes positions in `children`, so
            `mapping([('<<', alias('base'))], merge=[0])` is `<<: *base`.

    Returns:
        A `Node` of kind `KIND_MAPPING` with twice as many children as pairs.

    """
    children: list[Any] = []
    for key, value in pairs:
        children += (_child(key), _child(value))
    return Node(KIND_MAPPING, style, children=children, **kw)


def alias(name: str, **kw: Any) -> Node:
    """Build `*name`, an alias node.

    Args:
        name: The anchor being referenced, without the `*`. It is stored in
            `Node.anchor`, the same slot a node that defines an anchor uses.
        **kw: Passed straight to `Node`.

    Returns:
        A `Node` of kind `KIND_ALIAS`.

    """
    return Node(KIND_ALIAS, anchor=name, **kw)


def comment(text: str, *, own_line: bool = True, col: int = 0) -> Trivia:
    """Build a comment trivium.

    Args:
        text: The comment including its `#` and excluding the line break.
        own_line: True when the comment sits on a line of its own, False for an
            end-of-line comment.
        col: The column the `#` was written at.

    Returns:
        A `Trivia` carrying the comment.

    """
    return Trivia(text, own_line, col)


def blank(count: int = 1) -> Trivia:
    """Build a run of blank lines.

    Args:
        count: How many blank lines the run holds.

    Returns:
        A `Trivia` with `blank_lines` set and no text.

    """
    return Trivia(blank_lines=count)


def doc(root: Node | str | None = None, **kw: Any) -> Doc:
    """Flatten a builder tree into a `Doc`.

    Args:
        root: The root node, or a `str` for a plain scalar root. `doc()` with no root is
            the empty document.
        **kw: Passed straight to `Doc`, so `version=`, `tag_directives=`,
            `explicit_start=`, `explicit_end=`, `leading=` and `trailing=` all work.

    Returns:
        A `Doc` whose `nodes` are in depth-first pre-order with the root at index 0, and
        whose `root` is that index or `None` for the empty document.

    """
    nodes: list[Node] = []
    index = None if root is None else _flatten(_child(root), nodes, {})
    return Doc(root=index, nodes=nodes, **kw)


def docs(*roots: Node | str) -> list[Doc]:
    """Build one `Doc` per root, which is what `parse` returns for a multi-document stream.

    Args:
        *roots: One root node, or `str` for a plain scalar root, per document.

    Returns:
        The documents in the order the roots were given.

    """
    return [doc(r) for r in roots]


def _child(node: Child) -> Node:
    return scalar(node) if isinstance(node, str) else node


def _flatten(node: Node, nodes: list[Node], seen: dict[int, int]) -> int:
    """Append `node`'s subtree to `nodes` in pre-order and returns `node`'s index."""
    # A Node reused in two places is flattened once, so both parents point at one index.
    if id(node) in seen:
        return seen[id(node)]
    index = len(nodes)
    seen[id(node)] = index
    # Claim the index before recursing: the children have to land after their parent.
    nodes.append(node)
    # `node.children` still holds Node objects here, which is the type lie the module
    # docstring names; this is the line that pays it off.
    unflattened: list[Any] = node.children
    children = [_flatten(c, nodes, seen) for c in unflattened]
    nodes[index] = _rewire(node, children)
    return index


def _rewire(n: Node, children: list[int]) -> Node:
    """Return a copy of `n` with its Node children replaced by arena indices."""
    # A fresh Node rather than an assignment, so a builder result can be flattened twice
    # and a shared subtree is not mutated out from under its other parent.
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
        list(n.explicit),
        list(n.before),
        n.eol,
        list(n.inner),
        list(n.after),
    )


def _selfcheck() -> None:
    """Check the flattening rules the module docstring states, and prints `ok`."""
    d = doc(mapping([('a', seq(['1', '2'])), ('b', scalar('x', STYLE_DOUBLE, raw='"x"'))]))
    assert d.root == 0, d
    root = d.nodes[0]
    assert root.kind == KIND_MAPPING and root.children == [1, 2, 5, 6], root
    assert d.nodes[2].kind == KIND_SEQUENCE and d.nodes[2].children == [3, 4], d.nodes[2]
    assert [n.value for n in d.nodes] == [None, 'a', None, '1', '2', 'b', 'x'], d.nodes
    assert d.nodes[6].style == STYLE_DOUBLE and d.nodes[6].raw == '"x"'

    # Builders are pure: the tree can be flattened twice, and equal trees compare equal.
    tree = seq(['a', alias('base')])
    assert doc(tree) == doc(tree), doc(tree)
    assert doc(seq(['a'])) == doc(seq(['a']))
    assert doc(seq(['a'])) != doc(seq(['b']))
    assert isinstance(tree.children[0], Node), 'builder children stay Nodes'

    # A shared subtree is emitted once and referenced twice.
    shared = seq(['1'])
    two = doc(seq([shared, shared]))
    assert two.nodes[0].children == [1, 1], two
    assert len(two.nodes) == 3, two

    # Merge entries and trivia ride along.
    m = doc(mapping([('<<', alias('base'))], merge=[0], before=[blank(1), comment('# hi')]))
    assert m.nodes[0].merge == [0] and m.nodes[2].anchor == 'base', m
    assert m.nodes[0].before == [Trivia(blank_lines=1), Trivia('# hi', own_line=True, col=0)], m

    assert doc().root is None and doc().nodes == []
    assert doc(explicit_start=True, version=(1, 2)).version == (1, 2)
    assert len(docs('a', 'b')) == 2

    # The repr elides defaults and names the constants.
    assert repr(scalar('a')) == "Node(value='a')", repr(scalar('a'))
    assert repr(d.nodes[0]) == 'Node(kind=MAPPING, style=BLOCK, children=[1, 2, 5, 6])'
    assert repr(comment('# hi', own_line=False)) == "Trivia(text='# hi', own_line=False)"
    assert repr(EmitOptions()).startswith('EmitOptions(map_indent=2, seq_indent=2, width=80')

    print('ok')


if __name__ == '__main__':
    _selfcheck()
