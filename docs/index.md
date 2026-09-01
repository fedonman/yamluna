# yamluna

yamluna is a round-trip YAML library for Python. You load a document, change the parts you
care about, write it back, and everything you did not touch comes back exactly as the author
wrote it: the comments, the blank lines, the quoting, the anchors, the directives, the
indentation, the alignment of the trailing comments.

The whole pipeline is Rust. The scanner is a fork of
[`saphyr-parser`](https://github.com/saphyr-rs/saphyr) 0.0.12, extended to report the three
things a round trip needs and upstream throws away: comments, whether a collection was
written in block or flow style, and the names of anchors. On top of it sit a document model
that records what the source wrote rather than what it meant, and an emitter that writes
those recordings back. Python gets a thin layer over that, and the parse and the emit run
with the GIL released, so loads across threads genuinely overlap. It is
[YAML 1.2](comparison.md#yaml-11-or-12), with 1.1 scalar resolution available to a document
that asks for it with `%YAML 1.1`.

The public API is not new. It is `ruamel.yaml`'s `typ='rt'`, deliberately and closely: the
same `YAML` object, the same `CommentedMap` and `CommentedSeq`, the same `.ca` and `.lc`, the
same scalar types, the same exception hierarchy. The goal is that porting is an import
change, and that what you get afterwards is the behaviour that mode was always supposed to
have. [Migrating](migrating/index.md) lists what ports untouched, what is deliberately
different, and what is missing.

```python
from pathlib import Path

from yamluna import YAML

yaml = YAML()
config = yaml.load(Path('config.yaml'))

config['replicas'] = 5
config['ports'].append(8080)
del config['legacy_mode']

yaml.dump(config, Path('config.yaml'))
```

Here is `config.yaml` before:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 3
legacy_mode: true     # remove me before 2.0

ports:
  - 80                # http
  - 443               # https
```

and after. The blank line is still there, the sequence is still indented under its key, the
end-of-line comments are still aligned where they were, and the one comment that described
`legacy_mode` left with it:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 5

ports:
  - 80                # http
  - 443               # https
  - 8080
```

The same script through `ruamel.yaml` 0.19.1, changing only the import, gets the data right
and loses the blank line, re-indents the sequence to column 0, and drags both end-of-line
comments along with it:

```yaml
# service configuration
name: demo            # shown in the UI
replicas: 5
ports:
- 80                  # http
- 443                 # https
- 8080
```

## Install

```bash
pip install yamluna
```

Wheels are built for CPython 3.11 and newer on Linux, macOS and Windows. There is no Rust
toolchain to install and no runtime dependency. See [Install](install.md) for building from
source.

## Where to go next

<div class="grid cards" markdown>

-   **[Why yamluna exists](why.md)**

    The problem it was built for, and what a comment being attached to a node instead of an
    index actually buys you.

-   **[How it compares](comparison.md)**

    Next to `ruamel.yaml`, PyYAML, `strictyaml` and the Rust YAML crates, with the
    benchmark numbers.

-   **[Guide](guide/index.md)**

    Loading, dumping, comments, scalar styles, anchors, custom classes, settings, errors.

-   **[Migrating from ruamel.yaml](migrating/index.md)**

    What ports unchanged, what is deliberately different, and what is missing.

-   **[API reference](api/index.md)**

    Every public class and function, generated from the docstrings.

-   **[Internals](internals/index.md)**

    How the Rust core, the FFI boundary and the Python layer fit together.

</div>

## What it is not

`yamluna` implements `typ='rt'` and nothing else. There is no safe, base or unsafe mode, no
`!!python/object:`, no component substitution, no plug-ins, no `scan()` / `compose()` /
`serialize()` pipeline, and no module-level `load()` / `dump()`.

Those are omissions on purpose rather than gaps. Round-trip is the mode with the interesting
problem and the broken implementation; the others are `json.load` with more spelling. If you
need one of them, `PyYAML` and `ruamel.yaml` are both good at it, and
[Migrating](migrating/index.md) lists the replacement for each.

## Licence

MIT or Apache-2.0, at your option. `crates/yamluna-scanner` is a fork of
[saphyr-parser](https://github.com/saphyr-rs/saphyr) 0.0.12 under the same terms.
