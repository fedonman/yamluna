# Anchors, aliases and merge keys

`&name` marks a node, `*name` refers back to it, and `<<` merges one mapping into another.
yamluna keeps all three as references. An alias loads as the very object its anchor named and
dumps back as `*name`; a merge key is recorded and re-emitted as `<<: *name`; an anchor that
is in the source is in the output, whether anything refers to it or not. Nothing is expanded
and nothing is dropped.

## An alias is the same object

```python
from yamluna import YAML

SRC = """\
defaults: &defaults
  retries: 3
  timeout: 30
web: *defaults
"""

yaml = YAML()
doc = yaml.load(SRC)

doc['web'] is doc['defaults']   # True
doc['defaults'].anchor          # Anchor('defaults', (always dump))
yaml.dump(doc) == SRC           # True
```

Because it is the same object, editing through the alias edits the anchored node, and the
dump still writes one copy and one reference:

```python
doc['web']['retries'] = 10
print(yaml.dump(doc))
```

```yaml
defaults: &defaults
  retries: 10
  timeout: 30
web: *defaults
```

That is usually what you want from a file that uses anchors. When it is not, copy the node and
clear the anchor on the copy, or you get two definitions of `&defaults` in the output and the
second shadows the first:

```python
import copy

doc = yaml.load(SRC)
doc['web'] = copy.deepcopy(doc['defaults'])
doc['web'].yaml_set_anchor(None)
doc['web']['retries'] = 10
print(yaml.dump(doc))
```

```yaml
defaults: &defaults
  retries: 3
  timeout: 30
web:
  retries: 10
  timeout: 30
```

## An anchor is source text, so it is emitted

`ruamel.yaml` emits `&name` only for an object it has seen more than once, so an anchor that
nothing refers to yet, the usual shape of a deliberate extension point, disappears on the
first round trip. yamluna emits every anchor that is set, for the same reason it reproduces
quoting: it is in the file.

Load and dump this, unchanged:

```yaml
defaults: &defaults
  retries: 3
  timeout: 30

# referred to by ops/overrides.yaml
extension_point: &hooks
  before: []

web:
  <<: *defaults
  timeout: 5
```

=== "yamluna"

    ```yaml
    defaults: &defaults
      retries: 3
      timeout: 30

    # referred to by ops/overrides.yaml
    extension_point: &hooks
      before: []

    web:
      <<: *defaults
      timeout: 5
    ```

=== "ruamel.yaml 0.19.1"

    ```yaml
    defaults: &defaults
      retries: 3
      timeout: 30

    # referred to by ops/overrides.yaml
    extension_point:
      before: []

    web:
      <<: *defaults
      timeout: 5
    ```

The comment survived in both. The anchor the comment is about did not survive ruamel.

`Anchor.always_dump` is still on the object, and `yaml_set_anchor(name, always_dump=True)`
still accepts the flag, so ported code runs. It no longer decides anything: an anchor that is
set is emitted either way.

## Merge keys are recorded, never expanded

`<<: *defaults` is kept as an entry with a merge flag. The merged keys read through, so the
mapping behaves like the merged result, but they are not entries of it and they are not
written into the output.

```python
SRC = """\
defaults: &defaults
  retries: 3
  timeout: 30
web:
  <<: *defaults
  timeout: 5
"""

doc = yaml.load(SRC)
web = doc['web']

print('web["retries"]      ', web['retries'])
print('"retries" in web    ', 'retries' in web)
print('list(web)           ', list(web))
print('non_merged_items()  ', list(web.non_merged_items()))
print('web.merge           ', web.merge)
print('merge[0] is defaults', web.merge[0] is doc['defaults'])
print('merge.merge_pos     ', web.merge.merge_pos)
print('identical           ', yaml.dump(doc) == SRC)
```

```text
web["retries"]       3
"retries" in web     True
list(web)            ['timeout', 'retries']
non_merged_items()   [('timeout', 5)]
web.merge            [{'retries': 3, 'timeout': 30}]
merge[0] is defaults True
merge.merge_pos      0
identical            True
```

`.merge` is the list of mappings pulled in, in source order, and `merge_pos` is the position
the `<<` key held among its siblings, which is where the dump puts it back. Merged keys read
through but iterate after the mapping's own keys, which is why `list(web)` is
`['timeout', 'retries']` rather than source order; `non_merged_items()` gives you the entries
that are really this mapping's.

Assigning a key that came from the merge writes a local override into this mapping and leaves
the merge alone:

```python
web['retries'] = 10
print(yaml.dump(doc))
```

```yaml
defaults: &defaults
  retries: 3
  timeout: 30
web:
  <<: *defaults
  timeout: 5
  retries: 10
```

`<<: [*base, *extra]`, a merge inside a flow mapping, and a comment on the `<<` entry itself
all round-trip; see
[`tests/corpus/anchors-merge.yaml`](https://github.com/fedonman/yamluna/blob/main/tests/corpus/anchors-merge.yaml)
for the shapes that are pinned.

## Recursive anchors

A container is registered under its anchor before it is filled, so a node that aliases itself
loads without recursing forever and comes back as a genuinely cyclic Python object.

```python
SRC = """\
self_map: &sm
  name: node
  next: *sm
"""

doc = yaml.load(SRC)

doc['self_map']['next'] is doc['self_map']   # True
yaml.dump(doc) == SRC                        # True
repr(doc['self_map'])                        # "{'name': 'node', 'next': {...}}"
```

`ruamel.yaml` 0.19.1 loads the same document without raising and gives
`doc['self_map']['next']` as `None`; dumping it writes `'self_map:\n  name: node\n  next:\n'`.
The cycle is gone and nothing said so.

Take the usual care with cyclic data on the Python side. `repr` handles it, `copy.deepcopy`
handles it, `json.dumps` and a naive recursive walk of your own do not.

## Anchors on a tree you built

Set the anchor and dump. There is no separate registration step and no `always_dump` to
remember:

```python
from yamluna import CommentedMap

base = CommentedMap(retries=3, timeout=30)
base.yaml_set_anchor('defaults')

doc = CommentedMap()
doc['defaults'] = base
doc['web'] = CommentedMap(timeout=5)
doc['web'].add_yaml_merge([base])

print(yaml.dump(doc))
```

```yaml
defaults: &defaults
  retries: 3
  timeout: 30
web:
  <<: *defaults
  timeout: 5
```

`yaml_set_anchor(name)` and `node.anchor.value = name` do the same thing. The anchor goes on
the first occurrence of the object in the walk and every later occurrence becomes `*name`, so
the order the object appears in the tree decides where the definition is written. Put the
anchored node where you want the definition to be.

An object that appears twice with no anchor of its own gets a generated name rather than being
written out twice, which keeps the two sides the same object when the file is reloaded:

```python
shared = CommentedMap(retries=3)
doc = CommentedMap()
doc['defaults'] = shared
doc['web'] = shared
print(yaml.dump(doc))
```

```yaml
defaults: &id001
  retries: 3
web: *id001
```

Anchors only work on nodes that can carry one. Containers always can. A scalar can once it is
one of the scalar classes: `name: &n demo` loads as a `PlainScalarString` with
`.anchor.value == 'n'`, and a `PlainScalarString` you build yourself takes `yaml_set_anchor`
just as well. A bare `str` or `int` has nowhere to keep an anchor, which is why the loader
promotes an anchored scalar to a class in the first place. See
[Scalar styles and types](scalars.md).

## What the edges do

Each of these was measured against this version of the library.

| Situation | Result |
| --- | --- |
| `*name` for an anchor the document never defines | `ComposerError: found undefined alias 'name'` |
| `*name` for an anchor defined in an earlier document of the same stream | the same error: anchors do not cross a document boundary |
| the same anchor name defined twice | both definitions are kept, later aliases bind to the nearer one, and the document round-trips. No `ReusedAnchorWarning`; the class exists so a `filterwarnings` entry written against ruamel keeps importing |
| two `<<` keys in one mapping | `DuplicateKeyError`, even under `allow_duplicate_keys=True`, which is what the merge-key spec asks for |
| an alias used as a key of the mapping its own anchor is defined in | `DuplicateKeyError`. One node reached twice is one `dict` key, and YAML requires a mapping's keys to be unique. See [Errors](errors.md) |

## Where to look next

* [Comments and blank lines](comments.md) for the trivia model, which anchors ride along with.
* [Behaviour differences](../migrating/differences.md) §B1 for the ruamel anchor defect with
  its repro.
* [Containers](../api/containers.md) for the generated reference on `Anchor`, `MergeList` and
  `add_yaml_merge`.
