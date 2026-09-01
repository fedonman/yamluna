"""Corpus discovery for the yamluna acceptance suite.

A test that runs over the corpus takes one of the `corpus_*` fixtures and is
parametrised over `tests/corpus/*.yaml` automatically, with the file's stem as the
test id, so `-k comment-eol` selects one file.

The yamluna side of the harness is a single fixture, `yamluna_roundtrip`: text in,
load, dump, text out. It skips when the Rust extension has not been built (build it
with `maturin develop --uv`), so the pure Python tests still run without it.
`tests/test_roundtrip.py` is the acceptance run over that one fixture: what a load
followed by a dump writes has to be the source, byte for byte.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

CORPUS_DIR = Path(__file__).parent / "corpus"


def corpus_files() -> list[Path]:
    """Lists the corpus files.

    Returns:
        Every `tests/corpus/*.yaml` path, sorted, so test ids are stable across
        machines.
    """
    return sorted(CORPUS_DIR.glob("*.yaml"))


def _read(path: Path) -> str:
    """Corpus text, decoded but otherwise untouched: a BOM and CRLF survive."""
    return path.read_bytes().decode("utf-8")


@pytest.fixture(params=corpus_files(), ids=lambda p: p.stem)
def corpus_path(request: pytest.FixtureRequest) -> Path:
    """One corpus file, parametrised over all of them.

    Returns:
        The path of the corpus file for this parametrisation.
    """
    return request.param


@pytest.fixture
def corpus_bytes(corpus_path: Path) -> bytes:
    """The corpus file's exact bytes, which is what byte-identity is measured against.

    Returns:
        The file read in binary mode, with no decoding step in between.
    """
    return corpus_path.read_bytes()


@pytest.fixture
def corpus_text(corpus_path: Path) -> str:
    """The corpus file decoded as UTF-8, with the BOM and CRLF left in place.

    Returns:
        The decoded source, unchanged apart from the decoding.
    """
    return _read(corpus_path)


@pytest.fixture(scope="session")
def yamluna() -> Any:
    """The yamluna package, which imports without the Rust extension.

    Returns:
        The imported `yamluna` module. The test is skipped when `python/yamluna` is
        not importable.
    """
    return pytest.importorskip("yamluna", reason="python/yamluna is not importable")


@pytest.fixture(scope="session")
def yamluna_roundtrip(yamluna: Any) -> Callable[[str], str]:
    """The acceptance operation: text in, load, dump, text out.

    The single seam the whole corpus goes through: parse in Rust, construct the Python
    tree, represent it back to records, emit in Rust.

    Returns:
        A function taking the source text and returning what a load followed by a dump
        writes. The test is skipped when the extension has not been built.
    """
    import io

    if not hasattr(yamluna, "YAML"):
        pytest.skip("yamluna.YAML does not exist yet")
    pytest.importorskip(
        "yamluna._yamluna", reason="extension not built yet: maturin develop"
    )

    def roundtrip(text: str) -> str:
        yaml = yamluna.YAML()
        yaml.preserve_quotes = True
        buf = io.StringIO()
        yaml.dump_all(list(yaml.load_all(text)), buf)
        return buf.getvalue()

    return roundtrip


@pytest.fixture(scope="session")
def ruamel_roundtrip() -> Callable[[str], str]:
    """The ruamel.yaml oracle, for tests that compare the two implementations directly.

    Returns:
        `differential.roundtrip_with_ruamel`, which loads and dumps through one ruamel
        instance configured the way this harness measures it.
    """
    from differential import roundtrip_with_ruamel

    return roundtrip_with_ruamel
