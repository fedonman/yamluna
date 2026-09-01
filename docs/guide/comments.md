# Comments and blank lines

A comment describes the thing written under it or beside it. yamluna files it on that thing.
Every entry of a container owns its trivia, and the record travels with the entry: nothing is
keyed by line number, nothing is keyed by list index. Insert, delete, sort, reverse, rename
and move all leave each comment on the element it was written for, with one exception that
[has its own section](#the-one-position-whose-ownership-is-wrong) at the bottom of this page.

A blank line is trivia too. It arrives as a `CommentToken` of its own with `is_blank_line`
set, one token per blank line, so "how many blank lines were here" has an answer and the
answer survives the round trip.

## Comments follow the element

Take a sequence where each host is introduced by a comment and annotated by another:

```yaml
hosts:
  - web-01      # eu-west
  # internal only
  - worker-01   # eu-west
  # nightly batch
  - cron-01     # us-east
```

```python
from yamluna import YAML

yaml = YAML()
doc = yaml.load(SRC)          # SRC is the YAML above
doc['hosts'].reverse()
print(yaml.dump(doc))
```

Reversing the list reverses the comments with it. `ruamel.yaml` 0.19.1 keys the records by
index and glues each own-line comment into the previous element's end-of-line token, so the
same script relabels every host, and re-indents the block while it is at it:

=== "yamluna"

    ```yaml
    hosts:
      # nightly batch
      - cron-01     # us-east
      # internal only
      - worker-01   # eu-west
      - web-01      # eu-west
    ```

=== "ruamel.yaml 0.19.1"

    ```yaml
    hosts:
    - cron-01       # eu-west
      # internal only
    - worker-01     # eu-west
      # nightly batch
    - web-01        # us-east
    ```

`cron-01` is in `us-east` and has come back labelled `eu-west`.

Deleting is the same story. `del doc['hosts'][1]` takes `worker-01` and the comment that
introduces it, and touches nothing else. ruamel destroys the comment that introduced
`cron-01`, and leaves `# internal only`, which introduced the item that was just deleted,
sitting above `cron-01`:

=== "yamluna"

    ```yaml
    hosts:
      - web-01      # eu-west
      # nightly batch
      - cron-01     # us-east
    ```

=== "ruamel.yaml 0.19.1"

    ```yaml
    hosts:
    - web-01        # eu-west
      # internal only
    - cron-01       # us-east
    ```

Mappings key the store by the entry key, which never shifts, so the same holds for
`del`, `pop`, `popitem`, `rename`, `move_to_end` and `insert`. Given:

```yaml
services:
  web: 8080       # public
  # only reachable inside the VPC
  worker: 9000
  # runs the nightly jobs
  cron: 9100
```

`del doc['services']['worker']` followed by `doc['services'].move_to_end('web')` gives:

=== "yamluna"

    ```yaml
    services:
      # runs the nightly jobs
      cron: 9100
      web: 8080       # public
    ```

=== "ruamel.yaml 0.19.1"

    ```yaml
    services:
      cron: 9100
      web: 8080       # public
      # only reachable inside the VPC
    ```

ruamel lost the comment that introduced `cron` and dragged the one that introduced `worker`
to the end of the document, where it now describes nothing.

Deleting an entry here also drops its record, so re-adding the key later does not resurrect a
stale comment. `del d['b']` then `d['b'] = 'brand new unrelated value'` on
`"a: 1\n# secret about b\nb: 2   # eol b\nc: 3\n"` gives `b: brand new unrelated value` with
nothing attached; ruamel gives it back `# eol b` and leaves `# secret about b` on `c`.

The full list of the ruamel defects this model exists to avoid, each with a repro, is in
[Behaviour differences](../migrating/differences.md).

## What `.ca` holds

`.ca` is a `Comment`, and it keeps ruamel's shape so ported code goes on working. Three
things hang off it.

| Attribute | Holds |
| --- | --- |
| `.ca.items[entry]` | a four-slot record per entry of the container, keyed by mapping key or by current index |
| `.ca.comment` | the node's own trivia, `[eol_token, [own-line tokens above the node]]`, or `None` |
| `.ca.end` | the trailing trivia after the node's last entry, a list |

The four slots of a record, with the constants from `yamluna.comments`:

| Slot | Mapping | Sequence | Holds |
| --- | --- | --- | --- |
| 0 | `C_KEY_EOL` | `C_ELEM_EOL` | one token: the end-of-line comment after the key, or after the element |
| 1 | `C_KEY_PRE` | `C_ELEM_PRE` | a list: the own-line comments and blank lines above the key |
| 2 | `C_VALUE_EOL` | not used | one token: the end-of-line comment after the value |
| 3 | `C_VALUE_POST` | `C_ELEM_POST` | a list: the own-line comments below the value |

A sequence element has no key half, so its own end-of-line comment sits in slot 0.

`.ca.items` is a projection over the identity-keyed store, not a copy. For a mapping it is
the store; for a sequence it is a write-through view keyed by the current index, so only the
indices that carry a record appear in it. Either way, editing a record in place edits the
document.

## Reading a comment

```python
from yamluna import YAML
from yamluna.comments import C_KEY_PRE, C_VALUE_EOL

SRC = """\
# deployment settings
name: demo

# how many copies to run
replicas: 3        # bumped for the launch
ports:
  - 80             # http
  - 443            # https

# end of file
"""

yaml = YAML()
doc = yaml.load(SRC)

print('comment:', doc.ca.comment)
print('items:  ', dict(doc.ca.items))
print('end:    ', doc.ca.end)
print('ports:  ', dict(doc['ports'].ca.items))
print('pre:    ', doc.ca.get('replicas', C_KEY_PRE))
print('eol:    ', doc.ca.get('replicas', C_VALUE_EOL))
print('blank?  ', [t.is_blank_line for t in doc.ca.items['replicas'][C_KEY_PRE]])
print('same?   ', yaml.dump(doc) == SRC)
```

```text
comment: [None, [CommentToken('# deployment settings\n', col=0)]]
items:   {'replicas': [None, [CommentToken('\n', col=0), CommentToken('# how many copies to run\n', col=0)], CommentToken('# bumped for the launch', col=19), None]}
end:     [CommentToken('\n', col=0), CommentToken('# end of file\n', col=0)]
ports:   {0: [CommentToken('# http', col=19), None, None, None], 1: [CommentToken('# https', col=19), None, None, None]}
pre:     [CommentToken('\n', col=0), CommentToken('# how many copies to run\n', col=0)]
eol:     CommentToken('# bumped for the launch', col=19)
blank?   [True, False]
same?    True
```

Points worth taking from that output:

* The blank line before `# how many copies to run` is a separate token in the same list, and
  `is_blank_line` tells them apart. Loading the same document, ruamel gives `name` the single
  token `CommentToken('\n\n# how many copies to run\n')`: the blank line, the comment and the
  ownership all folded into one string on the wrong entry.
* An own-line token keeps its trailing newline and an end-of-line token does not, which is
  ruamel's convention.
* `.column` on a token is the 0-based column of the `#`, which is how the alignment at column
  19 comes back.
* Reading `.ca` does not disturb the document. Every attribute is created empty on first
  access, and the dump is still byte-identical afterwards. ruamel's `.ca` differs before and
  after a dump of the same document.

`.ca.get(entry, slot)` returns `None` rather than raising when the entry has no record.
`text in node.ca` searches every token attached to the node, its entries included.

## Setting a comment

Four methods cover most of what you want, and they are ruamel's:

| Call | Writes |
| --- | --- |
| `node.yaml_add_eol_comment(text, key)` | slot 2 of `key`'s record, slot 0 in a sequence |
| `node.yaml_set_comment_before_after_key(key, before=..., after=...)` | slots 1 and 3 of `key`'s record |
| `node.yaml_set_start_comment(text)` | the block above the node, replacing what is there |
| `node.yaml_end_comment_extend(tokens)` | appends to `.ca.end` |

`yaml_add_eol_comment` adds the `#` for you and, given no `column`, lines the comment up with
the nearest neighbouring end-of-line comment. `yaml_set_comment_before_after_key` appends to
the slot rather than replacing it, one token per line of the text, and a lone `'\n'` in
`before` gets you a blank line.

Continuing with the same document:

```python
from yamluna import CommentMark, CommentToken

doc = yaml.load(SRC)
doc.yaml_add_eol_comment('shown in the UI', 'name')
doc.yaml_set_comment_before_after_key('ports', before='\nthe listening sockets')
doc['ports'].yaml_add_eol_comment('https override', 1)
doc.yaml_end_comment_extend([CommentToken('# generated, do not edit\n', CommentMark(0))])
print(yaml.dump(doc))
```

```yaml
# deployment settings
name: demo         # shown in the UI

# how many copies to run
replicas: 3        # bumped for the launch

# the listening sockets
ports:
  - 80             # http
  - 443            # https override

# end of file
# generated, do not edit
```

To clear a comment, or to write a slot the methods do not reach, go through
`.ca.set(entry, slot, value)`: a `CommentToken` for the end-of-line slots, a list of them for
the own-line slots, `None` to empty a slot. Clearing the end-of-line comment on `replicas` is

```python
from yamluna.comments import C_VALUE_EOL

doc.ca.set('replicas', C_VALUE_EOL, None)
```

and the next dump has `replicas: 3` with nothing after it.

`CommentedMap.insert(pos, key, value, comment=...)` sets the new entry's end-of-line comment as
it inserts, which saves a second call.

!!! note

    `node.yaml_add_eol_comment(text)` with no `key` puts the token on the node's own
    `ca.comment[0]`, which the emitter only reads when the parent has no record for that
    entry. For a mapping value, comment it from its parent, `parent.yaml_add_eol_comment(text,
    key)`, and it lands where you meant it.

## The one position whose ownership is wrong

An own-line comment above a collection's **first** child is filed on the collection, in its
`ca.comment[1]`, rather than on that child's `before` slot. Everything else on this page
holds; this one position does not.

```python
SRC = """\
hosts:
  # internal only
  - worker-01
  # nightly batch
  - cron-01
"""
doc = yaml.load(SRC)
print(doc['hosts'].ca.comment)
print(dict(doc['hosts'].ca.items))
del doc['hosts'][0]
print(yaml.dump(doc))
```

```text
[None, [CommentToken('# internal only\n', col=2)]]
{1: [None, [CommentToken('# nightly batch\n', col=2)], None, None]}
hosts:
  # internal only
  # nightly batch
  - cron-01
```

`# nightly batch` moved correctly. `# internal only` belongs to `worker-01`, is filed on
`hosts`, and so stays at the top of the block after `worker-01` is gone. Deleting or moving a
first child drifts that one comment, which is the ruamel defect the rest of the model avoids.

Every byte still round-trips: an unedited load and dump of that document is byte-identical, and
the comment is not lost or duplicated. What is wrong is the ownership, for one position.

Twelve xfails in
[`tests/test_mutation.py`](https://github.com/fedonman/yamluna/blob/main/tests/test_mutation.py)
pin the two known gaps and fail if either starts passing: eight for this one, four for a
separate emitter gap where inserting into a sequence strands the `-` of the item before an
own-line comment onto its own line. Until both are closed, prefer `append` and `move_to_end`
to `insert(0, …)`, and check the diff after an insertion into a commented sequence.

## Where to look next

* [Loading and dumping](load-and-dump.md) for the round-trip guarantee the comments ride on.
* [The document model](../internals/document-model.md) for the trivia slots the Rust core
  carries and how they map onto `.ca`.
* [Containers](../api/containers.md) for the generated reference on `Comment`,
  `CommentToken` and the container methods.
* [`examples/comments.py`](https://github.com/fedonman/yamluna/blob/main/examples/comments.py)
  is this page as one runnable script, with its real output at the bottom.
