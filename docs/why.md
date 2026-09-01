# Why yamluna

A configuration file has two readers. A program reads the values. A person reads the file,
and what that person reads is mostly not values: it is the comment explaining why the
timeout is 45 rather than 30, the blank line separating the network block from the logging
block, the sequence indented under its key because every other file in the repository is
indented that way.

Both readings are the file. A program that rewrites it and keeps only the values has
discarded the half a human needs, and the next person to open the diff cannot tell the
intended change from the collateral damage.

## What a schema-first loader gives back

PyYAML loads YAML into plain `dict`s, `list`s and strings. That is the right model for a
document a machine wrote. For a file a person wrote it is lossy in one direction: by the
time you hold the object, everything that was not a value is already gone.

The file from the [home page](index.md), through PyYAML 6.0.3, with the same three edits:

```python
import yaml

config = yaml.safe_load(SRC)
config['replicas'] = 5
config['ports'].append(8080)
del config['legacy_mode']

print(yaml.safe_dump(config), end='')
```

```yaml
name: demo
ports:
- 80
- 443
- 8080
replicas: 5
```

Every comment gone, the blank line gone, the sequence flattened to column 0, and the keys
alphabetised, because `sort_keys=True` is the default. Passing `sort_keys=False` restores
the order and nothing else.

That is not a PyYAML defect. `safe_load` never offered to preserve any of it, and the same
is true of every schema-first loader in any language: the object model has no slot for a
comment, so the comment cannot survive the load, never mind the dump. If a machine is the
only reader of the file, this costs you nothing. If a person edits it too, the diff of a
one-line change is the whole file.

## ruamel.yaml, and comments keyed by position

`ruamel.yaml`'s `typ='rt'` exists for exactly this problem and gets a great deal of it
right. It keeps comments, it keeps quoting, it gives you a `dict` subclass you can mutate
normally. The [home page](index.md) shows it carrying two end-of-line comments through an
edit correctly.

What it does not do is attach a comment to the thing the comment is about. Comments live in
`.ca.items`, a table keyed by mapping key or by list index, and an own-line comment is
stored inside the *previous* sibling's comment token. The association is a position, and
ordinary mutation changes positions.

Delete a key:

```python
SRC = """image: app:1.4
# staging only, drop before release
debug: true
replicas: 3
"""

config = yaml.load(SRC)
del config['debug']
yaml.dump(config, sys.stdout)
```

=== "ruamel.yaml 0.19.1"

    ```yaml
    image: app:1.4
    # staging only, drop before release
    replicas: 3
    ```

=== "yamluna"

    ```yaml
    image: app:1.4
    replicas: 3
    ```

The key left. The comment stayed, and it now says that the replica count is staging-only.
A file that lies to its reader is worse than one that says nothing, and nothing in the
program that made the edit had any way to notice.

In this document ruamel physically stores that comment in `image`'s slot, as
`ca.items['image'][2]`, so deleting `debug` was never going to reach it. The fix is not a
more careful `__delitem__`. It is that in yamluna the comment is not in another node's
slot at all: trivia is attached to the node it describes, keyed by node identity. An
operation that removes a node removes what was written about it, and an operation that
moves a node moves it, whether or not anyone remembered to write that operation down.
`reverse()` tells the same story with nothing deleted. From this source:

```yaml
steps:
  - build            # compile the wheel
  - test             # run pytest
  - publish          # upload to the index
```

=== "ruamel.yaml 0.19.1"

    ```yaml
    steps:
    - publish            # compile the wheel
    - test               # run pytest
    - build              # upload to the index
    ```

=== "yamluna"

    ```yaml
    steps:
      - publish          # upload to the index
      - test             # run pytest
      - build            # compile the wheel
    ```

ruamel moved the values and left the comments at their old indices, so each of the three
steps now carries the wrong one. `insert()`, `del`, key rename and `move_to_end()` break
the same way for the same reason; entries A1 to A6 of
[Behaviour differences](migrating/differences.md) have them, each with its repro.

!!! note "One position is not fixed yet"

    An own-line comment above a collection's *first* child is filed on the collection
    rather than on that child, so it stays put while the child moves. Inserting immediately
    before an item that carries an own-line comment also emits a stranded `-` on its own
    line. Twelve xfails in `tests/test_mutation.py` pin both, so neither can close
    unnoticed. Every byte still round-trips; what is wrong is the ownership, at one
    position out of n. Entry A2 of
    [Behaviour differences](migrating/differences.md) carries the measurement.

## The measured bar

`tests/corpus/` is 41 hand-written files, one YAML concern each. The test is the strict
one: load the file, change nothing, dump it, compare bytes. Both libraries get the same two
lines of configuration, `YAML()` and `preserve_quotes = True`, and everything else stays at
its default. Run it yourself with `.venv/bin/python tests/differential.py`, which prints a
row per file; today it reports:

| | round-trips byte-identically |
|---|---|
| `ruamel.yaml` 0.19.1 | 3 of 40 |
| yamluna 0.1.0 | 40 of 40 |

The forty-first file, `key-duplicate.yaml`, writes `a: 1` and later `a: 3`. A mapping keeps
one of two equal keys, so no dict-backed API can write those bytes back; it is scored on
behaviour instead and both libraries get a row of their own.
[How it compares](comparison.md) has the rest of the field and the benchmark numbers.

## The second reason: two libraries, one class name

`ruamel.yaml`'s `register_class` computes the tag as `'!' + cls.__name__` and stores the
constructor in a process-global table on `RoundTripConstructor`. Two libraries that both
define a `Circuit` register the same tag. The second one wins, silently, and the winner is
decided by import order. Measured, with `libx.circuits.Circuit` registered first and
`liby.core.Circuit` second:

```yaml
x: !Circuit
  qubits: 2
y: !Circuit
  n: 3
```

```text
x: liby.core.Circuit  {'qubits': 2}
y: liby.core.Circuit  {'n': 3}
```

`x` came back as `liby`'s class holding `libx`'s attributes. No warning, no error, and the
first attribute access that expects `.n` raises `AttributeError` somewhere far from the
cause. Building your own private `YAML()` does not help: `add_constructor` is a
classmethod, so the registration is global to the process either way.

yamluna keys the registry on `f"{cls.__module__}.{cls.__qualname__}"`, so a registration
cannot overwrite another, and it writes the namespace into the document with YAML's own
`%TAG` mechanism rather than hoping the reader guesses. The same two classes:

```yaml
%TAG ! tag:libx/
%TAG !liby! tag:liby/
---
x: !Circuit
  qubits: 2
y: !liby!Circuit
  n: 3
```

which loads back as `libx.circuits.Circuit` and `liby.core.Circuit`, and dumps again
byte-identically. The registry is per `YAML()` instance, so a library that registers its
own classes cannot poison anybody else's, and a bare `!Circuit` with more than one
candidate raises an error listing the candidates instead of picking one.

[Custom classes and tags](guide/custom-classes.md) has the handle-assignment rules, the
collision promotion, and what happens to a tag from a namespace the registry has never
heard of.
