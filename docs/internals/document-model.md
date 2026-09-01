# The document model

One rule decides most of the design:

> Whatever the source wrote *between* two lexemes is a fact about the document. Record it
> verbatim. Never derive it from the two lexemes it sits between.

A document is not a sequence of nodes. It is a sequence of nodes and the gaps between them, and
the gaps carry as much of the author's intent as the nodes do: which column a key is aligned to,
whether a flow sequence has a trailing comma, whether the space before a `:` is one space or
seven, whether a tag sits on the node's line or the line above it. None of that follows from the
tree. A model that keeps only the tree has to invent the gaps on the way out, and an invention
that is right for one file is wrong for the next.

The failure mode is not a crash. The output is valid YAML, it holds the right data, and it has a
diff. `date   : 2001-01-23` comes back as `date:    2001-01-23`: the same columns, the wrong
spelling. That plausibility is what makes it easy to ship, and it is why the rule is written down
rather than assumed.

It is also measurable. Applying it is the single change that moved the `yaml-test-suite`
round-trip score from **209 of 308 to 302 of 308**, measured by running the same test at the two
commits:

```bash
cargo test -p yamluna-core --test proptest_roundtrip \
    yaml_test_suite_round_trips_byte_for_byte -- --nocapture
```

```text
# at 1e3b22d, before the rule
yaml-test-suite: 209/308 parsed cases round-trip byte-identically (0 of 308 did not parse, 0 known gaps)

# at 8b05b39, after it
yaml-test-suite: 302/308 parsed cases round-trip byte-identically (0 of 308 did not parse, 6 known gaps)
```

All 308 round-trip today. The six left over at `8b05b39` closed the same way, by recording one
more fact each.

## What had to be recorded

Each gap gets a field of its own, sized to the gap and not to a node.

| the gap | the field |
|---|---|
| between a key and its `:` | `Entry::colon` |
| between a `&anchor` or a tag and the node it decorates | `Node::anchor_at`, `Node::tag_at`, `Node::tag_first` |
| between a flow collection's own lexemes: brackets, commas, colons | `Node::flow_seps` |
| between a block scalar's `\|` or `>` header and the properties above it | `Node::header_at` |
| between the `%` lines (or a `...` that ends no document) and the `---` | `Document::directives_raw`, `Document::tags_before_version` |
| after the last lexeme of a line, and inside any line holding a TAB | `Document::line_space` |
| after the last lexeme of the stream | `Document::stream_tail` |
| inside a scalar's own lexeme | `Node::raw` |

Eight lines of YAML exercise most of that list at once. The document below has a `%YAML` line with
two spaces in it, a reserved directive with no model at all, a key three spaces from its colon, a
flow sequence with spaces and a trailing comma, a flow pair written with no brackets of its own, a
tag written ahead of its anchor, and no final line break:

=== "yamluna"

    ```python
    import io

    from yamluna import YAML

    src = (
        '%YAML  1.1\n'
        '%FOO bar\n'
        '---\n'
        'date   : 2001-01-23\n'
        'flow: [ 1 , 2 , ]\n'
        'pair: [a: 1]\n'
        'node: !!str &a v\n'
        'tail: [x]'
    )

    yaml = YAML()
    out = io.StringIO()
    yaml.dump_all(list(yaml.load_all(src)), out)
    print(out.getvalue() == src)
    print(out.getvalue())
    ```

    ```text
    True
    %YAML  1.1
    %FOO bar
    ---
    date   : 2001-01-23
    flow: [ 1 , 2 , ]
    pair: [a: 1]
    node: !!str &a v
    tail: [x]
    ```

=== "ruamel.yaml 0.19.1"

    ```python
    import io

    from ruamel.yaml import YAML

    src = (
        '%YAML  1.1\n'
        '%FOO bar\n'
        '---\n'
        'date   : 2001-01-23\n'
        'flow: [ 1 , 2 , ]\n'
        'pair: [a: 1]\n'
        'node: !!str &a v\n'
        'tail: [x]'
    )

    yaml = YAML()
    out = io.StringIO()
    yaml.dump_all(list(yaml.load_all(src)), out)
    print(out.getvalue() == src)
    print(out.getvalue())
    ```

    ```text
    False
    %YAML 1.1
    ---
    date: 2001-01-23
    flow: [1, 2]
    pair: [a: 1]
    node: &a !!str v
    tail: [x]
    ```

Every difference in the ruamel output is a gap that was reconstructed instead of recorded: the
spacing inside the directive, the reserved directive itself, the run before the colon, the three
runs inside the flow sequence, the order of the two node properties, and the line break at the end
of a stream that never had one.

## The edges of the stream

Four more facts belong to the stream rather than to any node, so the first and last documents
carry them: whether the source began with a BOM, whether it ended with a line break, the white
space at the end that no line break closes, and the lines whose white space no column can
reproduce (the ones holding a TAB, and the ones ending in space).

=== "yamluna"

    ```python
    import io

    from yamluna import YAML

    yaml = YAML()
    for name, src in [
        ('BOM', '\ufeffa: 1\n'),
        ('no final break', 'a: 1'),
        ('trailing space', 'a: 1\n   '),
        ('a TAB', 'a: [x,\ty]\n'),
    ]:
        out = io.StringIO()
        yaml.dump(yaml.load(src), out)
        print(f'{name:15} {src!r:16} -> {out.getvalue()!r:16} {out.getvalue() == src}')
    ```

    ```text
    BOM             '\ufeffa: 1\n'   -> '\ufeffa: 1\n'   True
    no final break  'a: 1'           -> 'a: 1'           True
    trailing space  'a: 1\n   '      -> 'a: 1\n   '      True
    a TAB           'a: [x,\ty]\n'   -> 'a: [x,\ty]\n'   True
    ```

=== "ruamel.yaml 0.19.1"

    ```python
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    for name, src in [
        ('BOM', '\ufeffa: 1\n'),
        ('no final break', 'a: 1'),
        ('trailing space', 'a: 1\n   '),
        ('a TAB', 'a: [x,\ty]\n'),
    ]:
        out = io.StringIO()
        yaml.dump(yaml.load(src), out)
        print(f'{name:15} {src!r:16} -> {out.getvalue()!r:16} {out.getvalue() == src}')
    ```

    ```text
    BOM             '\ufeffa: 1\n'   -> 'a: 1\n'         False
    no final break  'a: 1'           -> 'a: 1\n'         False
    trailing space  'a: 1\n   '      -> 'a: 1\n'         False
    a TAB           'a: [x,\ty]\n'   -> 'a: [x, y]\n'    False
    ```

## Two consequences

**A recorded gap is verbatim, not normalised.** `flow_seps` keeps the TAB in `[a<TAB>, b]`;
`directives_raw` keeps the two spaces in `%YAML  1.1`. The moment a recorder tidies its input it
has become a reconstructor with extra steps.

**Empty means "not recorded", and nothing else.** Editing a collection clears its `flow_seps`, and
the emitter lays it out from `EmitOptions` instead. There is no third state, no stale vector that
outlived the children it described, which is what stops a mutation from emitting punctuation for a
lexeme that is no longer written. The same holds one level up: a scalar you built has `raw` of
`None`, so the emitter chooses a style for it rather than echoing bytes that belonged to something
else.

!!! note

    `Node::flow_seps` records one run in front of each child and one in front of the closing
    bracket, so a recorded vector is `children + 1` long. A single pair written with no brackets
    of its own (`[a: 1]`) has no closing bracket to separate from and records exactly `children`
    runs. That length is the one fact that says the pair wrote no brackets.

## Trivia

A comment or a run of blank lines is a `Trivia`. Blank lines are a variant of their own rather
than newlines smuggled inside comment text, so "how many blank lines were there" has an answer.

Every node hangs trivia in four ordered slots:

| slot | holds |
|---|---|
| `before` | own-line trivia immediately preceding this node |
| `eol` | the end-of-line comment on the node's own line |
| `inner` | trivia between a collection's start token and its first child |
| `after` | a collection's trailing trivia, before its parent continues |

The rule the slots exist to serve is that **a comment is attached to the node it describes**, not
to a position. Seven lines of YAML fill all four:

```python
from yamluna._record import KIND_NAMES
from yamluna._yamluna import parse

src = (
    'ports:          # which ports\n'
    '  # the http one\n'
    '  - 80\n'
    '  # end of the list\n'
    '\n'
    '# about the key\n'
    'key: 1\n'
)

doc, = parse(src)
for i, node in enumerate(doc.nodes):
    print(i, KIND_NAMES[node.kind], repr(node.raw))
    for slot in ('before', 'eol', 'inner', 'after'):
        if getattr(node, slot):
            print('     ', slot, '=', getattr(node, slot))
```

```text
0 MAPPING None
1 SCALAR 'ports'
2 SEQUENCE None
      eol = Trivia(text='# which ports', own_line=False, col=16)
      inner = [Trivia(text='# the http one', own_line=True, col=2)]
      after = [Trivia(text='# end of the list', own_line=True, col=2)]
3 SCALAR '80'
4 SCALAR 'key'
      before = [Trivia(own_line=True, blank_lines=1), Trivia(text='# about the key', own_line=True)]
5 SCALAR '1'
```

Attachment is a merge along one axis, because comments and node spans share a single character
offset coordinate system. A comment after a node's last token on the same line is that node's
`eol`; for a mapping entry that node is the value, unless the comment falls between the key and
the `:`. An own-line comment goes to the `before` slot of the next node starting at or after it,
unless that node lies outside the collection currently open, in which case it is that collection's
`after`. Column decides where a run of comments sitting at the end of several nested blocks gets
cut. `col` on each comment is the 0-based column of the `#`, which is how the alignment of a
trailing comment survives.

## The store on the Python side

The four slots become `.ca` when the tree is built, and `.ca.items` holds one record per entry,
in ruamel's layout `[key_eol, key_pre, value_eol, value_post]`. That `items` is a **projection**
over a store the container owns, not the store itself:

* `CommentedMap`, `CommentedSet` and `CommentedKeyMap` key the store by the entry key, which for
  a `dict` is the entry's identity and never shifts.
* `CommentedSeq` and `CommentedKeySeq` keep a list of records parallel to the elements, so a
  record travels with its element through `insert`, `del`, `pop`, `sort`, `reverse` and slice
  assignment.

An index is a position, not an identity: it names a different element after every insertion and
every deletion, so a store keyed by one has to be rewritten on each mutation and is wrong the
moment a rewrite is missed. Keeping the records beside the elements means there is nothing to
rewrite.

That is not where ruamel goes wrong, though. It renumbers correctly in `insert` and
`__delsingleitem__`, and it keys mapping records by the key exactly as yamluna does. The bug is
one level down, in what a record holds. Load the same three-entry mapping in both libraries and
print `.ca.items`:

=== "yamluna"

    ```python
    from yamluna import YAML

    yaml = YAML()
    src = ('# about alpha\nalpha: 1   # eol alpha\n# about beta\nbeta: 2    # eol beta\n'
           '# about gamma\ngamma: 3   # eol gamma\n')
    m = yaml.load(src)
    for k, rec in m.ca.items.items():
        print(k, rec)
    ```

    ```text
    alpha [None, None, CommentToken('# eol alpha', col=11), None]
    beta [None, [CommentToken('# about beta\n', col=0)], CommentToken('# eol beta', col=11), None]
    gamma [None, [CommentToken('# about gamma\n', col=0)], CommentToken('# eol gamma', col=11), None]
    ```

=== "ruamel.yaml 0.19.1"

    ```python
    from ruamel.yaml import YAML

    yaml = YAML()
    src = ('# about alpha\nalpha: 1   # eol alpha\n# about beta\nbeta: 2    # eol beta\n'
           '# about gamma\ngamma: 3   # eol gamma\n')
    m = yaml.load(src)
    for k, rec in m.ca.items.items():
        print(k, rec)
    ```

    ```text
    alpha [None, None, CommentToken('# eol alpha\n# about beta\n', line: 1, col: 11), None]
    beta [None, None, CommentToken('# eol beta\n# about gamma\n', line: 3, col: 11), None]
    gamma [None, None, CommentToken('# eol gamma\n', line: 5, col: 11), None]
    ```

`# about beta` describes `beta`. ruamel stores it glued into `alpha`'s end-of-line token, so it
belongs to the entry above the one it describes, and every mutation that moves or removes an entry
moves the wrong comment with it. yamluna files it in `beta`'s own slot, and moving `beta`
therefore moves it:

=== "yamluna"

    ```python
    import io

    from yamluna import YAML

    yaml = YAML()
    src = ('# about alpha\nalpha: 1   # eol alpha\n# about beta\nbeta: 2    # eol beta\n'
           '# about gamma\ngamma: 3   # eol gamma\n')
    m = yaml.load(src)
    m.move_to_end('beta')
    yaml.dump(m, out := io.StringIO())
    print(out.getvalue(), end='')
    ```

    ```text
    # about alpha
    alpha: 1   # eol alpha
    # about gamma
    gamma: 3   # eol gamma
    # about beta
    beta: 2    # eol beta
    ```

=== "ruamel.yaml 0.19.1"

    ```python
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    src = ('# about alpha\nalpha: 1   # eol alpha\n# about beta\nbeta: 2    # eol beta\n'
           '# about gamma\ngamma: 3   # eol gamma\n')
    m = yaml.load(src)
    m.move_to_end('beta')
    yaml.dump(m, out := io.StringIO())
    print(out.getvalue(), end='')
    ```

    ```text
    # about alpha
    alpha: 1   # eol alpha
    # about beta
    gamma: 3   # eol gamma
    beta: 2    # eol beta
    # about gamma
    ```

Deletion is the same story in a sequence. `del seq[1]` takes the deleted element's comment with it
and leaves its neighbour's alone:

=== "yamluna"

    ```python
    import io

    from yamluna import YAML

    yaml = YAML()
    seq = yaml.load('- zero\n# about one\n- one\n# about two\n- two\n')
    del seq[1]
    yaml.dump(seq, out := io.StringIO())
    print(out.getvalue(), end='')
    ```

    ```text
    - zero
    # about two
    - two
    ```

=== "ruamel.yaml 0.19.1"

    ```python
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    seq = yaml.load('- zero\n# about one\n- one\n# about two\n- two\n')
    del seq[1]
    yaml.dump(seq, out := io.StringIO())
    print(out.getvalue(), end='')
    ```

    ```text
    - zero
    # about one
    - two
    ```

[Behaviour differences](../migrating/differences.md) has the full set of measured cases, A1 to A10.

## Two gaps, both pinned

The attachment rule is not fully honoured yet, and it is worth knowing where.

A comment indented into a nested block collection lands on that collection's `inner` slot rather
than on its first child's `before`. Every byte comes back either way; what differs is which
subtree the comment dies with. So `seq.insert(0, x)` still labels the new first element with the
old first element's comment, which is the ruamel defect the rest of this page says yamluna does not
have. Separately, an insertion can strand the `-` of the item preceding an own-line comment, giving
`-` and its value on two lines.

Both are pinned by strict `xfail` markers in `tests/test_mutation.py`, eight for the first and four
for the second, so the suite fails if either closes without being noticed. [Testing](testing.md)
covers the gates; the [changelog](../changelog.md) lists every known gap with its cause.
