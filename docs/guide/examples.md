# Examples

Three scripts in [`examples/`](https://github.com/qilimanjaro-tech/yamluna/tree/master/examples).
Each one runs standalone against an installed `yamluna` and imports nothing else, asserts the
claims it makes, and carries its real output in a comment at the bottom:

```bash
pip install yamluna
python examples/round_trip.py
```

CI runs all three on every push, so an example that stops working fails the build.

| | |
|---|---|
| [`round_trip.py`](#round-trip) | load, edit, dump; comments, blank lines and quoting kept |
| [`comments.py`](#comments) | reading and writing `.ca`, and what a `del` does to its neighbours |
| [`custom_classes.py`](#custom-classes) | `register_class` and the `%TAG` output that keeps two `Circuit`s apart |

## Round trip

[`examples/round_trip.py`](https://github.com/qilimanjaro-tech/yamluna/blob/master/examples/round_trip.py)
loads a config, checks it reproduces byte for byte, then makes five edits:

```python
yaml = YAML()
yaml.preserve_quotes = True

config = yaml.load(SRC)
assert yaml.dump(config) == SRC, 'round trip must be byte-identical'

# The containers are a dict and a list, so ordinary Python works on them.
assert isinstance(config, dict) and isinstance(config['ports'], list)
assert config['database']['motd'] == 'welcome\nto demo\n'

config['replicas'] = 5
config['database']['port'] = 6543
config['ports'].append(8080)
config['features'].append('beta')
del config['legacy_mode']

out = yaml.dump(config)
assert '# remove me before 2.0' not in out    # went with the key it described
assert '# shown in the UI' in out             # the neighbour's did not move
```

??? note "`SRC`, the document it starts from"

    ```yaml
    # service configuration
    name: demo            # shown in the UI
    replicas: 3
    legacy_mode: true     # remove me before 2.0

    ports:
      - 80                # http
      - 443               # https

    database:
      host: 'localhost'   # single quotes are kept
      port: 5432
      motd: |
        welcome
        to demo

    # everything below is optional
    features: []
    ```

What it prints:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 5

ports:
  - 80                # http
  - 443               # https
  - 8080

database:
  host: 'localhost'   # single quotes are kept
  port: 6543
  motd: |
    welcome
    to demo

# everything below is optional
features: [beta]
```

The blank lines, the two-space sequence indent, the single quotes on `localhost`, the `|`
block scalar and the flow style of `features` are all still what the author wrote, and no
`indent(...)` call arranged that: every node reproduces the layout it was loaded with. The
quotes stay quoted with or without `preserve_quotes`; what that setting changes is the Python
type you get back, `SingleQuotedScalarString` instead of `str`. See
[Settings](settings.md).

## Comments

[`examples/comments.py`](https://github.com/qilimanjaro-tech/yamluna/blob/master/examples/comments.py)
is the `.ca` tour. It prints the structure ruamel's API gives you for a document with a
header, an own-line comment, two end-of-line comments and a tail note:

```text
root .ca.comment  [None, [CommentToken('# file header\n', col=0), CommentToken('\n', col=0), CommentToken('# about alpha\n', col=0)]]
root .ca.items    {'alpha': [None, None, CommentToken('# eol alpha', col=13), None]}
beta .ca.comment  [None, [CommentToken('# inside beta\n', col=2)]]
beta .ca.items    {0: [CommentToken('# eol one', col=13), None, None, None]}
root .ca.end      [CommentToken('\n', col=0), CommentToken('# tail note\n', col=0)]
```

The blank line between the header and `# about alpha` is a `CommentToken` of its own, so
"how many blank lines are here" has an answer. [Comments and blank
lines](comments.md) covers the slot layout.

The second half is the part ruamel gets wrong. Given

```yaml
services:
  # the public one
  web: 8080
  # internal only
  worker: 9000
  # scheduled jobs
  cron: 9100
```

it deletes an entry, then renames and reorders another:

```python
cfg = yaml.load(CFG)
del cfg['services']['worker']    # takes '# internal only' with it, and nothing else
assert '# internal only' not in yaml.dump(cfg)
assert '# scheduled jobs' in yaml.dump(cfg)

cfg = yaml.load(CFG)
cfg['services'].rename('cron', 'scheduler')    # a rename carries the entry's comments
cfg['services'].move_to_end('worker')          # ... and so does a reorder
```

The delete leaves `# scheduled jobs` on `cron` where it belongs:

```yaml
services:
  # the public one
  web: 8080
  # scheduled jobs
  cron: 9100
```

and the rename and the move each take their own comment along:

```yaml
services:
  # the public one
  web: 8080
  # scheduled jobs
  scheduler: 9100
  # internal only
  worker: 9000
```

`ruamel.yaml` 0.19.1 gets the same delete wrong. `# internal only` stays where it was and
ends up labelling `cron`, and `# scheduled jobs` is destroyed:

```yaml
services:
  # the public one
  web: 8080
  # internal only
  cron: 9100
```

That is [A4](../migrating/differences.md#a4-commentedmap__delitem__-never-touches-ca-so-comments-drift-and-resurrect);
the rename and the reorder are
[A5](../migrating/differences.md#a5-key-rename-and-move_to_end-scatter-comments-across-the-document).

## Custom classes

[`examples/custom_classes.py`](https://github.com/qilimanjaro-tech/yamluna/blob/master/examples/custom_classes.py)
registers two classes both called `Circuit`, from two different libraries, and shows that both
survive:

```python
yaml = YAML()
yaml.register_class(LibxCircuit)     # libx.circuits.Circuit
yaml.register_class(LibyCircuit)     # liby.core.Circuit
two_libs = yaml.dump({'a': LibxCircuit(qubits=2), 'b': LibyCircuit(n=3)})

back = yaml.load(two_libs)
assert type(back['a']) is LibxCircuit and back['a'].qubits == 2
assert type(back['b']) is LibyCircuit and back['b'].n == 3
assert yaml.dump(back) == two_libs    # and it round-trips
```

```yaml
%TAG ! tag:libx/
%TAG !liby! tag:liby/
---
a: !Circuit
  qubits: 2
b: !liby!Circuit
  n: 3
```

It then hand-writes a bare `!Circuit`, with both classes registered and no directive to say
which is meant, and gets an error instead of a guess:

```text
ambiguous tag '!Circuit': 2 registered candidates: libx.circuits.Circuit (= tag:libx/Circuit), liby.core.Circuit (= tag:liby/Circuit); yamluna will not guess. Add a %TAG directive naming the source (e.g. '%TAG ! tag:libx/') or re-register with an explicit source= to disambiguate.
  in "<unicode string>", line 2, column 3
```

The script also covers the one-library shape, the two-modules-of-one-package shape, and the
fact that the registry belongs to the instance. [Custom classes and
tags](custom-classes.md) is the same ground written out.
