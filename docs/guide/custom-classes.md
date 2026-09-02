# Custom classes and tags

Register a class and its instances go out with a tag and come back as themselves:

```python
from yamluna import YAML
from libx.circuits import Circuit          # libx/circuits.py

yaml = YAML()
yaml.register_class(Circuit)

text = yaml.dump({'main': Circuit(qubits=2, shots=1024)})
```

```yaml
%TAG ! tag:libx/
---
main: !Circuit
  qubits: 2
  shots: 1024
```

`yaml.load(text)['main']` is a `libx.circuits.Circuit` again with `qubits == 2`, and dumping
it a second time reproduces those five lines byte for byte.

`register_class` returns the class, so it also works as a decorator, spelled `@yaml.register`
for short.

## The registry key is the class path

The registry is keyed on `f'{cls.__module__}.{cls.__qualname__}'`, so registering a class
replaces its own entry and nothing else. Two libraries that both define a `Circuit` stay
registered side by side.

`ruamel.yaml` keys on `'!' + cls.__name__` instead. Registering both `Circuit` classes with
`ruamel.yaml` 0.19.1 and loading the document back:

```pycon
>>> type(back['a']), type(back['b'])
(<class 'liby.core.Circuit'>, <class 'liby.core.Circuit'>)
>>> back['a'].__dict__, back['b'].__dict__
({'qubits': 2}, {'n': 3})
```

The first entry comes back as the wrong class, holding the attributes it was written with,
and nothing warns you. Which class wins depends on registration order. The repro is
[C1](../migrating/differences.md#c1-register_class-keys-the-constructor-registry-on-the-class-name).

## What gets written

| | default | override |
|---|---|---|
| tag name | `cls.__name__` | `cls.yaml_tag`, or `tag=` on `register_class` |
| source | the root package of `cls.__module__` | `cls.yaml_source`, or `source=`, which also pins it |
| body | `obj.__getstate__()` if the class defines one, otherwise `obj.__dict__` | a `to_yaml` classmethod, or `to_yaml=` on `register_class` |

A leading `!` on `yaml_tag` is stripped, since ruamel spells the attribute
`yaml_tag = '!Circuit'`.

Reading back is the mirror: `cls.__new__(cls)`, then `__setstate__(state)` if the class
defines one, otherwise `obj.__dict__.update(state)`. `__init__` is not called, and a
`from_yaml` classmethod or a `from_yaml=` on `register_class` replaces the whole of it.

The `to_yaml` and `from_yaml` hooks keep ruamel's signatures, so a class that already has them
ports unchanged. They are also how a class writes itself as something other than a mapping:

```python
# libx/timing.py
class Duration:
    def __init__(self, seconds):
        self.seconds = seconds

    @classmethod
    def to_yaml(cls, representer, obj):
        return representer.represent_scalar('!Duration', f'{obj.seconds}s')

    @classmethod
    def from_yaml(cls, constructor, node):
        return cls(int(node.value.removesuffix('s')))
```

```yaml
%TAG ! tag:libx/
---
timeout: !Duration 30s
```

Without `from_yaml`, a node that is not a mapping has no state to copy onto the object.
Loading `main: !Circuit [1, 2]` against a plain registered `Circuit` says so:

```text
cannot construct libx.circuits.Circuit from a CommentedSeq: give it a from_yaml classmethod, or pass from_yaml= to register_class
  in "<unicode string>", line 3, column 16
```

## A class you cannot edit

The hooks above have to live on the class, which rules out anything from a C extension:
`numpy.ndarray`, `decimal.Decimal`, a protobuf message. Those types reject attribute
assignment outright, with `TypeError: cannot set 'to_yaml' attribute of immutable type`.

Pass the two functions to `register_class` instead. A plain function gets no `cls`, so it
takes what the classmethod takes after it: `(representer, obj)` and `(constructor, node)`.

```python
from decimal import Decimal


def write_decimal(representer, obj):
    return representer.represent_scalar('!Decimal', str(obj))


def read_decimal(constructor, node):
    return Decimal(node.value)


yaml = YAML()
yaml.register_class(Decimal, to_yaml=write_decimal, from_yaml=read_decimal)
```

```yaml
%TAG ! tag:decimal/
---
price: !Decimal 19.99
```

A function passed here wins over a classmethod of the same name on the class, so it also
overrides a hook you did not write. Registering the class again replaces the whole record, so
a later `register_class(Decimal, source='decimal')` to settle a tag collision has to pass the
two functions again or the class goes back to being written as its attributes.

A registered class writes itself through its hook whatever it subclasses, so the extension
types built on `tuple`, `time.struct_time` and `os.stat_result` among them, go through
`to_yaml` rather than being written as their fields. A registered class with no hook keeps the
form its base type implies: a `dict` subclass is written as a mapping, tagged.

## Anchors, when the class writes itself

An object that appears more than once is anchored at its first occurrence and aliased at
every later one, and the walk settles that before it calls the hook. Whatever node the hook
returns is given the name the walk chose:

```python
duration = Duration(30)
print(yaml.dump({'connect': duration, 'read': duration}))
```

```yaml
%TAG ! tag:libx/
---
connect: &id001 !Duration 30s
read: *id001
```

A hook that names the node itself wins, and the aliases follow it. `represent_scalar` takes
`anchor=` for that:

```python
def write_decimal(representer, obj):
    return representer.represent_scalar('!Decimal', str(obj), anchor='price')
```

```yaml
%TAG ! tag:decimal/
---
list: &price !Decimal 19.99
sale: *price
```

The same holds when the hook hands back a node it did not build. `representer.represent_data(x)`
returns the node written for `x`, which may already carry a name of its own or be an alias to
one, and a node carries a single anchor. The object the hook was called for is aliased to
whichever name that node ends up with, so what is written stays loadable.

## The namespace on the wire

The source is written into the document with YAML's own mechanism, a `%TAG` directive. It
costs one line per source, touches no user data, and any conformant YAML parser round-trips
it. A document that uses no registered class gets no directive it did not already have.

**One source.** It takes the primary `!` handle, so the tags are bare:

```yaml
%TAG ! tag:libx/
---
main: !Circuit
  qubits: 2
```

**Two sources.** The most-used keeps `!`; the rest get named handles:

```yaml
%TAG ! tag:libx/
%TAG !liby! tag:liby/
---
a: !Circuit
  qubits: 2
b: !liby!Circuit
  n: 3
```

**Two modules of one package.** Both classes are called `Circuit` and both sources default to
the root package `libx`, so both are promoted to their full module paths and the same rule
applies:

```yaml
%TAG ! tag:libx.circuits/
%TAG !libx-gates! tag:libx.gates/
---
a: !Circuit
  qubits: 2
b: !libx-gates!Circuit
  width: 1
```

Three rules decide that layout:

* **Which source gets `!`.** The one used most, counted once per node in the document. A
  document with one `libx` node and two `liby` nodes gives `!` to `liby`.
* **Ties.** Broken on the source name, so registering `liby` before `libx` produces the same
  file. Output never depends on registration order.
* **Named handles.** The source folded to `[A-Za-z0-9-]`, so `libx.gates` becomes
  `!libx-gates!`. If two sources fold to the same string the second gets a digit appended,
  `!a-b2!`.

## Promotion, exactly

Two registrations that declare the same source and the same tag name, neither of them pinned,
both have their source replaced by their own `cls.__module__`. Nothing else moves.

The rule is recomputed over the whole registry on every registration, so it is a function of
what is registered rather than of the order it arrived in. Re-registering one of the pair
under a different source undoes the promotion it caused.

A source given as `source=` or as `yaml_source` is pinned and is never promoted. Only the
other side of the collision moves:

```python
from libx.circuits import Circuit as LibxCircuit
from libx.gates import Circuit as LibxGate

yaml.register_class(LibxCircuit, source='libx')   # pinned, stays tag:libx/
yaml.register_class(LibxGate)                     # moves to tag:libx.gates/
```

```yaml
%TAG ! tag:libx/
%TAG !libx-gates! tag:libx.gates/
---
a: !Circuit
  qubits: 2
b: !libx-gates!Circuit
  width: 1
```

!!! warning "Pinning both sides is your problem"

    Pin two classes of the same name to the same source and nothing promotes them apart.
    Both write `!Circuit`, the document is genuinely ambiguous, and loading it raises
    `ambiguous tag '!Circuit': 2 registered candidates: libx.circuits.Circuit (=
    tag:libx/Circuit), libx.gates.Circuit (= tag:libx/Circuit)`. Pin at most one of a
    colliding pair, or give one of them a different `tag=`.

## Reading back

A tag that resolves through a `%TAG` directive in scope is looked up by `(source, name)`. A
bare `!Circuit` with no directive, which is what a hand-written file has, is looked up by name
alone across every source: one candidate constructs it, so hand-written files stay pleasant to
write.

More than one candidate raises instead of guessing, and the message names every candidate and
both ways out:

```text
ambiguous tag '!Circuit': 2 registered candidates: libx.circuits.Circuit (= tag:libx/Circuit), liby.core.Circuit (= tag:liby/Circuit); yamluna will not guess. Add a %TAG directive naming the source (e.g. '%TAG ! tag:libx/') or re-register with an explicit source= to disambiguate.
  in "<unicode string>", line 2, column 3
```

A name that is missing from a source the registry does have classes in is a typo rather than
somebody else's tag, so it is reported too:

```text
unresolved tag '!Ghost' (= 'tag:libx/Ghost'): no class is registered as 'Ghost' in source 'libx'
  in "<unicode string>", line 4, column 3
```

A tag in a namespace the registry has never heard of belongs to somebody else's document. It
round-trips untouched, tag, directive, comments and all, through a `YAML()` that has nothing
registered:

```python
src = '%TAG !k! tag:kubernetes.io/\n---\nspec: !k!Pod\n  replicas: 3   # kept\n'
yaml = YAML()
assert yaml.dump(yaml.load(src)) == src
```

## One registry per instance

Each `YAML()` owns its registry, so registering a class in your loader cannot reach another
library's loader, and it cannot be reached by one:

```python
one, two = YAML(), YAML()
one.register_class(Circuit)

one.registry.registration_for(Circuit)    # Registration(...)
two.registry.registration_for(Circuit)    # None
```

ruamel's `register_class` is a classmethod writing to a process-global table
([C2](../migrating/differences.md#c2-register_class-is-process-global-not-per-yaml)). If you
want that, it is one shared registry away and you opt in per instance:

```python
import yamluna
from yamluna import YAML, default_registry

yamluna.register_class(Circuit)           # writes to default_registry

YAML().registry.resolve('!Circuit')                    # None
YAML(registry=default_registry).registry.resolve('!Circuit').cls is Circuit    # True
```

`TagRegistry` is public and constructible, so a set of instances can share one registry
without going through the module-level default. See [the API reference](../api/registry.md).

## What a registered class costs you

A registered node loads as your object, and your object has nowhere to keep the layout the
source wrote. Comments and blank lines *inside* the tagged node are lost on the next dump.
Everything outside it survives as usual:

```yaml
%TAG ! tag:libx/
---
# the experiment
main: !Circuit
  qubits: 2      # two for now
  shots: 1024

name: demo   # kept
```

dumps back as

```yaml
%TAG ! tag:libx/
---
# the experiment
main: !Circuit
  qubits: 2
  shots: 1024

name: demo   # kept
```

If the comments inside a node matter more than getting your class back, leave that node
unregistered: an unknown tag round-trips exactly, and you get a `CommentedMap` carrying both
the tag and the comments.

A loaded instance also carries a `_yaml_node` attribute in its `__dict__`, the record it was
built from. It is filtered out of what gets written, so it never reaches the document, but it
does show up in `obj.__dict__` and in anything that compares instances by it.

Runnable version of this page: [`examples/custom_classes.py`](examples.md#custom-classes).
