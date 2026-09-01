# Measured ruamel behaviour

This page is an appendix. It is the evidence the rest of the site cites when it says
`ruamel.yaml` does something: 59 runnable snippets against `ruamel.yaml` 0.19.1, each with the
output it actually printed. It is dense, it is not written to be read front to back, and
nothing here is a recommendation. Come to it when you want to check a claim made on
[Why yamluna exists](../why.md), [Behaviour differences](../migrating/differences.md) or
[Architecture](index.md), and use the [snippet index](#11-snippet-index) at the bottom to find
the section that covers it.

Every claim below was produced by running the snippet shown against `.venv/bin/python` with
`ruamel.yaml` 0.19.1 (`ruamel.yaml.version_info == (0, 19, 1)`). Output blocks are verbatim
program output, not reconstructions. Where the source explains *why*, the source is quoted; the
observed behaviour is the specification.

All snippets assume this preamble. Paste it above any snippet you want to re-run:

```python
import io
from ruamel.yaml import YAML
from ruamel.yaml.tokens import CommentToken

def Y(**kw):
    y = YAML()
    for k, v in kw.items():
        setattr(y, k, v)
    return y

def load(s, **kw):
    return Y(**kw).load(s)

def dump(d, **kw):
    b = io.StringIO(); Y(**kw).dump(d, b); return b.getvalue()

def ct(t):
    if t is None:
        return 'None'
    if isinstance(t, list):
        return '[' + ', '.join(ct(x) for x in t) + ']'
    if isinstance(t, CommentToken):
        m = t.start_mark
        loc = f'@L{m.line},C{m.column}' if hasattr(m, 'line') else f'@col{m.column}'
        return f'CT({t.value!r} {loc})'
    return repr(t)

def show(obj, name='obj'):
    ca = obj.ca
    print(f'{name}.ca.comment = {ct(ca.comment)}')
    print(f'{name}.ca.pre     = {ct(ca.pre)}')
    print(f'{name}.ca.end     = {ct(ca.end)}')
    for k, v in ca.items.items():
        print(f'{name}.ca.items[{k!r}] = {ct(v)}')
```

---

## 1. The `.ca` model

### 1.1 The container

`.ca` is a `ruamel.yaml.comments.Comment`, lazily created by `CommentedBase.ca` and
stored on the object as the attribute `_yaml_comment`.

```
Comment.__slots__ = ('comment', '_items', '_post', '_pre')
comment_attrib = _yaml_comment | format_attrib = _yaml_format
line_col_attrib = _yaml_line_col | merge_attrib = _yaml_merge
CommentedMap.__slots__ = ('_yaml_comment', '_ok', '_ref')
CommentedSeq.__slots__ = ('_yaml_comment', '_lst')
```

| attribute | backing slot | what it holds after `YAML().load()` |
|---|---|---|
| `.comment` | `comment`  | `None`, or `[post, pre]` where `post` is a single `CommentToken` (the node's own EOL comment) and `pre` is a `list[CommentToken]` (own-line comments immediately above the node's first token). |
| `.items`   | `_items`   | `dict` keyed by mapping key / sequence index → a 4-slot `list`. |
| `.end`     | `_post`    | `list`. **The round-trip loader never populates it**: it is always `[]` (see §1.6). |
| `.pre`     | `_pre`     | `None`. `Comment.__init__(old=True)` is the default and sets `_pre = None`; only `Comment(old=False)` gives `[]`. The rt path never constructs `old=False`, so `.ca.pre` is `None` on every loaded node. |

`Comment.get(item, pos)` / `Comment.set(item, pos, value)` read/write one slot, growing
the 4-list as needed. `Comment.set` asserts the slot is currently `None`.

### 1.2 The four slots of `ca.items[key]`: **mapping**

Written by `CommentedBase.yaml_key_comment_extend` (`r[0] = comment[0]; r[1] = comment[1]`)
and `yaml_value_comment_extend` (`r[2] = comment[0]; r[3] = comment[1]`):

| slot | name | type | holds |
|---|---|---|---|
| 0 | post-key | `CommentToken` | EOL comment on the **key's own line**, only for an explicit `? key` entry |
| 1 | pre-key  | `list[CommentToken]` | own-line comments above the key. **Only ever written by `yaml_set_comment_before_after_key`**, never by the loader |
| 2 | post-value | `CommentToken` | EOL comment after the value **plus every own-line comment that follows it**, concatenated into one token's `.value` |
| 3 | pre-value | `list[CommentToken]` | own-line comments between the `:` and a **collection** value |

> **Trap.** `ruamel.yaml.comments` exports `C_VALUE_EOL=0, C_KEY_EOL=1, C_KEY_PRE=2,
> C_VALUE_POST=3, C_VALUE_PRE=4, C_KEY_POST=5`. **These do not describe the layout above.**
> They belong to the experimental `comment_handling` scheme, which is only enabled for
> `typ='rtsc'`; `YAML(typ='rt').comment_handling is None`, and the `None` branch uses the
> `yaml_*_comment_extend` layout. Verified:
> ```
> YAML().comment_handling      = None
> ```

### 1.3 The four slots of `ca.items[index]`: **sequence**

`CommentedSeq._yaml_add_comment` always routes through `yaml_key_comment_extend`, so only
slots 0 and 1 are ever used:

| slot | type | holds |
|---|---|---|
| 0 | `CommentToken` | EOL comment on the item **plus every own-line comment that follows it** |
| 1 | `list[CommentToken]` | own-line comments above the item, populated by the loader **only inside a flow sequence** |
| 2, 3 | (none) | unused |

### 1.4 A comment in every position: mapping

```python
SRC = """\
# doc comment 1
# doc comment 2

# before first key
alpha: 1  # eol after alpha value
# between alpha and beta
beta:
  # before nested key
  inner: 2  # eol on inner
# between beta and gamma
gamma: 3
# trailing at end of doc
"""
d = load(SRC)
show(d, 'm')
show(d['beta'], 'm["beta"]')
```

```
m.ca.comment = [None, [CT('# doc comment 1\n' @L0,C0), CT('# doc comment 2\n\n' @L1,C0), CT('# before first key\n' @L3,C0)]]
m.ca.pre     = None
m.ca.end     = []
m.ca.items['alpha'] = [None, None, CT('# eol after alpha value\n# between alpha and beta\n' @L4,C10), None]
m.ca.items['beta'] = [None, None, None, [CT('# before nested key\n' @L7,C2)]]
m.ca.items['gamma'] = [None, None, CT('\n# trailing at end of doc\n' @L11,C0), None]

m["beta"].ca.comment = [None, [CT('# before nested key\n' @L7,C2)]]
m["beta"].ca.pre     = None
m["beta"].ca.end     = []
m["beta"].ca.items['inner'] = [None, None, CT('# eol on inner\n# between beta and gamma\n' @L8,C12), None]
```

Read this carefully; it is the whole model:

1. **A comment before the first key is not a per-key comment.** It goes into the
   *collection's* `ca.comment[1]` list, together with every comment above it, including
   comments that logically belong to the document rather than the mapping.
2. **A pre-key comment for any key but the first does not exist.** `# between alpha and
   beta` is glued onto the end of `alpha`'s post-value token: one `CommentToken` whose
   `.value` is `'# eol after alpha value\n# between alpha and beta\n'`. There is no way to
   ask "what comment precedes `beta`?" without string-splitting somebody else's token.
3. **A comment above a nested collection is stored twice**: in the parent's slot 3
   (`m.ca.items['beta'][3]`) *and* in the child's `ca.comment[1]`. Both are emitted from
   the child; the parent's copy is inert.
4. The `start_mark` records the source line/column of the **first** line of a multi-line
   token; the second and later lines carry their original indentation literally inside
   `.value` (see `d['key'].ca.items[0] = CT('# eol one\n  # between\n')` in §1.5).

### 1.5 A comment in every position: sequence

```python
SRC = """\
# doc comment
# before first item
- one    # eol on one
# between one and two
- two
# between two and three
- three  # eol on three
# trailing
"""
s = load(SRC)
show(s, 's')
```

```
s.ca.comment = [None, [CT('# doc comment\n' @L0,C0), CT('# before first item\n' @L1,C0)]]
s.ca.pre     = None
s.ca.end     = []
s.ca.items[0] = [CT('# eol on one\n# between one and two\n' @L2,C9), None, None, None]
s.ca.items[1] = [CT('\n# between two and three\n' @L5,C0), None, None, None]
s.ca.items[2] = [CT('# eol on three\n# trailing\n' @L6,C9), None, None, None]
```

`items[1]`'s token begins with a bare `\n`: item 1 has **no** EOL comment, so the token
starts with the line terminator of its own line and the following own-line comment is
appended. That leading `\n` is how "no EOL comment, but something below" is encoded.

Nested, with indentation preserved inside `.value`:

```python
SRC2 = """\
key:
  # before first item
  - one   # eol one
  # between
  - two
# after the seq, before next key
other: 1
"""
d = load(SRC2); show(d, 'd'); show(d['key'], "d['key']")
```

```
d.ca.comment = None
d.ca.items['key'] = [None, None, None, [CT('# before first item\n' @L1,C2)]]
d['key'].ca.comment = [None, [CT('# before first item\n' @L1,C2)]]
d['key'].ca.items[0] = [CT('# eol one\n  # between\n' @L2,C10), None, None, None]
d['key'].ca.items[1] = [CT('\n# after the seq, before next key\n' @L5,C0), None, None, None]
```

`# after the seq, before next key`, a comment that belongs to the *outer* mapping, at
column 0, outdented past the sequence, is stored on the sequence's **last item**. There is
no "this comment closes the block" concept.

### 1.6 `ca.comment[0]` (post) and `ca.end`

`ca.comment[0]` is populated when the collection itself carries an EOL comment:

```python
for src in ["a: {x: 1, y: 2}  # eol\n", "a: # eol on the key of a block map\n  b: 1\n"]:
    d = load(src); print(repr(src)); print(' child:', ct(d['a'].ca.comment))
```

```
'a: {x: 1, y: 2}  # eol\n'
 child: [CT('# eol\n' @L0,C17), None]
'a: # eol on the key of a block map\n  b: 1\n'
 child: [CT('# eol on the key of a block map\n' @L0,C3), None]
```

`ca.end` is **never** written by the rt loader in 0.19.1. Everything I could construct
(`...` markers, multi-document streams, comments after `...`, outdented trailing comments)
leaves it `[]`:

```
'a: 1\n...\n'                  -> ca.end = [] | items: {}
'a: 1\n# end\n...\n'           -> ca.end = [] | items: {'a': [None, None, CT('\n# end\n'), None]}
'- 1\n# tail\n'                -> ca.end = [] | items: {0: [CT('\n# tail\n'), None, None, None]}
'a: 1\n\n# tail after blank\n' -> ca.end = [] | items: {'a': [None, None, CT('\n\n# tail after blank\n'), None]}
'a: 1\n...\n# after end marker\n' -> ca.end = [] ; dump: 'a: 1\n'   # <- comment destroyed
```

`ca.end` *is* honoured on the write side, but only if `ca.comment` is already a list;
`representer.py:744` does `node.comment.append(comment.end)` inside a bare
`except AttributeError: pass`:

```python
m = load("a: 1\n")
m.yaml_end_comment_extend([CommentToken('# the end\n', CommentMark(0))], clear=True)
print(repr(dump(m)))                       # -> 'a: 1\n'      (silently dropped)
m = load("a: 1\n"); m.yaml_set_start_comment('start')
m.yaml_end_comment_extend([CommentToken('# the end\n', CommentMark(0))], clear=True)
print(repr(dump(m)))                       # -> '# start\na: 1\n# the end\n'
```

### 1.7 The canonical repr

```python
SRC = """\
# 1 before the first key (own line)
alpha: 1        # 2 eol after a value
# 3 between two keys
beta:           # 4 eol after a key whose value is a block map
  # 5 before the first nested key
  inner: 2
? gamma         # 6 eol on an explicit key
: 3             # 7 eol on its value
delta:
  - x           # 8 eol on a seq item
  # 9 between seq items
  - y
# 10 after everything
"""
d = load(SRC); print(repr(d.ca))
```

```
Comment(
  start=[None, [CommentToken('# 1 before the first key (own line)\n', line: 0, col: 0)]],
  items={
    alpha: [None, None, CommentToken('# 2 eol after a value\n# 3 between two keys\n', line: 1, col: 16), None]
    beta:  [None, None, CommentToken('# 4 eol after a key whose value is a block map\n  # 5 before the first nested key\n', line: 3, col: 16), None]
    gamma: [CommentToken('# 6 eol on an explicit key\n', line: 6, col: 16), None, CommentToken('# 7 eol on its value\n', line: 7, col: 16), None]
  })
```

`delta` has no entry at all; its comments live on `d['delta'].ca`:

```
d['beta'].ca : Comment(comment=[CommentToken('# 4 ...\n  # 5 ...\n', line: 3, col: 16), None, [], []], items={})
d['delta'].ca: Comment(comment=None,
  items={0: [CommentToken('# 8 eol on a seq item\n  # 9 between seq items\n', line: 9, col: 16), None, None, None],
         1: [CommentToken('\n# 10 after everything\n', line: 12, col: 0), None, None, None]})
```

`slot 0` (post-key) is populated only by the explicit `? gamma` form. Note also that
`d['beta'].ca.comment` has grown to **four** elements; see §10.3.

This document does **not** round-trip (`dump(d) == SRC` is `False`); see §10.5 and §10.6.

---

## 2. Blank lines

Blank lines have no representation of their own. They are bare `\n` characters embedded in
a `CommentToken.value`, indistinguishable from the line terminator that separates an EOL
comment from a following own-line comment.

```python
SRC = """\
a: 1

b: 2


c: 3
# c trailing

d: 4
"""
m = load(SRC); show(m, 'm'); print('roundtrip identical:', dump(m) == SRC)
```

```
m.ca.comment = None
m.ca.pre     = None
m.ca.end     = []
m.ca.items['a'] = [None, None, CT('\n\n' @L0,C3), None]
m.ca.items['b'] = [None, None, CT('\n\n\n' @L2,C3), None]
m.ca.items['c'] = [None, None, CT('\n# c trailing\n\n' @L6,C0), None]
roundtrip identical: True
```

Decoding: the token for a value with **no** EOL comment starts with one `\n` (that value's
own line ending); each further `\n` before a `#` or the end of the token is one blank line.
So `'\n\n'` = 1 blank line, `'\n\n\n'` = 2 blank lines, `'\n# c trailing\n\n'` = comment
then 1 blank line. Counting blank lines therefore requires parsing somebody else's
comment text.

With an EOL comment present the leading `\n` is replaced by the comment:

```
'a: 1\n\n\nb: 2\n'  ->  m.ca.items['a'] = [None, None, CT('\n\n\n' @L0,C3), None]
```

Two lossy edges:

```python
SRC3 = "\n\n# lead\n\na: 1\n"          # two leading blank lines
m3 = load(SRC3)
# m3.ca.comment = [None, [CT('\n' @L1,C0), CT('# lead\n\n' @L2,C0)]]
print(repr(dump(m3)))                   # -> '\n# lead\n\na: 1\n'   one blank line lost
```

```python
SRC4 = "a: 1\n   \nb: 2\n"             # blank line containing spaces
m = load(SRC4)
# m.ca.items['a'] = [None, None, CT('\n\n' @L0,C3), None]
print(repr(dump(m)), dump(m) == SRC4)  # -> 'a: 1\n\nb: 2\n' False
```

Trailing whitespace *inside a comment* is preserved verbatim:

```python
SRC3 = "a: 1  # trailing spaces here   \nb: 2\n"
m = load(SRC3)
# m.ca.items['a'] = [None, None, CT('# trailing spaces here   \n' @L0,C6), None]
print(dump(m) == SRC3)                  # -> True
```

---

## 3. The comment-drift bugs

These are the defects yamluna exists to not have. Each is a minimal, runnable repro with
the actual (wrong) output and the output a correct implementation must produce.

### 3.0 Where the bug is *not*

`CommentedSeq.insert` and `CommentedSeq.__delsingleitem__` **do** renumber `ca.items`
(`comments.py:489-505`). For EOL-only comments the result is correct:

```python
SRC = "- one    # comment one\n- two    # comment two\n- three  # comment three\n"
s = load(SRC); s.insert(0, 'zero'); print(dump(s))
```

```
- zero
- one    # comment one
- two    # comment two
- three  # comment three
```

The drift comes from the *representation*, not the index arithmetic: an own-line comment
does not live on the node it describes, it lives glued to the previous sibling's EOL
token (§1.4/§1.5). Renumbering moves the wrong thing.

### 3.1 `CommentedSeq.insert` puts the next item's comment above the new item

```python
SRC = """\
# about one
- one
# about two
- two
# about three
- three
"""
s = load(SRC); s.insert(0, 'zero'); print(dump(s))
```

Actual:
```
# about one
- zero
- one
# about two
- two
# about three
- three
```

`# about one` describes `one`; it now describes `zero`. **Expected:**
```
- zero
# about one
- one
# about two
- two
# about three
- three
```

Same defect mid-list, with `s.insert(1, 'x')`:

Actual:
```
# about one
- one
# about two
- x
- two
# about three
- three
```
`# about two` now labels `x`. **Expected:** `- x` between `- one` and `# about two`.

### 3.2 `del seq[i]` orphans the deleted item's comment onto its successor

```python
s = load(SRC); del s[1]; print(dump(s))
```

Actual:
```
# about one
- one
# about two
- three
```

`two` is gone, `# about two` is not; it now describes `three`. **Expected:**
```
# about one
- one
# about three
- three
```

### 3.3 `del seq[0]` both orphans one comment and destroys another

```python
s = load(SRC); del s[0]; print(dump(s))
```

Actual:
```
# about one
- two
# about three
- three
```

`# about one` (whose item is gone) survives and mislabels `two`; `# about two` (whose item
survives) is **destroyed**, because it lived inside `ca.items[0]`, which
`__delsingleitem__` pops. **Expected:**
```
# about two
- two
# about three
- three
```

`del s[2]` (last item) leaves `# about three` dangling as the final line of the document
with nothing to describe.

### 3.4 `list.reverse()` does not touch `ca.items` at all

`CommentedSeq` overrides `sort` (which remaps `ca.items` correctly) but not `reverse`:

```python
S = "- a  # ca\n- b  # cb\n- c  # cc\n- d  # cd\n"
s = load(S); s.sort(reverse=True); print(repr(dump(s)))
s = load(S); s.reverse();          print(repr(dump(s)))
```

```
sort(reverse=True) -> '- d  # cd\n- c  # cc\n- b  # cb\n- a  # ca\n'     correct
reverse()          -> '- d  # ca\n- c  # cb\n- b  # cc\n- a  # cd\n'     every comment wrong
```

### 3.5 `CommentedMap.__delitem__` never removes the key's `ca.items` entry

`comments.py:CommentedMap.__delitem__` touches `_ok`, `_ref`, `merge_pos` and the
`ordereddict`, never `self.ca`. Two consequences.

**(a) The comment drifts onto the following key, and stale state accumulates.**

```python
SRC = """\
# about alpha
alpha: 1   # eol alpha
# about beta
beta: 2    # eol beta
# about gamma
gamma: 3   # eol gamma
"""
m = load(SRC); m.pop('beta'); print(dump(m))
print('stale:', 'beta' in m.ca.items, ct(m.ca.items['beta']))
```

Actual:
```
# about alpha
alpha: 1   # eol alpha
# about beta
gamma: 3   # eol gamma

stale: True [None, None, CT('# eol beta\n# about gamma\n' @L3,C11), None]
```

`# about beta` now labels `gamma`; `# about gamma` is destroyed (it was inside beta's
token); `ca.items['beta']` survives forever. **Expected:**
```
# about alpha
alpha: 1   # eol alpha
# about gamma
gamma: 3   # eol gamma
```

**(b) The stale entry resurrects a deleted comment onto an unrelated new value.**

```python
SRC = "alpha: 1\nbeta: 2  # secret comment about beta\ngamma: 3\n"
m = load(SRC)
del m['beta']
print(repr(dump(m)))                    # -> 'alpha: 1\ngamma: 3\n'
m['beta'] = 'a brand new unrelated value'
print(repr(dump(m)))
```

```
'alpha: 1\ngamma: 3\nbeta: a brand new unrelated value # secret comment about beta\n'
```

A comment the user deleted reappears attached to a value it was never about.

### 3.6 Key rename

**Naive `pop` + assign**: order lost, comment misattached, EOL comment destroyed:

`SRC` here is the six-line document from §3.5(a).

```python
m = load(SRC); v = m.pop('beta'); m['BETA'] = v; print(dump(m))
```
```
# about alpha
alpha: 1   # eol alpha
# about beta
gamma: 3   # eol gamma
BETA: 2
```

**Order-preserving `CommentedMap.insert(pos, key, value)`**: order kept, `# eol beta`
still destroyed:

```python
m = load(SRC); m.insert(1, 'BETA', m.pop('beta')); print(dump(m))
```
```
# about alpha
alpha: 1   # eol alpha
# about beta
BETA: 2
gamma: 3   # eol gamma
```

**Expected** for a rename: every comment slot moves with the entry,
`BETA: 2    # eol beta` with `# about beta` above it.

### 3.7 `move_to_end` scatters comments across the document

```python
m = load(SRC); m.move_to_end('alpha'); print(dump(m))
```

Actual:
```
# about alpha
beta: 2    # eol beta
# about gamma
gamma: 3   # eol gamma
alpha: 1   # eol alpha
# about beta
```

`# about alpha` stayed at the top (it lives in `ca.comment[1]`), and `# about beta`, glued
to alpha's EOL token, travelled to the very end of the document. **Expected:**
```
# about beta
beta: 2    # eol beta
# about gamma
gamma: 3   # eol gamma
# about alpha
alpha: 1   # eol alpha
```

### 3.8 Deleting the last key strands its pre-comment

```python
m = load(SRC); del m['gamma']; print(dump(m))
```
```
# about alpha
alpha: 1   # eol alpha
# about beta
beta: 2    # eol beta
# about gamma          <- describes a key that no longer exists
```

### 3.9 Slice operations

```python
S = "- a  # ca\n- b  # cb\n- c  # cc\n- d  # cd\n"
s = load(S); del s[1:3];      print(repr(dump(s)))  # '- a  # ca\n- d  # cd\n'      correct
s = load(S); s[1:3] = ['X'];  print(repr(dump(s)))  # '- a  # ca\n- X\n- d  # cd\n' correct
```

Slices route through `MutableSliceableSequence` to repeated `__delsingleitem__`, so with
EOL-only comments they behave; with own-line comments they inherit §3.2/§3.3.

---

## 4. `.lc` (LineCol)

`LineCol` is `_yaml_line_col`, created lazily by `CommentedBase.lc`. It has exactly
`line`, `col`, `data` plus the accessors `key(k)`, `value(k)`, `item(i)`,
`add_kv_line_col`, `add_idx_line_col`.

**All values are 0-based**, both line and column; the class docstring says so
("values start at zero (0)") and the measurement agrees.

```python
SRC = """\
alpha:
    beta: 1
    gamma: [10, 20]
list:
  - first
  -   second
"""
d = load(SRC)
```

```
d.lc                 -> LineCol(0, 0)  line= 0 col= 0
d.lc.data            -> {'alpha': [0, 0, 1, 4], 'list': [3, 0, 4, 2]}
d.lc.key('alpha')    -> (0, 0)
d.lc.value('alpha')  -> (1, 4)
d.lc.key('list')     -> (3, 0)
d.lc.value('list')   -> (4, 2)
d['alpha'].lc        -> LineCol(1, 4) 1 4
d['alpha'].lc.data   -> {'beta': [1, 4, 1, 10], 'gamma': [2, 4, 2, 11]}
d['alpha'].lc.key('gamma')  -> (2, 4)
d['alpha'].lc.value('gamma')-> (2, 11)
gamma seq .lc        -> LineCol(2, 11) 2 11
gamma .lc.data       -> {0: [2, 12], 1: [2, 16]}
gamma .lc.item(0)    -> (2, 12)  item(1) -> (2, 16)
list .lc.data        -> {0: [4, 4], 1: [5, 6]}
list .lc.item(0)     -> (4, 4)  item(1) -> (5, 6)
```

| member | meaning |
|---|---|
| `.line` / `.col` | position of the collection node's own first character. For a block mapping that is its first key; for `d['alpha']` (indented 4) it is `(1, 4)`. |
| `.data` | mapping: `{key: [key_line, key_col, value_line, value_col]}`. sequence: `{index: [line, col]}` (2 elements, of the **item's value**, not of the `-`). |
| `.key(k)` | `(data[k][0], data[k][1])`. Raises `KeyError` for an unknown key (`data[k]`, not `.get`). |
| `.value(k)` | `(data[k][2], data[k][3])`. |
| `.item(i)`  | `(data[i][0], data[i][1])`. Works on a mapping too, where it returns the key position. |

`list .lc.item(1) -> (5, 6)` for the source line `  -   second`: column 6 is `second`, not
the dash at column 2. The dash column is not recorded anywhere.

Only `CommentedMap`/`CommentedSeq`/`CommentedSet`/`TaggedScalar` carry `.lc`. Plain scalars
do not: a loaded `int`, `str`, `bool`, or `SingleQuotedScalarString` has no `_yaml_line_col`
(`ScalarString.__slots__` is `Anchor.attrib` only). `TaggedScalar().lc` exists but reads
`LineCol(None, None)`.

**`.lc` is a load-time snapshot and is never maintained.**

```python
d['alpha']['beta'] = 99
d['alpha'].lc.value('beta')        # -> (1, 10)  still the old position

d2 = load(SRC); d2.insert(0, 'zzz', 1)
d2.lc.key('alpha')                 # -> (0, 0)   'alpha' is now on line 1
'zzz' in d2.lc.data                # -> False
```

---

## 5. `.fa` (Format) and `.anchor` (Anchor)

### 5.1 `Format`

`_yaml_format`, `__slots__ = ('_flow_style',)`. Three-valued: `None` (undecided),
`True` (flow), `False` (block).

```
d = load("a: {x: 1}\nb:\n  y: 2\nc: [1, 2]\n")
  d['a'] CommentedMap   fa=Format(True)  fa.flow_style()=True  flow_style(True)=True
  d['b'] CommentedMap   fa=Format(False) fa.flow_style()=False flow_style(True)=False
  d['c'] CommentedSeq   fa=Format(True)  fa.flow_style()=True  flow_style(True)=True
  root fa = Format(False) False
```

`flow_style(default=None)` returns `default` when `_flow_style is None`, else the stored
value, i.e. the per-node setting always wins over `YAML.default_flow_style`.

```python
d2 = load("a:\n  x: 1\n  y: 2\n"); d2['a'].fa.set_flow_style()
dump(d2)                                  # -> 'a: {x: 1, y: 2}\n'
d3 = load("a: {x: 1, y: 2}\n"); d3['a'].fa.set_block_style()
dump(d3)                                  # -> 'a:\n  x: 1\n  y: 2\n'
```

That is the whole API: `set_flow_style()`, `set_block_style()`, `flow_style(default)`.
There is no `set_undecided()`: once set you cannot go back to `None` through the API.

### 5.2 `Anchor`

`_yaml_anchor`, `__slots__ = ('value', 'always_dump')`, `always_dump` defaults to `False`.
`CommentedBase.yaml_set_anchor(value, always_dump=False)` sets both;
`yaml_anchor()` returns the `Anchor` or `None` if the attribute was never created.
On the scalar types `yaml_anchor(any=False)` additionally returns `None` unless
`always_dump` (so a scalar anchor must be `always_dump` to survive).

```python
SRC = "base: &b\n  x: 1\nuse: *b\nother: &unused\n  y: 2\n"
d = load(SRC)
```

```
d["base"].anchor        = Anchor('b') | .value = b | .always_dump = False
d["use"] is d["base"]   = True
d["other"].anchor.value = unused
round trip: 'base: &b\n  x: 1\nuse: *b\nother:\n  y: 2\n'
```

**An anchor referenced zero or one times is dropped on dump.** `&unused` is gone. The
serializer emits `&name` only for objects it has seen more than once, unless
`always_dump` is set:

```python
d2 = load("other: &unused\n  y: 2\n"); dump(d2)                     # 'other:\n  y: 2\n'
d3 = load("other: &unused\n  y: 2\n"); d3['other'].anchor.always_dump = True
dump(d3)                                                            # 'other: &unused\n  y: 2\n'
```

`yaml_set_anchor` behaves identically:

```python
d4 = load("a:\n  x: 1\nb:\n  y: 2\n"); d4['a'].yaml_set_anchor('myanchor')
dump(d4)                          # 'a:\n  x: 1\nb:\n  y: 2\n'    name ignored
d5 = ...;  d5['a'].yaml_set_anchor('myanchor', always_dump=True)
dump(d5)                          # 'a: &myanchor\n  x: 1\nb:\n  y: 2\n'
d6 = ...;  d6['a'].yaml_set_anchor('myanchor'); d6['b'] = d6['a']
dump(d6)                          # 'a: &myanchor\n  x: 1\nb: *myanchor\n'
```

Aliases are resolved to **object identity** at load: `d['use'] is d['base']` is `True`.
There is no alias node; a recursive anchor therefore produces a recursive Python object.

---

## 6. Public method surface

Exact signatures, taken from `inspect.signature`:

```
CommentedBase.yaml_key_comment_extend(self, key, comment, clear=False) -> None
CommentedBase.yaml_value_comment_extend(self, key, comment, clear=False) -> None
CommentedBase.yaml_end_comment_extend(self, comment, clear=False) -> None
CommentedBase.yaml_set_start_comment(self, comment, indent=0) -> None
CommentedBase.yaml_set_comment_before_after_key(self, key, before=None, indent=0,
                                                after=None, after_indent=None) -> None
CommentedBase.yaml_add_eol_comment(self, comment, key=NotNone, column=None) -> None
CommentedBase.yaml_set_anchor(self, value, always_dump=False) -> None
CommentedBase.yaml_anchor(self) -> Any
CommentedBase.yaml_set_ctag(self, value: Tag) -> None
CommentedBase.copy_attributes(self, t, memo=None) -> Any
CommentedMap.insert(self, pos, key, value, comment=None) -> None
CommentedSeq.insert(self, idx, val) -> None
CommentedMap.mlget(self, key, default=None, list_ok=False) -> Any
CommentedMap.add_yaml_merge(self, value) -> None
CommentedMap.copy(self) -> Any
CommentedSeq.sort(self, key=None, reverse=False) -> None
```

Plus the properties `.ca`, `.fa`, `.lc`, `.anchor`, `.tag`, and on `CommentedMap` `.merge`,
`.non_merged_items()`, `.add_referent()`, `.update_key_value()`.

### `yaml_set_start_comment(comment, indent=0)`

Splits on `\n`, prefixes `# ` to any line that does not already start with `#`, and writes
`CommentToken(line + '\n', CommentMark(indent))` into `ca.comment[1]`, **clearing** any
existing pre-comments first (`_yaml_clear_pre_comment`).

```python
m = load("a: 1\nb: 2\n"); m.yaml_set_start_comment('hello\nworld')
# ca.comment = [None, [CT('# hello\n' @col0), CT('# world\n' @col0)]]
dump(m)                                    # '# hello\n# world\na: 1\nb: 2\n'

m2 = load("a:\n  b: 1\n"); m2['a'].yaml_set_start_comment('nested note', indent=2)
dump(m2)                                   # 'a:\n  # nested note\n  b: 1\n'

m3 = load("# original\na: 1\n"); m3.yaml_set_start_comment('replacement')
dump(m3)                                   # '# replacement\na: 1\n'   original destroyed
```

### `yaml_add_eol_comment(comment, key=NotNone, column=None)`

Prefixes `# ` if absent. If `column is None` it calls `_yaml_get_column(key)`, which copies
the column of the nearest neighbouring key/index that already has a comment; if there is
none it returns `None`, and the method then prepends a space and uses column 0.
Writes `[CommentToken(text, CommentMark(column)), None]` into slot 2 (map) / slot 0 (seq).

```python
m = load("a: 1\nb: 2\n"); m.yaml_add_eol_comment('note on a', key='a')
# ca.items = {'a': [None, None, CT(' # note on a' @col0), None]}
dump(m)                                    # 'a: 1  # note on a\nb: 2\n'

m = load("a: 1\nb: 2\n"); m.yaml_add_eol_comment('at col 20', key='b', column=20)
dump(m)                                    # 'a: 1\nb: 2                # at col 20\n'

s = load("- 1\n- 2\n"); s.yaml_add_eol_comment('elem 0', key=0)
dump(s)                                    # '- 1  # elem 0\n- 2\n'

m = load("a: 1\n"); m.yaml_add_eol_comment('#raw', key='a')
dump(m)                                    # 'a: 1  #raw\n'    leading '#' honoured
```

Note the emitted token has **no trailing newline** (`' # note on a'`), unlike loader-produced
tokens. Calling it twice for the same key silently overwrites (it routes through
`yaml_value_comment_extend`, which assigns `r[2]` unconditionally, unlike `Comment.set`,
which would assert):

```python
m = load("a: 1\n")
m.yaml_add_eol_comment('first',  key='a')
m.yaml_add_eol_comment('second', key='a')
# ca.items['a'] = [None, None, CT(' # second' @col0), None]  ->  'a: 1  # second\n'
```

### `yaml_set_comment_before_after_key(key, before=None, indent=0, after=None, after_indent=None)`

`before` → slot 1 (a list, appended to). `after` → slot 3. `after_indent` defaults to
`indent + 2`. An empty string yields a token whose value is just `'\n'`, i.e. a blank line.

```python
m = load("a: 1\nb: 2\n")
m.yaml_set_comment_before_after_key('b', before='before b\nsecond line',
                                    indent=0, after='after b', after_indent=2)
# ca.items['b'] = [None, [CT('# before b\n' @col0), CT('# second line\n' @col0)],
#                  None, [CT('# after b\n' @col2)]]
dump(m)          # 'a: 1\n# before b\n# second line\nb: 2\n'   <- 'after b' is GONE
```

`after=` is **silently discarded when the value is a scalar**: slot 3 is only read for
collection values:

```python
m = load("a: 1\nb:\n  c: 2\n")
m.yaml_set_comment_before_after_key('b', before='before b', after='after b')
dump(m)          # 'a: 1\n# before b\nb:\n  # after b\n  c: 2\n'    works
```

`before='\n'` is the documented way to insert a blank line:

```python
m = load("a: 1\nb: 2\n"); m.yaml_set_comment_before_after_key('b', before='\n')
dump(m)          # 'a: 1\n\nb: 2\n'
```

### `yaml_end_comment_extend(comment, clear=False)`

Appends a list of `CommentToken` to `ca.end`. Emitted only if `ca.comment` is a list; see
§1.6.

### `yaml_key_comment_extend(key, comment, clear=False)` / `yaml_value_comment_extend(...)`

The low-level writers. `comment` is a 2-list `[eol_token, pre_token_list]`.
`key` variant writes slots 0/1, `value` variant slots 2/3. With `clear=False` and an
existing list in the pre slot they do `r[1].extend(comment[0])`, extending the *pre* list
with the *eol* token, which is almost certainly a typo in ruamel but is the shipped
behaviour.

### `yaml_set_anchor(value, always_dump=False)` / `yaml_anchor()`

See §5.2.

### `yaml_set_ctag(value: Tag)`

Sets `_yaml_tag`. `.tag` reads it back (creating an empty `Tag()` on demand).

### `copy_attributes(t, memo=None)`

Copies `_yaml_comment`, `_yaml_format`, `_yaml_line_col`, `_yaml_anchor`, `_yaml_tag`,
`_yaml_merge` onto `t` and returns `t`. **Without `memo` the attributes are shared, not
copied.**

```python
m = load("# start\na: 1  # eol a\nb: 2\n")
t = CommentedMap(); t['a'] = 1; t['b'] = 2
m.copy_attributes(t)
t.ca is m.ca                    # True   <- shared; mutating t.ca mutates m.ca
dump(t)                         # '# start\na: 1  # eol a\nb: 2\n'
t2 = CommentedMap(); t2['a'] = 1; t2['b'] = 2
m.copy_attributes(t2, memo={});  t2.ca is m.ca      # False
m.copy().ca is m.ca             # True   <- CommentedMap.copy() also shares
copy.deepcopy(m).ca is m.ca     # False
```

### `CommentedMap.insert(pos, key, value, comment=None)`

Inserts at `pos` in source order by appending then `move_to_end`-ing the tail; adjusts
`merge_pos` when a `<<` entry is present; `comment` is passed to `yaml_add_eol_comment`.

```python
m = load("a: 1\nb: 2\n"); m.insert(0, 'z', 0, comment='new z')
dump(m)                          # 'z: 0  # new z\na: 1\nb: 2\n'

m = load("base: &b {x: 1}\nd:\n  <<: *b\n  y: 2\n"); m['d'].insert(0, 'first', 9)
dump(m)                          # 'base: &b {x: 1}\nd:\n  first: 9\n  <<: *b\n  y: 2\n'
```

### `CommentedSeq.insert(idx, val)` / `sort(key=None, reverse=False)`

Both remap `ca.items` indices; see §3.1 and §3.4 for what that does and does not fix.

### `CommentedMap.mlget(key, default=None, list_ok=False)`

Multi-level `get` where `key` is a list of successive keys.

### Merge keys

```python
SRC = "base: &b\n  x: 1\n  y: 2\nderived:\n  <<: *b\n  y: 3\n"
d = load(SRC)
```
```
derived           = {'y': 3, 'x': 1}
derived.merge     = MergeValue([{'x': 1, 'y': 2}])
non_merged_items  = [('y', 3)]
derived['x']      = 1 (from merge)
'x' in derived    = True
round trip        = identical: True
merge_attrib type = MergeValue | merge_pos = 0
```

Merged-in keys are materialised into the `ordereddict` but excluded from `_ok`, so they
iterate **after** the own keys regardless of where `<<` appeared. Duplicate `<<` keys are
a hard error even with `allow_duplicate_keys=True`:

```
DuplicateKeyError: found duplicate merge key "<<" ...
Duplicate merge keys are never allowed, not even when `.allow_duplicate_keys` is set to True
```

### Other collection types

```python
d = load("? [a, b]\n: 1\n? {x: 1}\n: 2\n")
#   key ('a', 'b')            type=CommentedKeySeq  value=1
#   key ordereddict({'x': 1}) type=CommentedKeyMap  value=2
dump(d)     # '[a, b]: 1\n{x: 1}: 2\n'      != source (the '?' is dropped)

s = load("!!set\n? a  # about a\n? b\n")
#   type=CommentedSet | contents ['a','b'] | ca.items {'a': [CT('# about a\n' @L1,C5),None,None,None]}
dump(s) == "!!set\n? a  # about a\n? b\n"   # True
```

---

## 7. Scalar types

### 7.1 Constructors

```
LiteralScalarString(cls, value: Text, anchor=None)        style='|'  __slots__=('comment',)
FoldedScalarString(cls, value: Text, anchor=None)         style='>'  __slots__=('fold_pos','comment')
SingleQuotedScalarString(cls, value: Text, anchor=None)   style="'"  __slots__=()
DoubleQuotedScalarString(cls, value: Text, anchor=None)   style='"'  __slots__=()
PlainScalarString(cls, value: Text, anchor=None)          style=''   __slots__=()
ScalarInt(cls, *args, **kw)            # kw: width, underscore, anchor
BinaryInt(cls, value, width=None, underscore=None, anchor=None)
OctalInt(cls, value, width=None, underscore=None, anchor=None)
HexInt(cls, value, width=None, underscore=None, anchor=None)       # lower case a-f
HexCapsInt(cls, value, width=None, underscore=None, anchor=None)   # upper case A-F
DecimalInt(cls, value, width=None, underscore=None, anchor=None)
ScalarFloat(cls, *args, **kw)          # kw: width, prec, m_sign, m_lead0, exp,
                                       #     e_width, e_sign, underscore, anchor
ExponentialFloat(cls, value, width=None, underscore=None)
ExponentialCapsFloat(cls, value, width=None, underscore=None)
ScalarBoolean(cls, *args, **kw)        # kw: anchor
TaggedScalar(self, value=None, style=None, tag=None)
```

`ScalarString` subclasses `str`; the `*Int` and `ScalarBoolean` subclass `int`;
`ScalarFloat` subclasses `float`. **`TaggedScalar` subclasses nothing**: its MRO is
`['TaggedScalar', 'CommentedBase', 'object']`; it provides `__str__`, `__getitem__` and
`count` only.

Passing `anchor=` to any of them implies `always_dump=True`
(`ret_val.yaml_set_anchor(anchor, always_dump=True)`).

### 7.2 What a load produces and what it carries

```python
SRC = """\
lit: |
  line one
  line two
lit_strip: |-
  no trailing nl
lit_keep: |+
  keep

fold: >
  folded text
  continues
plain: hello
sq: 'single'
dq: "double\\n"
i: 42
hexi: 0x1f
hexcaps: 0X1F
octi: 0o17
octi_old: 017
bini: 0b1011
under: 1_000_000
f: 3.14
fexp: 1.5e+10
fpad: 3.14000
fneg: -0.5
b: true
tagged: !mytag some
tagged_map: !mymap {a: 1}
"""
d = load(SRC, preserve_quotes=True)
```

```
lit          LiteralScalarString        'line one\nline two\n' {'style': '|'}
lit_strip    LiteralScalarString        'no trailing nl'       {'style': '|'}
lit_keep     LiteralScalarString        'keep\n\n'             {'style': '|'}
fold         FoldedScalarString         'folded text continues\n' {'style': '>', 'fold_pos': [11]}
plain        str                        'hello'                {}
sq           SingleQuotedScalarString   'single'               {'style': "'"}
dq           DoubleQuotedScalarString   'double\n'             {'style': '"'}
i            int                        42                     {}
hexi         HexInt                     31                     {'_width': None, '_underscore': None}
hexcaps      str                        '0X1F'                 {}
octi         OctalInt                   15                     {'_width': None, '_underscore': None}
octi_old     ScalarInt                  17                     {'_width': 3, '_underscore': None}
bini         BinaryInt                  11                     {'_width': None, '_underscore': None}
under        ScalarInt                  1000000                {'_width': None, '_underscore': [3, False, False]}
f            ScalarFloat                3.14                   {'_width': 4, '_prec': 1, '_m_sign': False, '_m_lead0': 0, '_exp': None, '_e_width': None, '_e_sign': None, '_underscore': None}
fexp         ScalarFloat                15000000000.0          {'_width': 3, '_prec': 1, '_m_sign': False, '_m_lead0': 0, '_exp': 'e', '_e_width': 3, '_e_sign': True, '_underscore': None}
fpad         ScalarFloat                3.14                   {'_width': 7, '_prec': 1, '_m_sign': False, '_m_lead0': 0, '_exp': None, '_e_width': None, '_e_sign': None, '_underscore': None}
fneg         ScalarFloat                -0.5                   {'_width': 4, '_prec': 2, '_m_sign': '-', '_m_lead0': 0, '_exp': None, '_e_width': None, '_e_sign': None, '_underscore': None}
b            bool                       True                   {}
tagged       TaggedScalar               TaggedScalar(value='some', style=None, tag=Tag('!mytag')) {'style': None, 'lc': LineCol(None, None)}
tagged_map   CommentedMap               {'a': 1}               {'lc': LineCol(27, 12)}

round trip identical: True
```

Key facts:

- **A plain string, a decimal `int`, and a `bool` load as builtin `str`/`int`/`bool`**, not
  as `PlainScalarString`/`DecimalInt`/`ScalarBoolean`, even with `preserve_quotes=True`.
  Those three classes exist only for values the user constructs.
- Only `ScalarString` subclasses have `.style`. It is a **class** attribute, not an
  instance one; the style is the type.
- No scalar type carries `.lc` or `.ca` except `TaggedScalar` (which has both, via
  `CommentedBase`; `.lc` reads `LineCol(None, None)`).
- Every scalar type carries `.anchor` (an `Anchor`) and `yaml_anchor(any=False)`
  / `yaml_set_anchor(value, always_dump=False)`.

### 7.3 `_width` / `_prec` / `_m_sign` / `_exp` decoded

```
literal    type          w   p  m_sign m_lead0 exp  e_w  e_s  redump
3.14       ScalarFloat   4   1  False  0       None None None 'x: 3.14\n'
-0.5       ScalarFloat   4   2  '-'    0       None None None 'x: -0.5\n'
3.14000    ScalarFloat   7   1  False  0       None None None 'x: 3.14000\n'
0.001      ScalarFloat   5   1  False  3       None None None 'x: 0.001\n'
1.5e+10    ScalarFloat   3   1  False  0       'e'  3    True 'x: 1.5e+10\n'
1.5E-3     ScalarFloat   3   1  False  0       'E'  2    True 'x: 1.5E-3\n'
+2.5       ScalarFloat   4   2  '+'    0       None None None 'x: +2.5\n'
.5         ScalarFloat   2   0  False  0       None None None 'x: .5\n'
100.       ScalarFloat   4   3  False  0       None None None 'x: 100.\n'
1_000.5    ScalarFloat   7   5  False  0       None None None 'x: 01000.5\n'   <- WRONG
```

- `_width` = character width of the mantissa **as written** (used to zero-pad on output).
- `_prec` = 0-based index of the `.` within the mantissa.
- `_m_sign` = `'+'` / `'-'` if written, else `False`.
- `_m_lead0` = number of leading zeros after the point (`0.001` → 3).
- `_exp` = `'e'` / `'E'` / `None`; `_e_width` = width of the exponent field; `_e_sign` =
  whether the exponent carried an explicit sign.
- `_underscore` is **never set for floats**, so `1_000.5` loses its separator, and the
  retained `_width=7` then zero-pads it to `01000.5`.

```
literal      type          value      w     _underscore       redump
017          ScalarInt     17         3     None              'x: 017\n'
0017         ScalarInt     17         4     None              'x: 0017\n'
1_000_000    ScalarInt     1000000    None  [3, False, False] 'x: 1_000_000\n'
1_00_00      ScalarInt     10000      None  [2, False, False] 'x: 1_00_00\n'
0x_ff        HexInt        255        None  [2, True, False]  'x: 0x_ff\n'
12           int           12         -     -                 'x: 12\n'
+12          int           12         -     -                 'x: 12\n'      <- '+' lost
-12          int           -12        -     -                 'x: -12\n'
_100         str           '_100'     -     -                 'x: _100\n'
```

`_underscore` is `[group_width, leading_underscore, trailing_underscore]`.
`_width` for ints is the total digit width including leading zeros.

### 7.4 Hex/octal/binary edge cases

```
x: 0x1f    -> HexInt       31   redump='x: 0x1f\n'
x: 0x1F    -> HexCapsInt   31   redump='x: 0x1F\n'
x: 0X1f    -> str    '0X1f'     redump='x: 0X1f\n'      <- capital X not recognised as int
x: 0X1F    -> str    '0X1F'     redump='x: 0X1F\n'
x: 0xAB    -> HexCapsInt   171  redump='x: 0xAB\n'
x: -0x1F   -> HexCapsInt   -31  redump="x: !!int '0x-1F'\n"    <- sign inside the base prefix
```

Negative non-decimal ints re-emit with the minus sign **after** the base prefix and a
forced `!!int` tag:

```
x: -0x1F   value=-31   redump="x: !!int '0x-1F'\n"   reload -> -31 (HexCapsInt)
x: -0o17   value=-15   redump="x: !!int '0o-17'\n"   reload -> -15 (OctalInt)
x: -0b101  value=-5    redump="x: !!int '0b-101'\n"  reload -> -5  (BinaryInt)
```

ruamel reads its own output back correctly; no other YAML implementation will, because
`0x-1F` matches neither the YAML 1.1 nor the 1.2 int production.

`DecimalInt` is exported by `scalarint` but **has no representer**:

```python
m = CommentedMap(); m['dec'] = DecimalInt(7); dump(m)
# RepresenterError: cannot represent an object: 7
```

### 7.5 Constructed values → output

```python
m = CommentedMap()
m['lit']     = LiteralScalarString('a\nb\n')
m['fold']    = FoldedScalarString('long folded value here\n')
m['sq']      = SingleQuotedScalarString('sq')
m['dq']      = DoubleQuotedScalarString('dq')
m['plain']   = PlainScalarString('plain')
m['hex']     = HexInt(255)
m['hexw']    = HexInt(255, width=6)
m['hexcaps'] = HexCapsInt(255)
m['oct']     = OctalInt(8)
m['bin']     = BinaryInt(5)
m['us']      = ScalarInt(1234567, underscore=[3, False, False])
m['f']       = ScalarFloat(1.5, width=6, prec=3)
m['fexp']    = ScalarFloat(1.5e10, width=3, prec=1, exp='e', e_width=3, e_sign=True)
m['bt']      = ScalarBoolean(1)
m['bf']      = ScalarBoolean(0)
m['ts']      = TaggedScalar(value='v', style=None, tag=Tag(suffix='!custom'))
m['anch']    = LiteralScalarString('x\n', anchor='la')
print(dump(m))
```

```
lit: |
  a
  b
fold: >
  long folded value here
sq: 'sq'
dq: "dq"
plain: plain
hex: 0xff
hexw: 0x0000ff
hexcaps: 0xFF
oct: 0o10
bin: 0b101
us: 1_234_567
f: 001.50
fexp: 1.5e+10
bt: true
bf: false
ts: !custom v
anch: &la |
  x
```

`ScalarBoolean` is an `int`, not a `bool`:

```
ScalarBoolean(1) == True -> True | bool() -> True | isinstance(_, bool) -> False
```

### 7.6 Block scalars

```
'x: |\n  a\n'          value='a\n'    redump='x: |\n  a\n'          identical=True
'x: |-\n  a\n'         value='a'      redump='x: |-\n  a\n'         identical=True
'x: |+\n  a\n\n'       value='a\n\n'  redump='x: |+\n  a\n\n...\n'  identical=False
'x: |2\n   a\n'        value=' a\n'   redump='x: |2\n   a\n'        identical=True
'x: >-\n  a\n'         value='a'      redump='x: >-\n  a\n'         identical=True
```

`|+` at end of stream gains a spurious `...` document-end marker.

`LiteralScalarString.comment` holds the text between the `|` header and the newline
(**including** its leading whitespace, and **without** a trailing newline):

```python
SRC = "x: |  # header comment\n  body\n"
d = load(SRC)
type(d['x']).__name__      # 'LiteralScalarString'
d['x'].comment             # '  # header comment'
dump(d) == SRC             # True
```

`FoldedScalarString.fold_pos` records the character offsets in the *cooked* value where the
source had a fold, so the emitter can reproduce the original line breaks:

```python
SRC = "x: >\n  aaa bbb\n  ccc\n\n  ddd\n"
d = load(SRC)
str(d['x'])                # 'aaa bbb ccc\nddd\n'
d['x'].fold_pos            # [7, 11]
dump(d) == SRC             # True
```

A constructed `FoldedScalarString` has no `fold_pos` and is folded at `width` instead:

```python
m['x'] = FoldedScalarString('aaa bbb ccc ddd\n'); dump(m)   # 'x: >\n  aaa bbb ccc ddd\n'
```

### 7.7 Mutation and type preservation

`CommentedMap.__setitem__` / `CommentedSeq.__setsingleitem__` re-wrap a plain `str`
assigned over an existing `ScalarString` in the **old value's** type:

```python
s = load("x: 'quoted'\n", preserve_quotes=True); s['x'] = 'newvalue'
type(s['x']).__name__      # 'SingleQuotedScalarString'
dump(s, preserve_quotes=True)   # "x: 'newvalue'\n"
```

`ScalarString.replace` preserves the type; every other `str` method does not:

```python
type(s2['x'].replace('q','Q')).__name__   # 'SingleQuotedScalarString'
type(s2['x'].upper()).__name__            # 'str'
```

`ScalarInt` preserves `_width`/`_underscore` through `__iadd__`, `__isub__`, `__imul__`,
`__ipow__`, `__ifloordiv__` only. `ScalarFloat` defines the same five but every one of them
`return float(self) op a` on the first line; the type-preserving code below is dead.

---

## 8. `register_class`

### 8.1 How it registers

`main.py:777-803`:

```python
def register_class(self, cls):
    tag = getattr(cls, 'yaml_tag', '!' + cls.__name__)
    try:
        self.representer.add_representer(cls, cls.to_yaml)
    except AttributeError:
        def t_y(representer, data):
            return representer.represent_yaml_object(tag, data, cls,
                                                     flow_style=representer.default_flow_style)
        self.representer.add_representer(cls, t_y)
    try:
        self.constructor.add_constructor(tag, cls.from_yaml)
    except AttributeError:
        def f_y(constructor, node):
            return constructor.construct_yaml_object(node, cls)
        self.constructor.add_constructor(tag, f_y)
    return cls
```

So:

- The registry key for **loading** is the string tag, defaulting to `'!' + cls.__name__`.
  `cls.yaml_tag` overrides it. There is no `tag=` / `source=` parameter.
- The registry key for **dumping** is the class object, so dumping is unambiguous.
- `cls.to_yaml(representer, data)` / `cls.from_yaml(constructor, node)` classmethod hooks
  are used when present.

Both writers are **classmethods that mutate a class-level dict**:

```python
@classmethod
def add_representer(cls, data_type, representer):
    if 'yaml_representers' not in cls.__dict__:
        cls.yaml_representers = cls.yaml_representers.copy()
    cls.yaml_representers[data_type] = representer

@classmethod
def add_constructor(cls, tag, constructor):
    if isinstance(tag, Tag): tag = str(tag)
    if 'yaml_constructors' not in cls.__dict__:
        cls.yaml_constructors = cls.yaml_constructors.copy()
    ret_val = cls.yaml_constructors.get(tag, None)     # previous value, discarded
    cls.yaml_constructors[tag] = constructor
    return ret_val
```

`self.representer` / `self.constructor` are instances of `RoundTripRepresenter` /
`RoundTripConstructor`, so the registration lands on the **class** and is
**process-global**:

```python
class Thing:
    def __init__(self, v=0): self.v = v
y1 = YAML(); y1.register_class(Thing)
y2 = YAML()
'!Thing' in y2.constructor.yaml_constructors    # True
Thing in y2.representer.yaml_representers       # True
```

`add_constructor` returns the constructor it displaced; `register_class` throws that away,
so an overwrite is detectable but never reported.

Hooks work as documented:

```python
class Custom:
    yaml_tag = '!custom'
    def __init__(self, a=None): self.a = a
    @classmethod
    def to_yaml(cls, representer, node):
        return representer.represent_scalar(cls.yaml_tag, f'{node.a}')
    @classmethod
    def from_yaml(cls, constructor, node):
        return cls(a=node.value)
y = YAML(); y.register_class(Custom)
dump({'k': Custom('hi')})       # "k: !custom hi\n"
y.load("k: !custom hi\n")['k'].a  # 'hi'
```

An **unregistered** tag round-trips untouched:

```python
src = 'a: !Unknown\n  x: 1\nb: !Un2 scalar\nc: !Un3 [1, 2]\n'
d = YAML().load(src)
#   a: CommentedMap  tag=Tag('!Unknown')
#   b: TaggedScalar  tag=Tag('!Un2')
#   c: CommentedSeq  tag=Tag('!Un3')
dump(d) == src                  # True
```

### 8.2 The collision bug: a runnable repro

The registry is keyed on `cls.__name__`, so two classes of the same name from different modules
overwrite each other. This is the case [the tag registry](../guide/custom-classes.md) is built
around; [Architecture](index.md) has the design. Three files in a scratch directory:

```
libx/__init__.py        (empty)
libx/circuits.py:
    class Circuit:
        """libx's Circuit: has .qubits"""
        def __init__(self, qubits=0): self.qubits = qubits

liby/__init__.py        (empty)
liby/circuits.py:
    class Circuit:
        """liby's Circuit: has .n"""
        def __init__(self, n=0): self.n = n
```

```python
import io, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ruamel.yaml import YAML
from libx.circuits import Circuit as XCircuit
from liby.circuits import Circuit as YCircuit

print('XCircuit is YCircuit:', XCircuit is YCircuit)
print('both register under tag:', '!' + XCircuit.__name__, '/', '!' + YCircuit.__name__)

y = YAML()
y.register_class(XCircuit)     # import order: libx first
y.register_class(YCircuit)     # liby second -> overwrites the constructor for '!Circuit'

buf = io.StringIO()
y.dump({'x': XCircuit(qubits=2), 'y': YCircuit(n=3)}, buf)
doc = buf.getvalue()
print('--- dumped ---'); print(doc, end='')

back = y.load(doc)
print('--- loaded back ---')
for k, v in back.items():
    print(f'  {k}: constructed as {type(v).__module__}.{type(v).__qualname__}  __dict__={v.__dict__}')

assert type(back['y']) is YCircuit
assert type(back['x']) is YCircuit, "expected the collision"

y2 = YAML(); y2.register_class(YCircuit); y2.register_class(XCircuit)
back2 = y2.load(doc)
print('reverse registration order ->', type(back2['x']).__module__, '/', type(back2['y']).__module__)
```

```
XCircuit is YCircuit: False
both register under tag: !Circuit / !Circuit
constructor table for !Circuit -> YAML.register_class.<locals>.f_y ruamel.yaml.main
--- dumped ---
x: !Circuit
  qubits: 2
y: !Circuit
  n: 3
--- loaded back ---
  x: constructed as liby.circuits.Circuit  __dict__={'qubits': 2}
  y: constructed as liby.circuits.Circuit  __dict__={'n': 3}

BUG CONFIRMED: 'x' was dumped from libx.circuits.Circuit and loaded as
liby.circuits.Circuit with the wrong attribute set. Which class wins is pure
registration order.
reverse registration order -> libx.circuits / libx.circuits
```

`back['x']` is a `liby.circuits.Circuit` carrying `libx`'s attribute set, and accessing
`.n` on it raises `AttributeError`. Nothing warns; the winner is decided by import order,
and because the registry is class-level (§8.1) even two *independent* `YAML()` instances
in one process collide.

This is the regression test yamluna must pass: the same document must construct
`libx.circuits.Circuit` for `x` and `liby.circuits.Circuit` for `y`, or refuse with an
error naming both candidates.

---

## 9. `YAML` attributes that affect rt output

Defaults on a fresh `YAML()`:

```
typ                    = ['rt']            pure                   = False
allow_unicode          = True              allow_duplicate_keys   = False
default_flow_style     = False             default_style          = None
block_seq_indent       = 0                 map_indent             = None
sequence_indent        = None              sequence_dash_offset   = 0
top_level_colon_align  = None              prefix_colon           = None
version                = None              preserve_quotes        = None
width                  = None              explicit_start         = None
explicit_end           = None              encoding               = 'utf-8'
line_break             = None              compact_seq_seq        = None
compact_seq_map        = None              old_indent             = None
```

### 9.1 `indent(mapping=, sequence=, offset=)`

Sets `map_indent`, `sequence_indent`, `sequence_dash_offset`. Source:

```yaml
top:
  nested:
    - a
    - b:
        c: 1
list:
  - 1
  - 2
```

| setting | output |
|---|---|
| default | `top:`⏎`  nested:`⏎`  - a`⏎`  - b:`⏎`      c: 1`⏎`list:`⏎`- 1`⏎`- 2` |
| `mapping=4` | `top:`⏎`    nested:`⏎`    - a`⏎`    - b:`⏎`          c: 1`⏎`list:`⏎`- 1`⏎`- 2` |
| `sequence=4, offset=2` | `top:`⏎`  nested:`⏎`    - a`⏎`    - b:`⏎`        c: 1`⏎`list:`⏎`  - 1`⏎`  - 2` |
| `mapping=4, sequence=6, offset=3` | `top:`⏎`    nested:`⏎`       -  a`⏎`       -  b:`⏎`              c: 1`⏎`list:`⏎`   -  1`⏎`   -  2` |

Rule: the item column is `parent_indent + sequence`; the `-` column is
`parent_indent + offset`. `offset >= sequence` is not validated.

**The default output does not preserve the source's sequence indentation**: the input
above has `  - a` and the default dump produces `- a`. Sequence indentation is a global
emitter setting, never a per-node property.

### 9.2 `width`

```
source: 'text: this is quite a long plain scalar value that will be folded by the emitter when it exceeds the width\n'
width=None -> 'text: this is quite a long plain scalar value that will be folded by the emitter\n  when it exceeds the width\n'
width=20   -> 'text: this is quite \n  a long plain \n  scalar value that \n  will be folded by \n  the emitter when \n  it exceeds the \n  width\n'
width=40   -> 'text: this is quite a long plain scalar \n  value that will be folded by the \n  emitter when it exceeds the width\n'
width=80   -> 'text: this is quite a long plain scalar value that will be folded by the emitter\n  when it exceeds the width\n'
width=200  -> 'text: this is quite a long plain scalar value that will be folded by the emitter when it exceeds the width\n'
```

`YAML().width` is `None`; `Emitter.__init__` then does `self.best_width = 80`, and
`width=None` and `width=80` produce identical output. Folding leaves a **trailing space before the break**.
It applies to plain and double-quoted scalars but not to a loaded block scalar, which keeps
its source line breaks via `fold_pos` / literal lines:

```
width=20 on 'text: |\n  a very long literal line ...\n'  ->  unchanged
width=20 on 'text: "a very long double quoted scalar that goes past the limit"\n'
    -> 'text: "a very long double\n  quoted scalar that goes\n  past the limit"\n'
```

### 9.3 `preserve_quotes`

```
source        : 'a: \'single\'\nb: "double"\nc: plain\nd: "1"\n'
preserve=False: "a: single\nb: double\nc: plain\nd: '1'\n"
preserve=True : 'a: \'single\'\nb: "double"\nc: plain\nd: "1"\n'

types, preserve_quotes=False: {'a': 'str', 'b': 'str', 'c': 'str', 'd': 'str'}
types, preserve_quotes=True : {'a': 'SingleQuotedScalarString', 'b': 'DoubleQuotedScalarString',
                               'c': 'str', 'd': 'DoubleQuotedScalarString'}
```

`preserve_quotes=False` still quotes where it must (`"1"` → `'1'`), it just picks its own
style. A plain scalar stays a plain `str` either way.

### 9.4 `default_flow_style`

```
source (all block): 'a:\n  b: 1\n  c:\n    - 1\n    - 2\n'
default_flow_style=None  -> 'a:\n  b: 1\n  c:\n  - 1\n  - 2\n'
default_flow_style=False -> 'a:\n  b: 1\n  c:\n  - 1\n  - 2\n'
default_flow_style=True  -> 'a:\n  b: 1\n  c:\n  - 1\n  - 2\n'
```

The setting has **no effect on loaded containers**: each carries `.fa._flow_style` from
the source and `Format.flow_style(default)` returns the stored value. It applies only to
containers the user creates:

```python
y = YAML(); y.default_flow_style = True
d = y.load(S); d['a']['new'] = {'x': 1}      # -> '...\n  new: {x: 1}\n'
y = YAML(); y.default_flow_style = False
d = y.load(S); d['a']['new'] = {'x': 1}      # -> '...\n  new:\n    x: 1\n'
```

### 9.5 `explicit_start` / `explicit_end`

```
{}                                             -> 'a: 1\n'
{'explicit_start': True}                       -> '---\na: 1\n'
{'explicit_end': True}                         -> 'a: 1\n...\n'
{'explicit_start': True, 'explicit_end': True} -> '---\na: 1\n...\n'
{'explicit_start': False, 'explicit_end': False} -> 'a: 1\n'
```

**Not round-tripped.** A source that already has the markers loses them:

```
'---\na: 1\n'         -> 'a: 1\n'
'---\na: 1\n...\n'    -> 'a: 1\n'
```

`dump_all` inserts `---` between documents regardless:
`YAML().dump_all([{'a':1},{'b':2}])` → `'a: 1\n---\nb: 2\n'`.

### 9.6 `version`

```
YAML().version default = None
no directive, version unset      -> 'a: 1\n'
version=(1, 2)                   -> '%YAML 1.2\n---\na: 1\n'
version='1.1'                    -> '%YAML 1.1\n---\na: 1\n'
source has %YAML 1.2, dump plain -> '%YAML 1.2\n---\na: 1\n'
```

Setting `version` forces both the directive **and** `---`. An in-document `%YAML`
directive is preserved. The version changes resolution:

```python
src = "o: 012\nb: on\nn: no\ny: yes\n"
version=(1, 1) -> {'o': ('OctalInt', 10), 'b': ('bool', True), False: ('bool', False), True: ('bool', True)}
version=(1, 2) -> {'o': ('ScalarInt', 12), 'b': ('str', 'on'), 'n': ('str', 'no'), 'y': ('str', 'yes')}
version=None   -> {'o': ('ScalarInt', 12), 'b': ('str', 'on'), 'n': ('str', 'no'), 'y': ('str', 'yes')}
```

Under 1.1 the **keys** `no` and `yes` resolve to `False` and `True`. An in-document
directive has the same effect and survives the round trip:

```python
d = YAML().load("%YAML 1.1\n---\no: 012\nb: on\n")
# {'o': ('OctalInt', 10), 'b': ('bool', True)}
dump(d)     # '%YAML 1.1\n---\no: 012\nb: true\n'      note: 'on' -> 'true'
```

### 9.7 `allow_duplicate_keys`

```
default allow_duplicate_keys = False
"a: 1\nb: 2\na: 3\n" default    -> DuplicateKeyError: while constructing a mapping
"a: {x: 1, x: 2}\n"  default    -> DuplicateKeyError: while constructing a mapping
"a: 1\nb: 2\na: 3\n" allow=True -> {'a': 1, 'b': 2} | warnings: []
                       redump   -> 'a: 1\nb: 2\n'
```

With `allow_duplicate_keys=True` the **first** occurrence wins, the second is discarded
with no warning, and the dump silently loses it. Duplicate `<<` merge keys are rejected
even with the flag set (§6).

### 9.8 Errors

```python
try: YAML().load("a: [1, 2\n")
except Exception as e: ...
```

```
mro: (ParserError, MarkedYAMLError, YAMLError, Exception, BaseException)
problem_mark:   in "<unicode string>", line 2, column 1:

    ^ (line: 2)
mark attrs: name='<unicode string>' line=1 column=0 index=9 pointer=9
get_snippet(): '    \n    ^ (line: 2)'
```

`Mark.line` and `Mark.column` are **0-based**; the rendered message adds 1 to both.
`Mark.index` and `Mark.pointer` are the same value, a character offset.
`ruamel.yaml`'s top-level namespace exports only `YAMLError`; the rest live in
`ruamel.yaml.error`, `.parser`, `.scanner`, `.composer`, `.constructor`, `.representer`.

---

## 10. Additional surprising behaviour

### 10.1 Anchors used once are dropped

See §5.2. `other: &unused\n  y: 2\n` → `other:\n  y: 2\n`.

### 10.2 Comments after `...` are destroyed

```
'a: 1\n...\n# after end marker\n' -> ca.end = [] ; dump -> 'a: 1\n'
```

And `'a: 1\n...\n# after\n...\n'` raises `ParserError` on load.

### 10.3 Dumping mutates `.ca`

`representer.py:738-746` assigns `node.comment = comment.comment` (the *same list object*
when it was `None`-checked into the first branch) and then `node.comment.append(comment.end)`.
Each dump appends another element to the object's own `ca.comment`:

```python
m = load("# lead\na: 1\n")
after load : [None, [CT('# lead\n' @L0,C0)]]
after dump1: [None, [CT('# lead\n' @L0,C0)], []]
after dump2: [None, [CT('# lead\n' @L0,C0)], [], []]
after dump3: [None, [CT('# lead\n' @L0,C0)], [], [], []]
d1 == d2 == d3: True
```

Output is stable, but `.ca.comment` grows without bound and no longer matches its
documented `[post, [pre]]` shape, so code that does `post, pre = obj.ca.comment` breaks
after the first dump.

### 10.4 Explicit keys (`? key`) are dropped, sometimes producing unparseable YAML

```
'? gamma\n: 3\n'             -> 'gamma: 3\n'                          reparses
'? [a, b]\n: 1\n'            -> '[a, b]: 1\n'                         reparses
'? |\n  multi\n  line\n: 1\n'-> unchanged                             reparses
'? gamma  # c\n: 3\n'        -> 'gamma    # c\n: 3\n'                 REPARSE FAILS
```

The last one: `ParserError: expected '<document start>', but found ('<block mapping start>',)`.
A load→dump cycle turned a valid document into an invalid one.

### 10.5 Sequence indentation is not round-tripped

```
source: 'delta:\n  - x\n  - y\n'   dump: 'delta:\n- x\n- y\n'
```

Worse, an own-line comment inside that sequence keeps its **source** indentation while the
items move to column 0:

```
delta:
- x             # 8 eol on a seq item
  # 9 between seq items
- y
```

### 10.6 The `d['beta'].ca.comment` four-element form

`Comment.comment` is documented as `[post, [pre]]`, but after a load involving a nested
collection *and* a dump it can be `[post, None, [], []]` (§1.7). There is no single shape
consumers can rely on.

### 10.7 Merged keys iterate out of source order

```python
d = load("base: &b\n  x: 1\n  y: 2\nderived:\n  <<: *b\n  y: 3\n")
dict(d['derived'])      # {'y': 3, 'x': 1}
```

`<<` appears first in the source, but merged-in keys iterate last.

### 10.8 `1_000.5` gains a leading zero

See §7.3. `x: 1_000.5` → `x: 01000.5`.

---

## 11. Snippet index

Every code block above is a complete experiment: paste the preamble from the top of this
document in front of it and it runs against `ruamel.yaml` 0.19.1 and reproduces the output
shown beneath it. The groupings:

| § | what it measures |
|---|---|
| 1 | `.ca` shape, all four slots, mapping and sequence, `ca.comment` / `ca.end` / `ca.pre` |
| 2 | blank-line encoding, leading/trailing blank loss, whitespace-only lines |
| 3 | insert/delete/pop/rename/move_to_end/reverse drift, stale-entry resurrection |
| 4 | `.lc` line/col/data/key/value/item, 0-basing, staleness after mutation |
| 5 | `.fa` tri-state, `.anchor` + `always_dump`, unused-anchor loss |
| 6 | every public `yaml_*` method, `copy_attributes`, `insert`, merge keys, complex keys, sets |
| 7 | every scalar type: constructor, attributes, load result, round trip, hex/oct/bin edges |
| 8 | `register_class` internals and the two-module `!Circuit` collision |
| 9 | `indent`/`width`/`preserve_quotes`/`default_flow_style`/`explicit_*`/`version`/`allow_duplicate_keys` |
| 10 | dump-mutates-`.ca`, explicit-key corruption, sequence-indent loss |
