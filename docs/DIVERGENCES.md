# Divergences from ruamel.yaml 0.19.1

Where yamluna deliberately does not reproduce `ruamel.yaml`'s `typ='rt'` behaviour.

Every entry is a defect measured against `ruamel.yaml==0.19.1`; the repro is in
`docs/RUAMEL-BEHAVIOR.md` at the section named. The differential harness
(`DESIGN.md` §6.3) must classify each of these as "expected divergence", and each one
carries a regression test so it cannot come back.

Entries are grouped: **A** — the comment model (the reason this library exists);
**B** — round-trip fidelity; **C** — the tag registry; **D** — smaller emitter and
resolver bugs.

---

## A. The comment model

### A1. Own-line comments are stored on the previous sibling

**ruamel.** `.ca.items[key][2]` (mapping) / `[index][0]` (sequence) holds a *single*
`CommentToken` whose `.value` concatenates the node's end-of-line comment with every
own-line comment that follows it, up to the next node. `# between alpha and beta` is
physically inside `alpha`'s token:

```
m.ca.items['alpha'] = [None, None, CT('# eol after alpha value\n# between alpha and beta\n'), None]
```

A pre-key comment for any key but the first has no representation of its own; the first
key's pre-comments land in the *collection's* `ca.comment[1]`, mixed in with comments that
belong to the document. (RUAMEL-BEHAVIOR §1.4, §1.5)

**Why it is wrong.** A comment written above a node describes that node. Storing it on the
previous node makes every structural edit move it to the wrong place — that is the single
root cause of A2 through A6. It also makes the question "what comment precedes this key?"
unanswerable without re-parsing another node's comment text, and it makes "how many blank
lines are here?" unanswerable at all (A7).

**yamluna.** Trivia is attached to the node it describes, in four ordered slots keyed by
**node identity**, not by index or key (`DESIGN.md` §2.1): `before`, `eol`, `inner`,
`after`. A `Trivia::Comment` carries `own_line` and `col`. `.ca` is a *projection* over
that store, so code that reads `.ca` still works while mutation stays correct.

---

### A2. `seq.insert()` puts the following item's comment above the new item

**ruamel.** (RUAMEL-BEHAVIOR §3.1)

```python
SRC = "# about one\n- one\n# about two\n- two\n# about three\n- three\n"
s = load(SRC); s.insert(0, 'zero'); print(dump(s))
```
```
# about one          <- describes 'one'; now labels 'zero'
- zero
- one
# about two
- two
```

`CommentedSeq.insert` *does* renumber `ca.items` correctly. The comment still ends up wrong
because it never belonged to the item whose slot it lives in (A1).

**Why it is wrong.** Inserting an element must not change what any existing comment
describes.

**yamluna.** `insert` moves nothing: the comment is on the node it describes, and the new
element is a new node with empty trivia. `s.insert(3, 'extra')` on the same source, measured:

```
# about one
- one
# about two
- two
# about three
- three
- extra
```

Every comment is still above the item it was written above, where ruamel relabels one of them.

> **Two positions where this is not yet true**, both pinned by xfails in
> `tests/test_mutation.py` that fail the suite if either closes unnoticed.
>
> **The first child (8 xfails).** An own-line comment above a collection's *first* child is
> filed on the collection's `inner` slot rather than on that child's `before`
> (`loader.rs::take_before`), so it stays put while the child it describes moves — exactly the
> defect above, for one position out of n. DESIGN §2.2 rule 2 names `before` as the target and
> says so. Every byte still round-trips; only the ownership is wrong.
>
> **A stranded `-` (4 xfails).** Inserting immediately before an item that carries an own-line
> comment emits `-\n  value` where the source wrote `- value`. `s.insert(0, 'zero')` measures
> `'# about one\n- zero\n-\n  one\n# about two\n- two\n# about three\n- three\n'`, which
> shows both defects at once.

---

### A3. `del seq[i]` orphans the deleted item's comment and destroys its neighbour's

**ruamel.** (RUAMEL-BEHAVIOR §3.2, §3.3) With the same source:

```python
s = load(SRC); del s[1]     # -> '# about one\n- one\n# about two\n- three\n'
s = load(SRC); del s[0]     # -> '# about one\n- two\n# about three\n- three\n'
```

`del s[1]` leaves `# about two` labelling `three`. `del s[0]` is worse: `# about one`
(orphaned) survives and mislabels `two`, while `# about two` (whose item survives) is
**destroyed**, because `__delsingleitem__` pops `ca.items[0]` and that token held both.

**Why it is wrong.** Deleting an element must delete exactly that element's comments and
leave every other comment where it was. ruamel does the opposite in both directions
simultaneously.

**yamluna.** Deleting a node deletes its trivia and nothing else. Measured:

```
del s[1]  ->  '# about one\n- one\n# about three\n- three\n'
del s[0]  ->  '# about one\n# about two\n- two\n# about three\n- three\n'
```

`del s[1]` is the fix: `# about two` goes with `two`, and `# about three` stays on `three`,
where ruamel destroys the first and moves the second.

`del s[0]` still carries the [A2](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item)
first-item caveat: `# about one` is filed on the *collection's* `inner` slot rather than on
`one`, so deleting `one` leaves it behind, above `two`. The neighbour's comment is no longer
destroyed — that half of the ruamel defect is gone — but the orphan half is not, for the first
position only. `test_a3_deleting_the_first_item` is the xfail that pins it.

Deleting the last item of a collection re-parents its `before` trivia onto the collection's
`after` slot rather than leaving it dangling as the final line of the document.

---

### A4. `CommentedMap.__delitem__` never touches `.ca`, so comments drift *and* resurrect

**ruamel.** (RUAMEL-BEHAVIOR §3.5) `CommentedMap.__delitem__` adjusts `_ok`, `_ref` and
`merge_pos` and never looks at `self.ca`. Two failures fall out.

*Drift:*
```python
SRC = "# about alpha\nalpha: 1   # eol alpha\n# about beta\nbeta: 2    # eol beta\n# about gamma\ngamma: 3   # eol gamma\n"
m = load(SRC); m.pop('beta')
```
```
# about alpha
alpha: 1   # eol alpha
# about beta        <- now labels gamma
gamma: 3   # eol gamma
```
`# about gamma` is destroyed (it lived in beta's token) and `ca.items['beta']` survives.

*Resurrection:*
```python
SRC = "alpha: 1\nbeta: 2  # secret comment about beta\ngamma: 3\n"
m = load(SRC); del m['beta']
dump(m)                                   # 'alpha: 1\ngamma: 3\n'
m['beta'] = 'a brand new unrelated value'
dump(m)
# 'alpha: 1\ngamma: 3\nbeta: a brand new unrelated value # secret comment about beta\n'
```

**Why it is wrong.** The stale entry is unbounded state growth in the common
"delete a key" path, and it re-attaches a deleted comment to an unrelated later value.
That is not a formatting glitch: it moves prose the user deleted onto data it was never
about.

**yamluna.** Trivia is owned by the node. Removing an entry removes the node and its
trivia; there is no side table to go stale, so nothing can resurrect.


Same first-item caveat as [A2](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item).
---

### A5. Key rename and `move_to_end` scatter comments across the document

**ruamel.** (RUAMEL-BEHAVIOR §3.6, §3.7) With the same six-line source:

```python
m = load(SRC); m.insert(1, 'BETA', m.pop('beta'))
# '# about alpha\nalpha: 1   # eol alpha\n# about beta\nBETA: 2\ngamma: 3   # eol gamma\n'
#                                                      ^ '# eol beta' destroyed

m = load(SRC); m.move_to_end('alpha')
# beta: 2    # eol beta
# # about gamma
# gamma: 3   # eol gamma
# alpha: 1   # eol alpha
# # about beta        <- travelled to the end of the document
```

`# about alpha` stays at the top because it lives in `ca.comment[1]`; `# about beta`
travels with alpha because it lives in alpha's EOL token.

**Why it is wrong.** Reordering entries must reorder their comments with them. Renaming a
key must carry its comments to the new key.

**yamluna.** Reordering moves nodes; trivia rides along because it hangs off the node.
Measured:

```python
m.rename('beta', 'BETA')
# '# about alpha\nalpha: 1   # eol alpha\n# about beta\nBETA: 2   # eol beta\n# about gamma\ngamma: 3   # eol gamma\n'
m.move_to_end('beta')
# '# about alpha\nalpha: 1   # eol alpha\n# about gamma\ngamma: 3   # eol gamma\n# about beta\nbeta: 2   # eol beta\n'
```

Both slots follow the key. `rename` is a yamluna addition — ruamel has no rename at all, which
is why its column above spells one as `insert(1, 'BETA', m.pop('beta'))`, a delete plus an
insert that cannot keep what the deleted node held. `rename` mutates the key of an existing
node, so all four slots survive by construction.

Moving the *first* entry carries the
[A2](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item) caveat: its own-line
comment is filed on the mapping's `inner` slot, so `move_to_end('alpha')` moves `alpha` and its
end-of-line comment and leaves `# about alpha` at the top. `test_a5_move_to_end` and
`test_a5_move_to_front` are the xfails that pin it.

---

### A6. `CommentedSeq.reverse()` moves nothing

**ruamel.** (RUAMEL-BEHAVIOR §3.4) `CommentedSeq` overrides `sort` (which remaps
`ca.items`) but not `reverse`, so `list.reverse` runs and leaves the index-keyed comments
untouched:

```
"- a  # ca\n- b  # cb\n- c  # cc\n- d  # cd\n"
sort(reverse=True) -> '- d  # cd\n- c  # cc\n- b  # cb\n- a  # ca\n'   correct
reverse()          -> '- d  # ca\n- c  # cb\n- b  # cc\n- a  # cd\n'   every comment wrong
```

**Why it is wrong.** Half the list API maintains the comment table and half does not, with
no way to tell which from the outside.

**yamluna.** There is no comment table to maintain, so every mutating list operation is
correct by construction — including the ones nobody remembered to override. `reverse()` on the
six-line source above, measured:

```
'# about one\n# about three\n- three\n# about two\n- two\n- one\n'
```

`three` and `two` bring their comments with them, which is the fix. `# about one` does not,
for the [A2](#a2-seqinsert-puts-the-following-items-comment-above-the-new-item) reason — it is
on the sequence's `inner` slot, not on `one` — so it stays at the top and `one` arrives at the
bottom bare. `test_a6_reverse` is the xfail that pins it.

---

### A7. Blank lines are smuggled inside comment text

**ruamel.** (RUAMEL-BEHAVIOR §2) A blank line is a bare `\n` inside a
`CommentToken.value`, indistinguishable from the newline that terminates the node's own
line:

```
"a: 1\n\nb: 2\n"      -> ca.items['a'][2] = CT('\n\n')
"a: 1\n\n\nb: 2\n"    -> ca.items['a'][2] = CT('\n\n\n')
"a: 1\n\n# c\n\nd: 2" -> ca.items['a'][2] = CT('\n\n# c\n\n')
```

Consequences measured: two leading blank lines collapse to one
(`"\n\n# lead\n\na: 1\n"` → `"\n# lead\n\na: 1\n"`), and a blank line containing spaces is
normalised (`"a: 1\n   \nb: 2\n"` → `"a: 1\n\nb: 2\n"`).

**Why it is wrong.** "How many blank lines separate these two keys" has no answer that does
not involve counting newlines in another node's comment string, and the encoding is lossy
at both ends of the document.

**yamluna.** `Trivia::BlankLines(n)` is a first-class trivia kind (`DESIGN.md` §2.1). The
count is a number; leading and trailing runs are preserved exactly.

---

### A8. `.ca` is mutated by dumping

**ruamel.** (RUAMEL-BEHAVIOR §10.3) `representer.py:744` does
`node.comment.append(comment.end)` where `node.comment` is the object's own
`ca.comment` list. Every dump appends one more element:

```
after load : [None, [CT('# lead\n')]]
after dump1: [None, [CT('# lead\n')], []]
after dump2: [None, [CT('# lead\n')], [], []]
after dump3: [None, [CT('# lead\n')], [], [], []]
```

Output is stable, but `.ca.comment` no longer matches its documented `[post, [pre]]` shape,
so `post, pre = obj.ca.comment` raises after the first dump.

**Why it is wrong.** Serialisation is a read. It must not modify the object graph, and it
certainly must not grow it without bound in a loop.

**yamluna.** Emission takes `&Document`. The Python layer projects `.ca` on read and never
writes back through it during a dump.

---

### A9. `.ca.end` is write-only, and `yaml_end_comment_extend` silently no-ops

**ruamel.** (RUAMEL-BEHAVIOR §1.6) The rt loader never populates `ca.end` — every input I
could construct leaves it `[]`, including `...` markers and multi-document streams. On the
write side it is emitted only when `ca.comment` already happens to be a list, because the
append is inside a bare `except AttributeError: pass`:

```python
m = load("a: 1\n")
m.yaml_end_comment_extend([CommentToken('# the end\n', CommentMark(0))], clear=True)
dump(m)                                    # 'a: 1\n'          <- silently dropped
m = load("a: 1\n"); m.yaml_set_start_comment('start')
m.yaml_end_comment_extend([CommentToken('# the end\n', CommentMark(0))], clear=True)
dump(m)                                    # '# start\na: 1\n# the end\n'
```

**Why it is wrong.** A documented API that works only when an unrelated attribute happens
to be set is worse than one that raises.

**yamluna.** Trailing document trivia goes to `Document::trailing` (`DESIGN.md` §2.2 rule 4)
and is both loaded and emitted. `.ca.end` projects that store and round-trips.

---

### A10. The `C_*` slot constants do not describe `.ca.items`

**ruamel.** `ruamel.yaml.comments` exports `C_VALUE_EOL=0, C_KEY_EOL=1, C_KEY_PRE=2,
C_VALUE_POST=3, C_VALUE_PRE=4, C_KEY_POST=5`. Under `typ='rt'` the actual layout is
`[key_eol, pre_key_list, value_eol, pre_value_list]` — the constants belong to the
`comment_handling` scheme, which is only enabled for `typ='rtsc'`
(`YAML().comment_handling is None`). (RUAMEL-BEHAVIOR §1.2)

**Why it is wrong.** The only named constants for the slots name them wrongly for the only
`typ` this library supports.

**yamluna.** `yamluna.comments` exports constants that name the slots the projection
actually has, and only those:

```python
C_KEY_EOL = 0      # the key's end-of-line comment
C_KEY_PRE = 1      # the own-line comments above the key
C_VALUE_EOL = 2    # the value's end-of-line comment
C_VALUE_POST = 3   # the own-line comments below the value

C_ELEM_EOL, C_ELEM_PRE, C_ELEM_POST = C_KEY_EOL, C_KEY_PRE, C_VALUE_POST   # sequences
```

`C_VALUE_PRE` and `C_KEY_POST` do not exist here: they belong to ruamel's `rtsc`
`comment_handling` scheme, and there is no slot for them in a `typ='rt'` record. Any code
importing them by name gets an `ImportError` rather than an index that silently points at the
wrong slot.

---

## B. Round-trip fidelity

`DESIGN.md` §6.2 is stricter than ruamel: for an unmutated document, `load → dump` is
byte-identical to the input. Each item below is an input where ruamel is not.

### B1. Anchors referenced fewer than twice are dropped

**ruamel.** (RUAMEL-BEHAVIOR §5.2)

```
"base: &b\n  x: 1\nuse: *b\nother: &unused\n  y: 2\n"
  ->  "base: &b\n  x: 1\nuse: *b\nother:\n  y: 2\n"
```

`&unused` survives on `.anchor.value` but the serializer emits `&name` only for objects it
has seen more than once, unless `always_dump` is set.

**Why it is wrong.** The anchor is in the source text. A round trip that deletes source
text is not a round trip — and `&unused` is frequently a deliberate extension point that a
later file or a later edit refers to.

**yamluna.** `Node.anchor` is emitted whenever it is present, for the same reason `raw` is —
it is source text. `Anchor.always_dump` stays on the object for API compatibility, but a
loaded anchor no longer needs it; it only matters for anchors the user sets on
newly-constructed nodes.

---

### B2. `---` and `...` are dropped

**ruamel.** (RUAMEL-BEHAVIOR §9.5) `explicit_start` / `explicit_end` are emitter-global and
default to `None`; a source that has the markers loses them.

```
'---\na: 1\n'      -> 'a: 1\n'
'---\na: 1\n...\n' -> 'a: 1\n'
```

**Why it is wrong.** `---` is meaningful (it separates directives from content, and many
tools require it). Deleting it is a content change.

**yamluna.** `Document::explicit_start` / `explicit_end` are per-document properties read
from the source. The `YAML.explicit_start` / `.explicit_end` settings override them when
set, and are `None` (= keep what the source had) by default.

---

### B3. Comments after `...` are destroyed

**ruamel.** (RUAMEL-BEHAVIOR §10.2)

```
'a: 1\n...\n# after end marker\n'  ->  'a: 1\n'
```

and `'a: 1\n...\n# after\n...\n'` raises `ParserError` on load.

**Why it is wrong.** Silent data loss on valid input.

**yamluna.** Such comments go to `Document::trailing` and are re-emitted after the `...`.

---

### B4. Explicit keys (`? key`) are dropped, sometimes producing unparseable output

**ruamel.** (RUAMEL-BEHAVIOR §10.4) The `?` indicator is not preserved; the emitter
re-derives it only when the key is too long or is a block scalar.

```
'? gamma\n: 3\n'              -> 'gamma: 3\n'                  reparses
'? [a, b]\n: 1\n'             -> '[a, b]: 1\n'                 reparses
'? gamma  # c\n: 3\n'         -> 'gamma    # c\n: 3\n'         ParserError on reload
```

The last case turns a valid document into one that ruamel itself cannot read:
`ParserError: expected '<document start>', but found ('<block mapping start>',)`.

**Why it is wrong.** A load→dump cycle must never produce invalid YAML. This one does, on a
four-line input.

**yamluna.** The explicit-key indicator is `Entry::explicit` in the document model, carried
across the FFI as `Node.explicit`, and re-emitted. All three inputs above come back byte-for-byte,
the third included, so the load→dump cycle no longer produces YAML that cannot be re-read.

One exception, pinned: an explicit key **inside a flow collection** loses its `?` —
`'[\n? foo\n bar : baz\n]\n'` comes back as `'[\n{ foo\n bar : baz,\n}\n]\n'`. The indicator is
recorded; the flow emitter does not write it. That is `CT4Q` in `KNOWN_GAPS`
(`crates/yamluna-core/tests/proptest_roundtrip.rs`).

---

### B5. Sequence indentation is not round-tripped

**ruamel.** (RUAMEL-BEHAVIOR §9.1, §10.5) `sequence_indent` / `sequence_dash_offset` are
global emitter settings with no per-node counterpart, so the source layout is discarded:

```
'delta:\n  - x\n  - y\n'  ->  'delta:\n- x\n- y\n'
```

Worse, an own-line comment inside that sequence keeps its *source* indentation while the
items move to column 0, producing output where the comment is indented past the items it
sits between.

**Why it is wrong.** The single most-reported ruamel annoyance, and the reason so much user
code carries a `yaml.indent(mapping=2, sequence=4, offset=2)` incantation that still only
works for files that happen to use that one style. A file with mixed sequence indentation
cannot round-trip at all.

**yamluna.** Indentation is recorded per node (`Node.pos`, plus the emitter's verbatim
`raw` reproduction for unmutated subtrees, `DESIGN.md` §2.4). The `indent()` settings apply
only to nodes the user created or restyled.

---

### B6. `|+` at end of stream gains a spurious `...`

**ruamel.** (RUAMEL-BEHAVIOR §7.6)

```
'x: |+\n  a\n\n'  ->  'x: |+\n  a\n\n...\n'
```

**Why it is wrong.** Adding a document-end marker the user did not write is a content
change. The trailing blank lines of a keep-chomped scalar at end of stream are
unambiguous without it.

**yamluna.** Emits `...` only when `Document::explicit_end` says the source had one, or the
user asked for it.

---

### B7. `1_000.5` becomes `01000.5`

**ruamel.** (RUAMEL-BEHAVIOR §7.3) `ScalarFloat` never sets `_underscore`, so the digit
separator is lost, while `_width=7` is retained and zero-pads the result:

```
'x: 1_000.5\n'  ->  'x: 01000.5\n'
```

**Why it is wrong.** Two bugs compounding: a lost separator and a fabricated leading zero.

**yamluna.** An unmutated scalar re-emits its `raw` lexeme verbatim; the question does not
arise. A *constructed* `ScalarFloat` honours an explicit `underscore=`.

---

### B8. A leading `+` on an integer is dropped

**ruamel.** (RUAMEL-BEHAVIOR §7.3) `'x: +12\n'` → `'x: 12\n'`. `ScalarFloat` records
`_m_sign` and preserves `+2.5`; `ScalarInt` has no equivalent, and a plain decimal int does
not even become a `ScalarInt`.

**yamluna.** `raw` preservation.

---

### B9. Blank lines are normalised at the document edges

**ruamel.** (RUAMEL-BEHAVIOR §2) `"\n\n# lead\n\na: 1\n"` → `"\n# lead\n\na: 1\n"`;
`"a: 1\n   \nb: 2\n"` → `"a: 1\n\nb: 2\n"`.

**yamluna.** `Document::leading` holds a `Trivia::BlankLines(n)` with the real `n`;
whitespace-only lines are reproduced as written.

---

### B10. Comments inside flow collections are destroyed

**ruamel.** (`tests/corpus/comment-flow.yaml`; `differential.py --ruamel comment-flow`)
A plain `load` → `dump`, nothing mutated:

```
'flow_map: {\n  # inside\n  x: 1,\n}\n'   ->  'flow_map: {x: 1}\n'
'flow_seq: [\n  a,\n  # inside\n  b,\n]\n'  ->  'flow_seq: [a, b]\n'
```

Over the whole corpus file that is 2 own-line and 6 end-of-line comments lost, and 22 lines
collapsed to 8. The loader populates slot 1 of `.ca.items` for own-line comments *only*
inside a flow sequence (RUAMEL-BEHAVIOR §1, the `.ca.items` slot table), and the emitter re-lays
out the collection from the values alone, so whatever was between two tokens is gone.

**Why it is wrong.** Losing a comment on an untouched round trip is the defect this library
exists to not have, and a flow collection is exactly where a comment is most likely to be
explaining a magic value.

**yamluna.** A comment between any two tokens of a flow collection is a `Trivia` in the slot
that names where it sat — `inner` for one after the opening bracket, `before` on the item it
precedes, `eol` for one after an item — and the emitter writes it back there. The
distinction that decides this is `inner` versus `before`: promoting a flow collection's
`inner` trivia to `before` would push the opening brace onto the next line, so the
projection keeps them apart (`_leading_is_before` in `python/yamluna/representer.py`).

Every comment survives, in both the Rust and the Python path;
`corpus/comment-flow.yaml` round-trips byte-for-byte, brackets included. *In place* has two
known exceptions, both in `yaml-test-suite` and both pinned by `KNOWN_GAPS` in
`crates/yamluna-core/tests/proptest_roundtrip.rs`: a comment that splits the separation run
between two lexemes (`6HB6`, `7TMG`) comes back a line away from where it sat, because the run
is one string per gap with the comments taken out of it and cannot be put back *around* one.
Nothing is lost; the placement is. Where the collection's
own punctuation sat is a separate fact from the comments, and it is recorded: `Node.flow_seps`
holds the separation the source wrote in front of each child and in front of the closing
bracket, and the record classes carry it across the FFI, so `[ 1 , 2 ]`, `[1, 2, ]`, `[a<TAB>,
b]` and `{a: 1, b}` all come back as written.

---

### B11. A block scalar's header is re-spelled rather than reproduced

**ruamel.** (`tests/corpus/block-scalar-indent.yaml`)

```
'keep: |+2\n\n    body\nlast: end\n'  ->  'keep: |2\n\n    body\nlast: end\n'
'keep: |\n\n  body\nlast: end\n'      ->  'keep: |2\n\n  body\nlast: end\n'
'chomp_then_explicit: |-2\n...'       ->  'chomp_then_explicit: |2-\n...'
```

The header is rebuilt from the parsed scalar: a `+` with no trailing blank line to keep is
dropped, an explicit indentation indicator is added where the source had none, and the
indicator/chomping pair is reordered into ruamel's preferred spelling.

**Why it is wrong.** None of these change the scalar's *value* — ruamel keeps `|+` when
there are trailing blank lines that depend on it, and both spellings of the
indicator/chomping pair are legal. It is a round-trip defect, not a semantic one, of the
same kind as B5 and B9: a file under version control gains a diff nobody asked for.

**yamluna.** The header is part of the scalar's lexeme, so an untouched node re-emits it
verbatim. The lexeme spans from the `|`/`>` through the last body line, *including* the
blank lines between the header and the body — those are content that `|+` keeps, and the
cooked value begins with them, so recording them as trivia as well would write them twice.

---

## C. The tag registry

### C1. `register_class` keys the constructor registry on the class *name*

**ruamel.** (RUAMEL-BEHAVIOR §8) `tag = getattr(cls, 'yaml_tag', '!' + cls.__name__)`.
Two classes named `Circuit` in two modules both register under `!Circuit`; the second
overwrites the first; `add_constructor` returns the displaced constructor and
`register_class` throws it away. Measured:

```
--- dumped ---
x: !Circuit
  qubits: 2
y: !Circuit
  n: 3
--- loaded back ---
  x: constructed as liby.circuits.Circuit  __dict__={'qubits': 2}
  y: constructed as liby.circuits.Circuit  __dict__={'n': 3}
reverse registration order -> libx.circuits / libx.circuits
```

`back['x']` is `liby`'s class holding `libx`'s attributes; touching `.n` raises
`AttributeError`. The winner is decided purely by import order.

**Why it is wrong.** Silent construction of the wrong class. No warning, no error, and the
outcome depends on something as incidental as the order of two `import` statements. A YAML
file is not self-describing: `!Circuit` means whatever was imported last.

**yamluna.** `DESIGN.md` §5.2–§5.4. The registry key is `f"{cls.__module__}.{cls.__qualname__}"`,
so registration cannot overwrite. Wire identity is `tag:{source}/{tag_name}` written with
`%TAG` directives, and colliding `(source, tag_name)` pairs are automatically promoted to
full module paths. A bare `!Name` with more than one candidate raises `ConstructorError`
listing every candidate's fully qualified path. **Never guess.**

---

### C2. `register_class` is process-global, not per-`YAML()`

**ruamel.** (RUAMEL-BEHAVIOR §8.1) `add_representer` and `add_constructor` are
`@classmethod`s that mutate a dict on `RoundTripRepresenter` / `RoundTripConstructor`:

```python
y1 = YAML(); y1.register_class(Thing)
y2 = YAML()
'!Thing' in y2.constructor.yaml_constructors    # True
Thing in y2.representer.yaml_representers       # True
```

**Why it is wrong.** It turns C1 from "one library's problem" into "any two libraries in
one process". A library that constructs its own private `YAML()` still poisons — and is
poisoned by — every other one.

**yamluna.** The registry is per-`YAML` instance. A process-wide default registry may be
shared for convenience, but registering on one instance never mutates another.

---

## D. Emitter and resolver defects

### D1. Negative hex/octal/binary integers emit an invalid literal

**ruamel.** (RUAMEL-BEHAVIOR §7.4)

```
x: -0x1F   ->  x: !!int '0x-1F'
x: -0o17   ->  x: !!int '0o-17'
x: -0b101  ->  x: !!int '0b-101'
```

The sign is placed *after* the base prefix and an explicit `!!int` tag is forced. ruamel
reads its own output back (correctly, to `-31`); no other implementation will, because
`0x-1F` matches neither the YAML 1.1 nor the 1.2 int production.

**Why it is wrong.** A round trip that produces YAML only one library can read defeats the
point of YAML.

**yamluna.** `raw` preservation for loaded values; `-0x1f` for constructed ones.

---

### D2. `0X1F` (capital `X`) does not resolve as an integer

**ruamel.** (RUAMEL-BEHAVIOR §7.4)

```
x: 0x1f  -> HexInt     31
x: 0x1F  -> HexCapsInt 31
x: 0X1f  -> str '0X1f'
x: 0X1F  -> str '0X1F'
```

**Why it is wrong.** The YAML 1.1 int production is `0x[0-9a-fA-F_]+`; whether the `x` is
capitalised is not part of it. `0X1F` is an integer in every other implementation, so the
same file means different things to ruamel and to everyone else.

**yamluna.** Resolves `0X`/`0O`/`0B` prefixes as integers and re-emits the original
capitalisation from `raw`.

---

### D3. `DecimalInt` has no representer

**ruamel.** (RUAMEL-BEHAVIOR §7.4) `DecimalInt` is exported by `ruamel.yaml.scalarint` and
documented as "needed if anchor", but dumping one raises:

```
RepresenterError: cannot represent an object: 7
```

**Why it is wrong.** A public, exported type that cannot be serialised.

**yamluna.** Every exported scalar type round-trips; the test corpus constructs one of each
and asserts the output.

---

### D4. `yaml_set_comment_before_after_key(after=...)` is a no-op for scalar values

**ruamel.** (RUAMEL-BEHAVIOR §6) Slot 3 is only read when the value is a collection:

```python
m = load("a: 1\nb: 2\n")
m.yaml_set_comment_before_after_key('b', after='after b')
m.ca.items['b'][3]      # [CT('# after b\n' @col2)]   stored
dump(m)                 # 'a: 1\nb: 2\n'              never emitted
```

With a collection value it works:
`'a: 1\n# before b\nb:\n  # after b\n  c: 2\n'`.

**Why it is wrong.** The call succeeds, the data is stored, and the output is silently
missing it.

**yamluna.** `after=` on a scalar-valued entry places the comment in the entry's `after`
trivia slot and emits it on the line following the value. A comment that is stored is
always emitted; the store-then-silently-discard path does not exist.

---

### D5. `allow_duplicate_keys=True` keeps the *first* value and warns about nothing

**ruamel.** (RUAMEL-BEHAVIOR §9.7)

```python
load("a: 1\nb: 2\na: 3\n", allow_duplicate_keys=True)   # {'a': 1, 'b': 2}
dump(...)                                               # 'a: 1\nb: 2\n'
```

No warning is emitted, and the second value disappears from the output.

**Why it is wrong.** First-wins contradicts both YAML's own "last wins" convention where
duplicates are tolerated and Python's `dict` semantics, and doing it silently means a
config file with an accidental duplicate quietly ignores the value the author most likely
meant. The dump then rewrites the file with the duplicate deleted.

**yamluna.** `DESIGN.md` §2.3: the loader *records* every duplicate rather than silently
merging, and the Python layer raises `DuplicateKeyError` or warns per
`allow_duplicate_keys`. The default (`False` → raise) matches ruamel. The `True` path must
warn rather than stay silent, and must name both source positions — DESIGN.md does not fix
which value wins; whichever is chosen, it has to be documented and tested, and the
byte-identical round-trip requirement (§6.2) means the dump cannot drop the losing entry
from the output.

---

### D6. `copy_attributes` and `CommentedMap.copy()` share the `Comment` object

**ruamel.** (RUAMEL-BEHAVIOR §6)

```python
m.copy_attributes(t);  t.ca is m.ca          # True
m.copy().ca is m.ca                          # True
m.copy_attributes(t2, memo={}); t2.ca is m.ca  # False
copy.deepcopy(m).ca is m.ca                  # False
```

**Why it is wrong.** `copy()` returning an object that shares mutable state with the
original is a bug factory: adding a comment to the copy edits the original.

**yamluna.** `copy()` copies the trivia store. `copy_attributes(t)` keeps ruamel's
signature but copies rather than aliases; `memo=` continues to deep-copy.

---

### D7. `.lc` is a load-time snapshot with no staleness signal

**ruamel.** (RUAMEL-BEHAVIOR §4) After any mutation `.lc` still reports the source
positions, and newly inserted keys are simply absent from `.lc.data`:

```python
d2 = load(SRC); d2.insert(0, 'zzz', 1)
d2.lc.key('alpha')        # (0, 0)   'alpha' is now on line 1
'zzz' in d2.lc.data       # False
d2.lc.key('zzz')          # KeyError
```

**Why it is wrong.** Not that the positions are stale — recomputing them on every edit
would be absurd — but that there is no way to tell. `.lc.key(k)` raising `KeyError` for a
key that demonstrably exists in the mapping is the only signal, and it reads as a bug in
the caller rather than "this node has no recorded source position".

**yamluna.** `.lc` keeps ruamel's semantics (0-based, load-time snapshot; not maintained
across edits). The one change: `.lc.key(k)` / `.lc.value(k)` / `.lc.item(i)` return `None`
for a node with no recorded position, the same as `.lc` on a node that was never loaded,
instead of raising `KeyError`.

---

## Not divergences

Recorded so the differential harness does not re-litigate them:

- **`.lc` is 0-based** for both line and column, matching `Marker::col()` in the scanner
  fork (`DESIGN.md` §1.5). yamluna keeps 0-basing.
- **`Mark.index` / `Mark.pointer` are character offsets**, not byte offsets — same as
  `Marker::index()`. yamluna keeps character offsets at the Python surface.
- **Aliases resolve to object identity** (`d['use'] is d['base']`). yamluna keeps this at
  the Python surface even though the core models `Alias` as a distinct node kind, because
  user code depends on it.
- **`Format.flow_style()` beats `YAML.default_flow_style`** for loaded containers.
  Correct, and yamluna does the same.
- **Duplicate `<<` merge keys are always an error**, even with
  `allow_duplicate_keys=True`. Correct per the merge-key spec; yamluna keeps it.
- **A plain string / decimal int / bool loads as a builtin `str` / `int` / `bool`**, not as
  `PlainScalarString` / `DecimalInt` / `ScalarBoolean`. Keeping this matters for
  `isinstance` checks in user code; yamluna preserves the original lexeme in the document
  model instead of in the Python object.
