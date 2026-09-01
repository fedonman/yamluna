# Behaviour differences

Everything on this page is a place where `ruamel.yaml` 0.19.1 does one thing and yamluna does
another, on purpose, because what ruamel does there is a defect. Each entry carries the input,
what ruamel produced, what yamluna produced, and a regression test in the repository so it
cannot come back.

Two ways to use it. If something moved when you ported, scan the headings: each one is written
as the symptom you would see, so the one that matches your bug is the entry to read. If you are
still deciding, read the four group introductions and skip the rest; they say what class of
thing each group is about.

Every output below was produced by running the code shown, against `ruamel.yaml` 0.19.1 and
yamluna 0.1.0, on CPython 3.13.12. Section numbers like §3.1 point into
[measured ruamel behaviour](../internals/ruamel-behaviour.md), which holds the raw session for
each one.

---

## A. Comments end up on the wrong node { #a-the-comment-model }

ruamel stores an own-line comment inside the *previous* sibling's end-of-line comment token. It
reads back correctly, so nothing looks wrong until you insert, delete, rename or reorder
something; then the comment stays with the node it was filed on rather than the node it
describes. A1 is the cause and A2 to A7 are what you see. This group is why the library exists.

### A1. An own-line comment is filed on the node above it { #a1-own-line-comments-are-stored-on-the-previous-sibling }

**ruamel.** (§1.4, §1.5) `.ca.items[key][2]` for a mapping, `[index][0]` for a sequence, holds
one `CommentToken` whose `.value` runs the node's end-of-line comment together with every
own-line comment below it. `# between alpha and beta` is physically inside `alpha`'s token, and
`beta` has no record at all:

```pycon
>>> SRC = "alpha: 1   # eol alpha\n# between alpha and beta\nbeta: 2\n"
>>> dict(ruamel.yaml.YAML().load(SRC).ca.items)
{'alpha': [None, None, CommentToken('# eol alpha\n# between alpha and beta\n', line: 0, col: 11), None]}
```

The first key is a special case: its own-line comments land in the *collection's*
`ca.comment[1]`, mixed in with comments that belong to the document.

**Why it is wrong.** A comment written above a node describes that node. Filing it on the
previous node makes every structural edit move it somewhere it does not belong, which is the
single root cause of A2 to A6. It also makes "what comment precedes this key?" unanswerable
without re-parsing another node's comment text, and "how many blank lines are here?"
unanswerable at all (A7).

**yamluna.** Trivia hangs off the node it describes, in four ordered slots keyed by **node
identity** rather than by index or key: `before`, `eol`, `inner`, `after`. `.ca` is a projection
over that store, so code that reads `.ca` still works, and it answers the question directly:

```pycon
>>> dict(yamluna.YAML().load(SRC).ca.items)
{'alpha': [None, None, CommentToken('# eol alpha', col=11), None],
 'beta': [None, [CommentToken('# between alpha and beta\n', col=0)], None, None]}
```

The [design contract](../internals/index.md) §2.1 is the normative version.

---

### A2. `seq.insert(0, x)` labels the new item with the old first item's comment { #a2-seqinsert-puts-the-following-items-comment-above-the-new-item }

Source used by A2, A3 and A6:

```yaml
# about one
- one
# about two
- two
# about three
- three
```

**ruamel.** (§3.1) `s.insert(0, 'zero')`:

```yaml
# about one          <- describes 'one'; now labels 'zero'
- zero
- one
# about two
- two
# about three
- three
```

`CommentedSeq.insert` does renumber `ca.items` correctly. The comment still lands wrong because
it never belonged to the item whose slot it was in (A1).

**Why it is wrong.** Inserting an element must not change what any existing comment describes.

**yamluna.** `insert` moves nothing: the comment is on the node it describes, and the new element
is a new node with empty trivia. `s.insert(3, 'extra')`, measured:

```yaml
# about one
- one
# about two
- two
# about three
- three
- extra
```

!!! warning "Two positions where this is not yet true"

    Both are pinned by xfails in
    [`tests/test_mutation.py`](https://github.com/qilimanjaro-tech/yamluna/blob/master/tests/test_mutation.py)
    that fail the suite if either closes unnoticed. `pytest tests/test_mutation.py` reports
    `158 passed, 12 xfailed`.

    **The first child (8 xfails).** An own-line comment above a collection's *first* child is
    filed on the collection's `inner` slot rather than on that child's `before`, so it stays put
    while the child it describes moves. That is the defect above, for one position out of n.
    Every byte still round-trips; only the ownership is wrong.

    **A stranded `-` (4 xfails).** Inserting immediately before an item that carries an own-line
    comment emits `-\n  value` where the source wrote `- value`. `s.insert(0, 'zero')` measures
    `'# about one\n- zero\n-\n  one\n# about two\n- two\n# about three\n- three\n'`, which shows
    both at once.

---

### A3. `del seq[i]` moves one comment and destroys another { #a3-del-seqi-orphans-the-deleted-items-comment-and-destroys-its-neighbours }

**ruamel.** (§3.2, §3.3) With the same source:

```text
del s[1]   ->  '# about one\n- one\n# about two\n- three\n'
del s[0]   ->  '# about one\n- two\n# about three\n- three\n'
```

`del s[1]` leaves `# about two` labelling `three`. `del s[0]` is worse: `# about one` is orphaned
and now mislabels `two`, while `# about two`, whose item survives, is **destroyed**, because
`__delsingleitem__` pops `ca.items[0]` and that token held both.

**Why it is wrong.** Deleting an element must delete exactly that element's comments and leave
every other comment where it was. ruamel does the opposite in both directions at once.

**yamluna.** Deleting a node deletes its trivia and nothing else. Measured:

```text
del s[1]   ->  '# about one\n- one\n# about three\n- three\n'
del s[0]   ->  '# about one\n# about two\n- two\n# about three\n- three\n'
```

`del s[1]` is the fix: `# about two` goes with `two` and `# about three` stays on `three`, where
ruamel destroys the first and moves the second. `del s[0]` still carries the
[A2 first-item caveat](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item):
`# about one` is filed on the sequence rather than on `one`, so deleting `one` leaves it behind
above `two`. The neighbour's comment is no longer destroyed, which is half the ruamel defect
gone; the orphan half remains, for the first position only.
`test_a3_deleting_the_first_item_takes_its_comment_and_leaves_the_rest` is the xfail that pins it.

Deleting the last item of a collection re-parents its `before` trivia onto the collection's
`after` slot rather than leaving it dangling as the final line of the document.

---

### A4. Deleting a key drops the wrong comment, and re-adding the key resurrects it { #a4-commentedmap__delitem__-never-touches-ca-so-comments-drift-and-resurrect }

**ruamel.** (§3.5) `CommentedMap.__delitem__` adjusts `_ok`, `_ref` and `merge_pos`, and never
looks at `self.ca`. Two failures fall out of that.

*Drift.* Source:

```yaml
# about alpha
alpha: 1   # eol alpha
# about beta
beta: 2    # eol beta
# about gamma
gamma: 3   # eol gamma
```

`m.pop('beta')`:

```yaml
# about alpha
alpha: 1   # eol alpha
# about beta        <- now labels gamma
gamma: 3   # eol gamma
```

`# about gamma` is destroyed, because it lived in beta's token, and `ca.items['beta']` survives.

*Resurrection.*

```pycon
>>> SRC = "alpha: 1\nbeta: 2  # secret comment about beta\ngamma: 3\n"
>>> m = load(SRC); del m['beta']
>>> dump(m)
'alpha: 1\ngamma: 3\n'
>>> m['beta'] = 'a brand new unrelated value'
>>> dump(m)
'alpha: 1\ngamma: 3\nbeta: a brand new unrelated value # secret comment about beta\n'
```

**Why it is wrong.** The stale entry is unbounded state growth in the commonest "delete a key"
path, and it re-attaches a deleted comment to an unrelated later value. That is not a formatting
glitch: it moves prose the user deleted onto data it was never about.

**yamluna.** Trivia is owned by the node. Removing an entry removes the node and its trivia, so
there is no side table to go stale and nothing can resurrect. The same two, measured:

```text
m.pop('beta')  ->  '# about alpha\nalpha: 1   # eol alpha\n# about gamma\ngamma: 3   # eol gamma\n'
re-adding beta ->  'alpha: 1\ngamma: 3\nbeta: a brand new unrelated value\n'
```

Deleting the *first* key carries the
[A2 caveat](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item).

---

### A5. Renaming or moving a key scatters its comments { #a5-key-rename-and-move_to_end-scatter-comments-across-the-document }

**ruamel.** (§3.6, §3.7) With the same six-line source. ruamel has no rename, so the rename is
spelled as a delete plus an insert, which cannot keep what the deleted node held:

```pycon
>>> m.insert(1, 'BETA', m.pop('beta'))
'# about alpha\nalpha: 1   # eol alpha\n# about beta\nBETA: 2\ngamma: 3   # eol gamma\n'
```

`# eol beta` is gone. And `m.move_to_end('alpha')`:

```yaml
# about alpha
beta: 2    # eol beta
# about gamma
gamma: 3   # eol gamma
alpha: 1   # eol alpha
# about beta        <- travelled to the end of the document
```

`# about alpha` stays at the top because it lives in `ca.comment[1]`; `# about beta` travels with
alpha because it lives in alpha's end-of-line token.

**Why it is wrong.** Reordering entries must reorder their comments with them, and renaming a key
must carry its comments to the new key.

**yamluna.** Reordering moves nodes, and trivia rides along because it hangs off the node.
Measured:

```text
m.rename('beta', 'BETA')
'# about alpha\nalpha: 1   # eol alpha\n# about beta\nBETA: 2    # eol beta\n# about gamma\ngamma: 3   # eol gamma\n'

m.move_to_end('beta')
'# about alpha\nalpha: 1   # eol alpha\n# about gamma\ngamma: 3   # eol gamma\n# about beta\nbeta: 2    # eol beta\n'
```

Both slots follow the key. `CommentedMap.rename` is a yamluna addition: it changes the key of an
existing node, so all four slots survive by construction where `pop` plus `insert` cannot.

Moving the *first* entry carries the
[A2 caveat](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item): its own-line
comment is filed on the mapping's `inner` slot, so `move_to_end('alpha')` moves `alpha` and its
end-of-line comment and leaves `# about alpha` at the top.
`test_a5_move_to_end_takes_the_entry_s_comments_with_it` and
`test_a5_move_to_front_reorders_the_comments_too` are the xfails that pin it.

---

### A6. `seq.reverse()` leaves every comment on its old index { #a6-commentedseqreverse-moves-nothing }

**ruamel.** (§3.4) `CommentedSeq` overrides `sort`, which remaps `ca.items`, and does not override
`reverse`, so `list.reverse` runs and the index-keyed comments stay put:

```text
input               '- a  # ca\n- b  # cb\n- c  # cc\n- d  # cd\n'
sort(reverse=True)  '- d  # cd\n- c  # cc\n- b  # cb\n- a  # ca\n'   correct
reverse()           '- d  # ca\n- c  # cb\n- b  # cc\n- a  # cd\n'   every comment wrong
```

**Why it is wrong.** Half the list API maintains the comment table and half does not, with no way
to tell which from the outside.

**yamluna.** There is no comment table to maintain, so every mutating list operation is correct by
construction, including the ones nobody remembered to override. `reverse()` on the three-item
source, measured:

```text
'# about one\n# about three\n- three\n# about two\n- two\n- one\n'
```

`three` and `two` bring their comments with them, which is the fix. `# about one` does not, for
the [A2 reason](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item): it is on the
sequence's `inner` slot, not on `one`, so it stays at the top and `one` arrives at the bottom
bare. `test_a6_reverse_carries_own_line_comments_too` is the xfail that pins it.

---

### A7. Blank lines are newlines hidden inside a comment { #a7-blank-lines-are-smuggled-inside-comment-text }

**ruamel.** (§2) A blank line is a bare `\n` inside a `CommentToken.value`, indistinguishable from
the newline that ends the node's own line:

```text
"a: 1\n\nb: 2\n"       ->  ca.items['a'][2] = CommentToken('\n\n')
"a: 1\n\n\nb: 2\n"     ->  ca.items['a'][2] = CommentToken('\n\n\n')
"a: 1\n\n# c\n\nd: 2\n" -> ca.items['a'][2] = CommentToken('\n\n# c\n\n')
```

**Why it is wrong.** "How many blank lines separate these two keys" has no answer that does not
involve counting newlines in another node's comment string, and the encoding is lossy at both ends
of the document, which is [B9](#b9-blank-lines-are-normalised-at-the-document-edges).

**yamluna.** A run of blank lines is a trivia kind of its own with a count on it. The count is a
number; leading and trailing runs are reproduced exactly.

---

### A8. Every dump appends one more element to `.ca.comment` { #a8-ca-is-mutated-by-dumping }

**ruamel.** (§10.3) `representer.py:744` does `node.comment.append(comment.end)`, where
`node.comment` is the object's own `ca.comment` list:

```text
after load : [None, [CommentToken('# lead\n')]]
after dump1: [None, [CommentToken('# lead\n')], []]
after dump2: [None, [CommentToken('# lead\n')], [], []]
after dump3: [None, [CommentToken('# lead\n')], [], [], []]
```

The output stays stable, but `.ca.comment` stops matching its documented `[post, [pre]]` shape, so
after the first dump:

```pycon
>>> post, pre = m.ca.comment
ValueError: too many values to unpack (expected 2)
```

**Why it is wrong.** Serialisation is a read. It must not modify the object graph, and it
certainly must not grow it without bound in a loop.

**yamluna.** Emission takes an immutable document. `.ca` is projected on read and never written
back through during a dump, so `.ca.comment` is `[None, [CommentToken('# lead\n', col=0)]]` after
the third dump as it was after the load, and `post, pre = m.ca.comment` keeps working.

---

### A9. `.ca.end` never loads, and only sometimes dumps { #a9-caend-is-write-only-and-yaml_end_comment_extend-silently-no-ops }

**ruamel.** (§1.6) The round-trip loader never populates `ca.end`; every input I could construct
leaves it `[]`, `...` markers and multi-document streams included. On the write side it is emitted
only when `ca.comment` already happens to be a list, because the append sits inside a bare
`except AttributeError: pass`:

```pycon
>>> m = load("a: 1\n")
>>> m.yaml_end_comment_extend([CommentToken('# the end\n', CommentMark(0))], clear=True)
>>> dump(m)
'a: 1\n'                              # silently dropped
>>> m = load("a: 1\n"); m.yaml_set_start_comment('start')
>>> m.yaml_end_comment_extend([CommentToken('# the end\n', CommentMark(0))], clear=True)
>>> dump(m)
'# start\na: 1\n# the end\n'
```

**Why it is wrong.** A documented API that works only when an unrelated attribute happens to be
set is worse than one that raises.

**yamluna.** Trailing document trivia is a property of the document, loaded and emitted. The first
call above produces `'a: 1\n# the end\n'`, and a comment after a `...` marker loads into `.ca.end`
and comes back:

```pycon
>>> m = yaml.load("a: 1\n...\n# after end marker\n")
>>> m.ca.end
[CommentToken('# after end marker\n', col=0)]
>>> yaml.dump(m)
'a: 1\n...\n# after end marker\n'
```

---

### A10. The `C_*` slot constants point at the wrong slots { #a10-the-c_-slot-constants-do-not-describe-caitems }

**ruamel.** (§1.2) `ruamel.yaml.comments` exports `C_VALUE_EOL=0`, `C_KEY_EOL=1`, `C_KEY_PRE=2`,
`C_VALUE_POST=3`, `C_VALUE_PRE=4`, `C_KEY_POST=5`. Under `typ='rt'` the actual layout of
`.ca.items[key]` is `[key_eol, pre_key_list, value_eol, pre_value_list]`. The constants belong to
the `comment_handling` scheme, which is only enabled for `typ='rtsc'`, and
`YAML().comment_handling` is `None`.

**Why it is wrong.** The only named constants for the slots name them wrongly for the only `typ`
this library supports.

**yamluna.** `yamluna.comments` exports constants that name the slots the projection actually has,
and only those:

```python
C_KEY_EOL = 0      # the key's end-of-line comment
C_KEY_PRE = 1      # the own-line comments above the key
C_VALUE_EOL = 2    # the value's end-of-line comment
C_VALUE_POST = 3   # the own-line comments below the value

C_ELEM_EOL, C_ELEM_PRE, C_ELEM_POST = C_KEY_EOL, C_KEY_PRE, C_VALUE_POST   # sequences
```

`C_VALUE_PRE` and `C_KEY_POST` are not here, because a `typ='rt'` record has no slot for them.
Code importing them by name gets `ImportError: cannot import name 'C_VALUE_PRE' from
'yamluna.comments'` rather than an index that silently points at the wrong slot.

---

## B. Something in the file does not come back { #b-round-trip-fidelity }

The contract yamluna holds itself to is the strict one: for a document you did not change,
`dump(load(text)) == text`, byte for byte. Every item below is an input where ruamel is not. The
[corpus harness](https://github.com/qilimanjaro-tech/yamluna/blob/master/tests/differential.py)
scores this over 41 hand-written files, one YAML concern each: ruamel 3 of 40, yamluna 40 of 40,
with `key-duplicate` scored on behaviour rather than bytes because no `dict`-backed API can write
two equal keys back.

Each entry reads `in` for the input, then what each library produced.

### B1. An anchor used once is dropped { #b1-anchors-referenced-fewer-than-twice-are-dropped }

**ruamel.** (§5.2)

```text
in       'base: &b\n  x: 1\nuse: *b\nother: &unused\n  y: 2\n'
ruamel   'base: &b\n  x: 1\nuse: *b\nother:\n  y: 2\n'
yamluna  'base: &b\n  x: 1\nuse: *b\nother: &unused\n  y: 2\n'
```

`&unused` survives on `.anchor.value`, but the serializer emits `&name` only for objects it has
seen more than once, unless `always_dump` is set.

**Why it is wrong.** The anchor is in the source text. A round trip that deletes source text is
not a round trip, and `&unused` is frequently a deliberate extension point that a later file or a
later edit refers to.

**yamluna.** An anchor is emitted whenever it is present, for the same reason a scalar's lexeme is:
it is source text. `Anchor.always_dump` stays on the object for API compatibility, but a loaded
anchor no longer needs it; it matters only for anchors you set on nodes you built.

---

### B2. `---` and `...` are dropped { #b2-and-are-dropped }

**ruamel.** (§9.5) `explicit_start` and `explicit_end` are emitter-global and default to `None`, so
a source that has the markers loses them.

```text
in       '---\na: 1\n'                yamluna  '---\na: 1\n'         ruamel  'a: 1\n'
in       '---\na: 1\n...\n'           yamluna  '---\na: 1\n...\n'    ruamel  'a: 1\n'
```

**Why it is wrong.** `---` is meaningful: it separates directives from content, and many tools
require it. Deleting it is a content change.

**yamluna.** The markers are per-document properties read from the source. The
`YAML.explicit_start` and `.explicit_end` settings override them when set, and are `None`, meaning
"keep what the source had", by default.

---

### B3. A comment after `...` is destroyed { #b3-comments-after-are-destroyed }

**ruamel.** (§10.2)

```text
in       'a: 1\n...\n# after end marker\n'
ruamel   'a: 1\n'
yamluna  'a: 1\n...\n# after end marker\n'
```

`'a: 1\n...\n# after\n...\n'` raises `ParserError` on load.

**Why it is wrong.** Silent data loss on valid input.

**yamluna.** Such comments are trailing document trivia, re-emitted after the `...`, and reachable
from [`.ca.end`](#a9-caend-is-write-only-and-yaml_end_comment_extend-silently-no-ops).

---

### B4. `? key` is dropped, and one input comes back unparseable { #b4-explicit-keys-key-are-dropped-sometimes-producing-unparseable-output }

**ruamel.** (§10.4) The `?` indicator is not preserved; the emitter re-derives it only when the key
is too long or is a block scalar.

```text
in       '? gamma\n: 3\n'             ruamel  'gamma: 3\n'                reparses
in       '? [a, b]\n: 1\n'            ruamel  '[a, b]: 1\n'               reparses
in       '? gamma  # c\n: 3\n'        ruamel  'gamma    # c\n: 3\n'       ParserError on reload
in       '[\n? foo\n bar : baz\n]\n'  ruamel  '[foo bar: baz]\n'          reparses, as one key
```

The third turns a valid document into one ruamel itself cannot read:
`ParserError: expected '<document start>', but found ('<block mapping start>',)`.

**Why it is wrong.** A load-then-dump cycle must never produce invalid YAML. This one does, on a
two-line input.

**yamluna.** The explicit-key indicator is part of the entry in the document model, carried across
the FFI and re-emitted. All four inputs above come back byte for byte, the third included, so the
cycle no longer produces YAML that cannot be re-read.

The fourth is the explicit key **inside a flow collection**, which was the last one to close. The
`?` is part of the separation run the collection wrote between its lexemes, so the flow emitter
writes it from there rather than deciding whether to.

---

### B5. Sequence indentation is re-decided, not reproduced { #b5-sequence-indentation-is-not-round-tripped }

**ruamel.** (§9.1, §10.5) `sequence_indent` and `sequence_dash_offset` are global emitter settings
with no per-node counterpart, so the source layout is discarded:

```text
in       'delta:\n  - x\n  - y\n'
ruamel   'delta:\n- x\n- y\n'
yamluna  'delta:\n  - x\n  - y\n'
```

An own-line comment inside that sequence keeps its *source* indentation while the items move to
column 0, so the comment ends up indented past the items it sits between.

**Why it is wrong.** The single most-reported ruamel annoyance, and the reason so much user code
carries a `yaml.indent(mapping=2, sequence=4, offset=2)` incantation that still only works for
files using that one style. A file with mixed sequence indentation cannot round-trip at all.
`tests/corpus/struct-seq-indent.yaml` mixes offset-0, offset-2 and offset-6 sequences in one
document; yamluna reproduces it byte for byte and no single ruamel setting does.

**yamluna.** Indentation is recorded per node, and an unmutated subtree is reproduced from its own
source. The `indent()` settings lay out nodes you created; see
[The port](index.md#layout-is-reproduced-not-re-decided) for what they do and do not reach.

---

### B6. `|+` at end of stream gains a `...` you did not write { #b6-at-end-of-stream-gains-a-spurious }

**ruamel.** (§7.6)

```text
in       'x: |+\n  a\n\n'
ruamel   'x: |+\n  a\n\n...\n'
yamluna  'x: |+\n  a\n\n'
```

**Why it is wrong.** Adding a document-end marker the user did not write is a content change. The
trailing blank lines of a keep-chomped scalar at end of stream are unambiguous without it.

**yamluna.** `...` is emitted only when the source had one or you asked for it.

---

### B7. `1_000.5` comes back as `01000.5` { #b7-1_0005-becomes-010005 }

**ruamel.** (§7.3) `ScalarFloat` never sets `_underscore`, so the digit separator is lost, while
`_width=7` is retained and zero-pads the result:

```text
in       'x: 1_000.5\n'
ruamel   'x: 01000.5\n'
yamluna  'x: 1_000.5\n'
```

**Why it is wrong.** Two bugs compounding: a lost separator and a fabricated leading zero.

**yamluna.** An unmutated scalar re-emits its source lexeme verbatim, so the question does not
arise. A `ScalarFloat` you construct honours an explicit `underscore=`.

---

### B8. `+12` comes back as `12` { #b8-a-leading-on-an-integer-is-dropped }

**ruamel.** (§7.3)

```text
in       'x: +12\n'
ruamel   'x: 12\n'
yamluna  'x: +12\n'
```

`ScalarFloat` records `_m_sign` and preserves `+2.5`; `ScalarInt` has no equivalent, and a plain
decimal int does not even become a `ScalarInt`.

**yamluna.** Lexeme preservation.

---

### B9. A leading blank line and a whitespace-only line are normalised { #b9-blank-lines-are-normalised-at-the-document-edges }

**ruamel.** (§2) The consequence of [A7](#a7-blank-lines-are-smuggled-inside-comment-text):

```text
in       '\n\n# lead\n\na: 1\n'       ruamel  '\n# lead\n\na: 1\n'    yamluna  unchanged
in       'a: 1\n   \nb: 2\n'          ruamel  'a: 1\n\nb: 2\n'        yamluna  unchanged
```

**yamluna.** The document's leading trivia holds the real count, and whitespace-only lines are
reproduced as written.

---

### B10. Every comment inside a flow collection is destroyed { #b10-comments-inside-flow-collections-are-destroyed }

**ruamel.** A plain load then dump, nothing mutated:

```text
in       'flow_map: {\n  # inside\n  x: 1,\n}\n'
ruamel   'flow_map: {x: 1}\n'
yamluna  'flow_map: {\n  # inside\n  x: 1,\n}\n'

in       'flow_seq: [\n  a,\n  # inside\n  b,\n]\n'
ruamel   'flow_seq: [a, b]\n'
yamluna  'flow_seq: [\n  a,\n  # inside\n  b,\n]\n'
```

Over the whole of
[`tests/corpus/comment-flow.yaml`](https://github.com/qilimanjaro-tech/yamluna/blob/master/tests/corpus/comment-flow.yaml)
that is nine comments inside flow collections, of which ruamel loses eight and moves the ninth
onto a line of its own; 22 lines come back as 8. The loader populates slot 1 of `.ca.items` for
own-line comments *only* inside a flow sequence, and the emitter re-lays out the collection from
the values alone, so whatever sat between two tokens is gone.

**Why it is wrong.** Losing a comment on an untouched round trip is the defect this library exists
not to have, and a flow collection is exactly where a comment is most likely to be explaining a
magic value.

**yamluna.** A comment between any two tokens of a flow collection is trivia in the slot that names
where it sat: `inner` for one after the opening bracket, `before` on the item it precedes, `eol`
for one after an item. The emitter writes it back there. The distinction that decides it is `inner`
versus `before`, because promoting a flow collection's `inner` trivia to `before` would push the
opening brace onto the next line, so the projection keeps them apart
([`_leading_is_before`](https://github.com/qilimanjaro-tech/yamluna/blob/master/python/yamluna/representer.py)).

Every comment survives, in place, in both the Rust and the Python path.
`corpus/comment-flow.yaml` round-trips byte for byte, brackets included, as do all 308
`yaml-test-suite` cases through the Rust core. Where the collection's own punctuation sat is a
separate fact from the comments, and it is recorded too: the separation each child and the closing
bracket was written behind is kept, so `[ 1 , 2 ]`, `[1, 2, ]`, `[a<TAB>, b]` and `{a: 1, b}` all
come back as written.

---

### B11. A block scalar's header is re-spelled { #b11-a-block-scalars-header-is-re-spelled-rather-than-reproduced }

**ruamel.**

```text
in       'keep: |+2\n\n    body\nlast: end\n'
ruamel   'keep: |2\n\n    body\nlast: end\n'

in       'keep: |\n\n  body\nlast: end\n'
ruamel   'keep: |2\n\n  body\nlast: end\n'

in       'chomp_then_explicit: |-2\n    body\nlast: end\n'
ruamel   'chomp_then_explicit: |2-\n    body\nlast: end\n'
```

yamluna returns all three unchanged. The header is rebuilt from the parsed scalar: a `+` with no
trailing blank line to keep is dropped, an explicit indentation indicator is added where the source
had none, and the indicator and chomping indicator are reordered into ruamel's preferred spelling.

**Why it is wrong.** None of these change the scalar's *value*: ruamel keeps `|+` when there are
trailing blank lines that depend on it, and both spellings of the indicator pair are legal. It is a
round-trip defect of the same kind as B5 and B9, where a file under version control gains a diff
nobody asked for.

**yamluna.** The header is part of the scalar's lexeme, so an untouched node re-emits it verbatim.
The lexeme runs from the `|` or `>` through the last body line, *including* the blank lines between
the header and the body, because those are content that `|+` keeps and the cooked value begins with
them; recording them as trivia as well would write them twice.

---

## C. Two classes with the same name silently become one { #c-the-tag-registry }

This is the part that is deliberately not ruamel-compatible, so it is the part of a port that needs
a decision rather than a check. [The port](index.md#the-registry-is-per-instance-and-tags-carry-a-namespace)
says what changes in your code; the two entries here say what is wrong with the thing being
replaced.

### C1. `register_class` keys the registry on the class name { #c1-register_class-keys-the-constructor-registry-on-the-class-name }

**ruamel.** (§8) `tag = getattr(cls, 'yaml_tag', '!' + cls.__name__)`. Two classes named `Circuit`
in two modules both register under `!Circuit`, the second overwrites the first, `add_constructor`
returns the displaced constructor and `register_class` throws it away. Measured, with
`libx.circuits.Circuit` and `liby.circuits.Circuit`:

```text
--- dumped ---
x: !Circuit
  qubits: 2
y: !Circuit
  n: 3
--- loaded back ---
  x constructed as liby.circuits.Circuit  __dict__= {'qubits': 2}
  y constructed as liby.circuits.Circuit  __dict__= {'n': 3}
  reverse registration order -> libx.circuits / libx.circuits
  back['x'].n -> AttributeError: 'Circuit' object has no attribute 'n'
```

`back['x']` is `liby`'s class holding `libx`'s attributes. The winner is decided purely by import
order.

**Why it is wrong.** Silent construction of the wrong class. No warning, no error, and the outcome
depends on something as incidental as the order of two `import` statements. A YAML file is not
self-describing: `!Circuit` means whatever was imported last.

**yamluna.** The registry key is `f"{cls.__module__}.{cls.__qualname__}"`, so a registration cannot
overwrite another. Wire identity is `tag:{source}/{tag_name}`, written with `%TAG` directives, and
a colliding `(source, tag_name)` pair is promoted to full module paths automatically. The same two
classes, measured:

```yaml
%TAG ! tag:libx/
%TAG !liby! tag:liby/
---
x: !Circuit
  qubits: 2
y: !liby!Circuit
  n: 3
```

Both come back as themselves, and the document round-trips. A bare `!Circuit` with more than one
candidate raises instead of guessing:

```text
ConstructorError: ambiguous tag '!Circuit': 2 registered candidates:
libx.circuits.Circuit (= tag:libx/Circuit), liby.circuits.Circuit (= tag:liby/Circuit);
yamluna will not guess. Add a %TAG directive naming the source (e.g. '%TAG ! tag:libx/')
or re-register with an explicit source= to disambiguate.
  in "<unicode string>", line 2, column 3
```

---

### C2. A registration on one `YAML()` leaks into every other { #c2-register_class-is-process-global-not-per-yaml }

**ruamel.** (§8.1) `add_representer` and `add_constructor` are classmethods that mutate a dict on
`RoundTripRepresenter` and `RoundTripConstructor`:

```pycon
>>> y1 = YAML(); y1.register_class(Thing)
>>> y2 = YAML()
>>> '!Thing' in y2.constructor.yaml_constructors
True
>>> Thing in y2.representer.yaml_representers
True
```

**Why it is wrong.** It turns C1 from one library's problem into any two libraries in one process.
A library that builds its own private `YAML()` still poisons, and is poisoned by, every other one.

**yamluna.** The registry belongs to the instance. A second `YAML()` has never heard of the class:

```pycon
>>> yamluna.register_class(Circuit)               # the module-level, shared registry
>>> YAML(registry=yamluna.default_registry).dump({'m': Circuit(qubits=2)})
'%TAG ! tag:libx/\n---\nm: !Circuit\n  qubits: 2\n'
>>> YAML().dump({'m': Circuit(qubits=2)})
RepresenterError: cannot represent an object: <libx.circuits.Circuit object at 0x...>;
register libx.circuits.Circuit with YAML.register_class() first
```

Sharing is opt-in, and it is the `registry=` argument that does it.

---

## D. Scalars, copies and comments that were stored but not written { #d-emitter-and-resolver-defects }

Smaller defects, each in a different corner: three in how a scalar is spelled back out, one in a
comment API that stores and never emits, one in duplicate keys, one in `copy()`, one in `.lc`.

### D1. `-0x1F` comes back as `!!int '0x-1F'` { #d1-negative-hexoctalbinary-integers-emit-an-invalid-literal }

**ruamel.** (§7.4)

```text
in       'x: -0x1F\n'      ruamel  "x: !!int '0x-1F'\n"     yamluna  'x: -0x1F\n'
in       'x: -0o17\n'      ruamel  "x: !!int '0o-17'\n"     yamluna  'x: -0o17\n'
in       'x: -0b101\n'     ruamel  "x: !!int '0b-101'\n"    yamluna  'x: -0b101\n'
```

The sign is placed *after* the base prefix and an explicit `!!int` tag is forced. ruamel reads its
own output back, correctly, as `-31`; no other implementation will, because `0x-1F` matches neither
the YAML 1.1 nor the 1.2 int production.

**Why it is wrong.** A round trip that produces YAML only one library can read defeats the point of
YAML.

**yamluna.** Lexeme preservation for loaded values, `-0x1f` for constructed ones.

---

### D2. `0X1F` does not load as an integer { #d2-0x1f-capital-x-does-not-resolve-as-an-integer }

**ruamel.** (§7.4) Capitalising the `x` changes the type:

```text
x: 0x1f  ->  HexInt     31
x: 0x1F  ->  HexCapsInt 31
x: 0X1f  ->  str '0X1f'
x: 0X1F  ->  str '0X1F'
```

**Why it is wrong.** The YAML 1.1 int production is `0x[0-9a-fA-F_]+`; whether the `x` is
capitalised is not part of it. `0X1F` is an integer in every other implementation, so the same file
means different things to ruamel and to everyone else.

**yamluna.** All four load as `HexInt`, and the file round-trips byte for byte, so the source
capitalisation comes back:

```pycon
>>> yaml.load('a: 0x1f\nb: 0x1F\nc: 0X1f\nd: 0X1F\n')
{'a': HexInt(0x1f), 'b': HexInt(0x1F), 'c': HexInt(0X1f), 'd': HexInt(0X1F)}
```

`0O` and `0B` prefixes resolve the same way.

---

### D3. `DecimalInt` cannot be dumped { #d3-decimalint-has-no-representer }

**ruamel.** (§7.4) `DecimalInt` is exported by `ruamel.yaml.scalarint` and documented as "needed if
anchor", and dumping one raises:

```pycon
>>> YAML().dump({'x': DecimalInt(7)}, stream)
RepresenterError: cannot represent an object: 7
```

**Why it is wrong.** A public, exported type that cannot be serialised.

**yamluna.** Every exported scalar type round-trips; the test corpus constructs one of each and
asserts the output. `DecimalInt` and `HexCapsInt` are the two ruamel names yamluna does not export
at all, for the reasons in [The port](index.md#absent-and-what-to-use-instead).

---

### D4. A comment stored with `after=` is never written { #d4-yaml_set_comment_before_after_keyafter-is-a-no-op-for-scalar-values }

**ruamel.** (§6) Slot 3 is only read when the value is a collection:

```pycon
>>> m = load("a: 1\nb: 2\n")
>>> m.yaml_set_comment_before_after_key('b', after='after b')
>>> m.ca.items['b'][3]
[CommentToken('# after b\n', col: 2)]        # stored
>>> dump(m)
'a: 1\nb: 2\n'                               # never emitted
```

With a collection value it works: `'a: 1\n# before b\nb:\n  # after b\n  c: 2\n'`.

**Why it is wrong.** The call succeeds, the data is stored, and the output is silently missing it.

**yamluna.** `after=` on a scalar-valued entry goes in that entry's `after` slot and is emitted on
the line following the value:

```pycon
>>> yaml.dump(m)
'a: 1\nb: 2\n  # after b\n'
```

A comment that is stored is always emitted; the store-then-silently-discard path does not exist.

---

### D5. `allow_duplicate_keys=True` keeps the first value and says nothing { #d5-allow_duplicate_keystrue-keeps-the-first-value-and-warns-about-nothing }

**ruamel.** (§9.7)

```pycon
>>> load("a: 1\nb: 2\na: 3\n", allow_duplicate_keys=True)
{'a': 1, 'b': 2}
>>> dump(...)
'a: 1\nb: 2\n'
```

No warning, and the second value disappears from the output.

**Why it is wrong.** First-wins contradicts both YAML's own last-wins convention where duplicates
are tolerated and Python's `dict` semantics, and doing it silently means a config file with an
accidental duplicate quietly ignores the value the author most likely meant. The dump then rewrites
the file with the duplicate deleted.

**yamluna.** The loader records every duplicate rather than merging silently. The default,
`allow_duplicate_keys = False`, raises as ruamel does, naming both positions:

```text
DuplicateKeyError: found duplicate key 'a' first at line 1, column 1, again at line 3, column 1
  in "<unicode string>", line 3, column 1
```

`True` warns, also naming both positions, and the last value wins:

```text
DuplicateKeyFutureWarning: duplicate key 'a' first at line 1, column 1, again at line 3,
column 1; the last value wins
loaded: {'a': 3, 'b': 2}
dumped: 'a: 3\nb: 2\n'
```

The losing entry is still gone from the output, and it is the one file in the corpus yamluna does
not round-trip byte for byte: `CommentedMap` is a `dict`, so two entries with equal keys are one
entry. That is scored on behaviour instead, which is why the corpus denominator is 40 and not 41.

---

### D6. `m.copy()` shares its comments with the original { #d6-copy_attributes-and-commentedmapcopy-share-the-comment-object }

**ruamel.** (§6) On a mapping that has comments:

```pycon
>>> m = load("alpha: 1  # eol alpha\nbeta: 2\n")
>>> m.copy().ca is m.ca
True
>>> t = CommentedMap(); m.copy_attributes(t); t.ca is m.ca
True
>>> c = m.copy(); c.yaml_add_eol_comment('added on the copy', 'beta')
>>> dump(m)                                   # the original, not the copy
'alpha: 1  # eol alpha\nbeta: 2   # added on the copy\n'
```

`copy_attributes(t, memo={})` and `copy.deepcopy(m)` both give `False`, so the aliasing is
`copy()`'s and plain `copy_attributes`'s alone.

**Why it is wrong.** A `copy()` that returns an object sharing mutable state with the original is a
bug factory: commenting the copy edits the original.

**yamluna.** `copy()` copies the trivia store, and `copy_attributes(t)` keeps ruamel's signature
while copying rather than aliasing. Both are `False`, and the same four lines leave the original
alone:

```text
original after commenting the copy:  'alpha: 1  # eol alpha\nbeta: 2\n'
the copy:                            'alpha: 1  # eol alpha\nbeta: 2   # added on the copy\n'
```

`memo=` continues to deep-copy.

---

### D7. `.lc.key(k)` raises `KeyError` for a key that exists { #d7-lc-is-a-load-time-snapshot-with-no-staleness-signal }

**ruamel.** (§4) After any mutation `.lc` still reports the source positions, and a newly inserted
key is simply absent from `.lc.data`:

```pycon
>>> d = load("alpha: 1\nbeta: 2\n"); d.insert(0, 'zzz', 1)
>>> d.lc.key('alpha')
(0, 0)                    # 'alpha' is now on line 1
>>> 'zzz' in d.lc.data
False
>>> d.lc.key('zzz')
KeyError: 'zzz'
```

**Why it is wrong.** Not that the positions are stale, because recomputing them on every edit would
be absurd, but that there is no way to tell. `.lc.key(k)` raising `KeyError` for a key that
demonstrably exists in the mapping is the only signal, and it reads as a bug in the caller rather
than "this node has no recorded source position".

**yamluna.** `.lc` keeps ruamel's semantics: 0-based, a load-time snapshot, not maintained across
edits. The one change is that `.lc.key(k)`, `.lc.value(k)` and `.lc.item(i)` return `None` for a
node with no recorded position, the same as `.lc` on a node that was never loaded, instead of
raising:

```pycon
>>> d.lc.key('alpha'), d.lc.key('zzz')
((0, 0), None)
```

---

## Things that look different and are not { #not-divergences }

Recorded so the differential harness does not re-litigate them, and so you do not go looking.

| | |
|---|---|
| `.lc` is **0-based** for both line and column | matches the scanner fork; yamluna keeps it |
| `Mark.index` and `Mark.pointer` are **character** offsets, not byte offsets | same; yamluna keeps character offsets at the Python surface |
| an alias resolves to **object identity**, so `d['use'] is d['base']` | measured `True` in both; the core models an alias as a distinct node kind and the Python surface keeps the identity, because user code depends on it |
| `Format.flow_style()` beats `YAML.default_flow_style` for a loaded container | correct, and yamluna does the same |
| a duplicate `<<` merge key is always an error, `allow_duplicate_keys=True` included | correct per the merge-key spec; yamluna keeps it |
| a plain string, decimal int or bool loads as a builtin `str`, `int` or `bool`, not `PlainScalarString` / `DecimalInt` / `ScalarBoolean` | keeping this matters for `isinstance` checks in user code; yamluna preserves the original lexeme in the document model instead of in the Python object |
