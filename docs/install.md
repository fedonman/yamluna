# Install

```bash
pip install yamluna
```

There is no runtime dependency and no Rust toolchain to install: the wheel carries the
compiled extension.

!!! note "The first release is not published yet"

    `yamluna` 0.1.0 is still marked unreleased in
    [CHANGELOG.md](https://github.com/fedonman/yamluna/blob/main/CHANGELOG.md) and
    the repository carries no release tag, so that `pip install` has nothing to fetch today.
    Until it does, use [from source](#from-source) below.

## Python versions

CPython 3.11 and newer. The extension is built against the [stable ABI][abi3] with PyO3's
`abi3-py311` feature, so it compiles once and the same binary is loaded by every later
CPython: one wheel per platform, tagged `cp311-abi3`, rather than one per interpreter
version.

CI runs the test suite on 3.11, 3.12 and 3.13, then installs the wheel it built into a
fresh 3.13 environment and exercises it, which is what makes the abi3 tag a checked claim
instead of a build flag. That smoke test is
[`ci/smoke.py`](https://github.com/fedonman/yamluna/blob/main/ci/smoke.py); it
loads a commented document, edits it, dumps it, and asserts the comments went with the
entries they described.

  [abi3]: https://docs.python.org/3/c-api/stable.html

## Platforms

The wheel job in
[`.github/workflows/ci.yml`](https://github.com/fedonman/yamluna/blob/main/.github/workflows/ci.yml)
builds and installs a Linux wheel on every push. No source file in `crates/` is conditional
on the target platform, so a source build works wherever a Rust toolchain and CPython 3.11
do; macOS and Windows wheels are not built by CI yet.

## From source

You need Rust 1.85 or newer, because the crates are `edition = "2024"`, and CPython 3.11 or
newer.

```bash
git clone https://github.com/fedonman/yamluna
cd yamluna

uv venv                            # or: python -m venv .venv
uv pip install -e . --group dev    # maturin builds the extension

.venv/bin/python -c "import yamluna; print(yamluna.__version__)"
```

```text
0.1.0
```

`maturin` is the build backend, so `pip install -e .` compiles the extension for you. After
a change to the Rust, rebuild it in place:

```bash
.venv/bin/maturin develop --uv            # debug build
.venv/bin/maturin develop --uv --release  # optimised, and what the benchmarks need
```

Drop `--uv` if you are not using `uv`.

### The justfile

The repository root has a [`justfile`][just] wrapping the same commands:

| recipe | what it runs |
|---|---|
| `just build` | `maturin develop --uv`, the in-place debug build |
| `just release` | the same with `--release` |
| `just unit` | `pytest tests`, which needs no extension |
| `just rust` | `cargo test --workspace` |
| `just lint` | `cargo clippy`, `cargo fmt --check`, `ruff check python tests` |
| `just check` | build, then `cargo test --workspace`, then `pytest` (the default recipe) |
| `just diff` | `tests/differential.py`, the corpus round trip against `ruamel.yaml` |

  [just]: https://github.com/casey/just

## Importing does not need the extension

Only `YAML.load` and `YAML.dump` reach into Rust. The object model, the scalar types, the
error hierarchy and the tag registry are pure Python, so `import yamluna` works against a
checkout with nothing compiled, and so does building a document by hand:

```python
import yamluna


class Circuit:
    def __init__(self, qubits: int) -> None:
        self.qubits = qubits


yaml = yamluna.YAML()
yaml.register_class(Circuit)
document = yamluna.CommentedMap({'main': Circuit(2)})
document['ports'] = yamluna.CommentedSeq([80, 443])
```

Call `load` or `dump` without it and you get an `ImportError` that names the build command
rather than a missing-module traceback. This is `YAML().load('a: 1\n')` against a checkout
with no `_yamluna` in it:

```text
ImportError: the yamluna Rust extension (yamluna._yamluna) is not built. Build it with `maturin develop` from the repository root (or `pip install -e .`). Everything that does not touch the parser or the emitter works without it.
```

That is why `just unit` is a separate recipe from `just check`: the justfile calls it
"python tests, no extension required", and the corpus round-trip fixture skips itself when
there is nothing compiled to round-trip through.
