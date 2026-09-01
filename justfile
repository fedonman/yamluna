py := ".venv/bin/python"

default: check

# build the extension into the venv, in place
build:
    .venv/bin/maturin develop --uv

release:
    .venv/bin/maturin develop --uv --release

rust:
    cargo test --workspace

lint:
    cargo clippy --workspace --all-targets -- -D warnings
    cargo fmt --check
    .venv/bin/ruff check python tests

# python tests, no extension required
unit:
    PYTHONPATH=python .venv/bin/pytest tests -q

# full stack: extension + every test
check: build
    cargo test --workspace
    .venv/bin/pytest tests -q

# how far are we from ruamel?
diff:
    .venv/bin/python tests/differential.py

# build the documentation site into site/
docs:
    .venv/bin/zensical build

# serve the documentation site with live reload
docs-serve:
    .venv/bin/zensical serve
