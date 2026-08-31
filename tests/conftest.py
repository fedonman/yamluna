"""Corpus discovery for the yamluna acceptance suite.

Every test that is "run this over the corpus" takes one of the ``corpus_*``
fixtures and is parametrised over ``tests/corpus/*.yaml`` automatically, with
the file's stem as the test id, so ``-k comment-eol`` selects one file.

The yamluna side of the harness is deliberately a *single* fixture,
``yamluna_roundtrip``: ``text -> load -> dump -> text``.  It skips when the
Rust extension has not been built (``maturin develop --uv``), so the pure
Python tests still run without it; ``test_roundtrip.py`` is the DESIGN 6.2
acceptance run over that one fixture.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

CORPUS_DIR = Path(__file__).parent / "corpus"


def corpus_files() -> list[Path]:
    """Every corpus file, sorted, so test ids are stable across machines."""
    return sorted(CORPUS_DIR.glob("*.yaml"))


def _read(path: Path) -> str:
    """Corpus text, decoded but otherwise untouched: BOM and CRLF survive."""
    return path.read_bytes().decode("utf-8")


@pytest.fixture(params=corpus_files(), ids=lambda p: p.stem)
def corpus_path(request: pytest.FixtureRequest) -> Path:
    """One corpus file, parametrised over all of them."""
    return request.param


@pytest.fixture
def corpus_bytes(corpus_path: Path) -> bytes:
    """The corpus file's exact bytes -- what byte-identity is measured against."""
    return corpus_path.read_bytes()


@pytest.fixture
def corpus_text(corpus_path: Path) -> str:
    """The corpus file decoded as UTF-8, with the BOM and CRLF left in place."""
    return _read(corpus_path)


@pytest.fixture(scope="session")
def yamluna() -> Any:
    """The yamluna package (pure Python; importable without the extension)."""
    return pytest.importorskip("yamluna", reason="python/yamluna is not importable")


@pytest.fixture(scope="session")
def yamluna_roundtrip(yamluna: Any) -> Callable[[str], str]:
    """``text -> load -> dump -> text``, the DESIGN 6.2 acceptance operation.

    The single seam the whole corpus goes through: parse in Rust, construct,
    represent, emit in Rust.  It skips when the extension has not been built.
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
    """The oracle, for tests that compare the two implementations directly."""
    from differential import roundtrip_with_ruamel

    return roundtrip_with_ruamel
