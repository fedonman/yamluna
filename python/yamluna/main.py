"""The ``YAML`` entry point -- DESIGN.md §4.2.

``YAML(typ='rt')`` is the whole public API.  ``typ`` accepts ``'rt'`` and nothing else;
the safe/base/unsafe modes, ``!!python/object:``, component substitution, plug-ins and the
low-level ``scan``/``compose``/``serialize`` pipeline are deliberate omissions (README,
"What it is not").

The two halves of the pipeline are one line each::

    load  :  yamluna._yamluna.parse(text)  ->  list[Doc]  ->  constructor.construct
    dump  :  representer.represent        ->  list[Doc]  ->  yamluna._yamluna.emit

Both extension calls go through :func:`_extension`, which is imported *lazily*: importing
``yamluna``, registering classes, building ``CommentedMap``\\ s by hand and every scalar
type work with no Rust extension built.  Calling :meth:`YAML.load` or :meth:`YAML.dump`
without it raises an :class:`ImportError` naming the build command.

**Empty documents.**  ``load`` returns the root object, and a document with no root -- a
file that is only comments, or a bare ``---`` -- has ``None`` for a root.  ``None`` is a
singleton: it cannot carry the :data:`~yamluna.constructor.DOC_ATTRIB` record every other
root carries, so ``---``, ``%YAML``, ``%TAG`` and the document's own comments would have
nowhere to live and the file would round-trip to ``null``.

They live here instead, in :attr:`YAML._empty`: the records of the documents that loaded
as ``None``, keyed by their position in **the stream this instance loaded last**.
:meth:`dump_all` hands each record back to the ``None`` at the same position, and since a
document that loaded as ``None`` has no content the user could have edited, the record
*is* the document -- it is re-emitted as it was read.

The alternatives were a sentinel object (``load`` would stop returning ``None``, which is
ruamel's contract and what every ``if data is None:`` in the wild tests) and an empty
``CommentedMap`` carrier (same problem, plus it is falsely a mapping).  The cost of this
one is that the association is positional: a load followed by a dump of a *different*
number of documents can hand a record to the wrong empty document, and every load
replaces the table.  That is the same "safe way to be wrong" as a stale ``.lc``, and it is
exact for the load-edit-dump cycle the library exists for.

**Each instance owns its registry** (DIVERGENCES C2).  ruamel's ``register_class`` is a
classmethod that mutates process-global tables, so two ``YAML()`` objects in one process
poison each other; here two instances can register different classes under the same tag
name and neither notices the other.  :data:`default_registry` and the module-level
:func:`register_class` exist for the "one registry for my whole app" case, and are shared
only by instances explicitly constructed with ``YAML(registry=default_registry)``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import IO, Any, Final

from ._record import Doc, EmitOptions
from .error import ComposerError, YAMLStreamError
from .registry import TagRegistry

__all__ = ['YAML', 'default_registry', 'register_class']

#: Anything :meth:`YAML.load` accepts.  A ``str`` is the document *text*, not a path --
#: that is ruamel's rule and too much code depends on it; use a ``Path`` for a file.
ReadStream = str | bytes | bytearray | os.PathLike[str] | IO[str] | IO[bytes]

#: Anything :meth:`YAML.dump` accepts.  ``None`` means "return the text".
WriteStream = os.PathLike[str] | IO[str] | IO[bytes] | None

#: Byte-order marks, longest first: ``\xff\xfe\x00\x00`` (UTF-32-LE) starts with the
#: UTF-16-LE mark, so the order is load-bearing.
_BOMS: Final = (
    (b'\x00\x00\xfe\xff', 'utf-32-be'),
    (b'\xff\xfe\x00\x00', 'utf-32-le'),
    (b'\xef\xbb\xbf', 'utf-8'),
    (b'\xfe\xff', 'utf-16-be'),
    (b'\xff\xfe', 'utf-16-le'),
)

_NO_EXTENSION: Final = (
    'the yamluna Rust extension (yamluna._yamluna) is not built. Build it with '
    '`maturin develop` from the repository root (or `pip install -e .`). Everything '
    'that does not touch the parser or the emitter works without it.'
)

_BAD_TYP: Final = (
    "yamluna supports typ='rt' only; got {typ!r}. The safe/base/unsafe modes, "
    "!!python/object:, component substitution, plug-ins and the low-level "
    "scan/compose/serialize pipeline are deliberate omissions, not gaps -- see "
    '"What it is not" in the README.'
)


def _extension() -> Any:
    """The Rust extension, or an :class:`ImportError` that says how to build it."""
    try:
        from . import _yamluna
    except ImportError as exc:  # pragma: no cover -- covered by the "not built" test
        raise ImportError(_NO_EXTENSION) from exc
    return _yamluna


def _decode(data: bytes) -> str:
    """Decode source bytes, keeping a byte-order mark as a leading ``\\ufeff``.

    The BOM survives into the text because it is source the round trip has to reproduce
    (``tests/corpus/text-bom.yaml``); the loader is what strips it before scanning
    (DESIGN.md §2.3).  Without a BOM the encoding is UTF-8, as YAML requires.
    """
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return '\ufeff' + data[len(bom) :].decode(codec)
    return data.decode('utf-8')


def _read(stream: ReadStream) -> str:
    """Source text from `stream`.  See :data:`ReadStream` for what that may be."""
    if isinstance(stream, str):
        return stream
    if isinstance(stream, bytes | bytearray):
        return _decode(bytes(stream))
    if isinstance(stream, os.PathLike):
        return _decode(Path(stream).read_bytes())
    read = getattr(stream, 'read', None)
    if read is None:
        raise YAMLStreamError(
            f'cannot read YAML from {type(stream).__name__}: expected str, bytes, a '
            'path, or an object with a .read() method'
        )
    data = read()
    return data if isinstance(data, str) else _decode(bytes(data))


def _write(text: str, stream: WriteStream, encoding: str) -> str | None:
    """Write `text` to `stream`, or return it when `stream` is ``None``.

    A binary stream is detected by trying the text write first: ``BytesIO.write(str)``
    raises ``TypeError`` before writing anything, so the fallback cannot double-write.
    """
    if stream is None:
        return text
    if isinstance(stream, os.PathLike):
        Path(stream).write_bytes(text.encode(encoding))
        return None
    if not hasattr(stream, 'write'):
        raise YAMLStreamError(
            f'cannot write YAML to {type(stream).__name__}: expected a path, an object '
            'with a .write() method, or None to get the text back'
        )
    try:
        stream.write(text)  # type: ignore[arg-type]
    except TypeError:
        stream.write(text.encode(encoding))  # type: ignore[arg-type]
    return None


class YAML:
    """Round-trip YAML reader and writer.

    ::

        yaml = YAML()                       # typ='rt' is the only mode
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        data = yaml.load(Path('config.yaml'))
        data['answer'] = 42
        yaml.dump(data, Path('config.yaml'))

    ``dump`` with no stream returns the text.  The context-manager form collects every
    document dumped inside the block and writes them as one stream::

        with YAML(output=Path('out.yaml')) as yaml:
            yaml.dump(first)
            yaml.dump(second)
    """

    __slots__ = (
        '_cm_docs',
        '_empty',
        '_output',
        '_version',
        'allow_duplicate_keys',
        'default_flow_style',
        'encoding',
        'explicit_end',
        'explicit_start',
        'line_break',
        'map_indent',
        'preserve_quotes',
        'registry',
        'sequence_dash_offset',
        'sequence_indent',
        'typ',
        'width',
    )

    def __init__(
        self,
        *,
        typ: str | Sequence[str] = 'rt',
        output: WriteStream = None,
        registry: TagRegistry | None = None,
    ) -> None:
        requested = list(typ) if isinstance(typ, list | tuple) else [typ]
        if requested != ['rt']:
            raise ValueError(_BAD_TYP.format(typ=typ))

        self.typ: list[str] = ['rt']
        # This instance's tag registry.  Never shared unless you pass one in (C2).
        self.registry: TagRegistry = TagRegistry() if registry is None else registry

        # -- settings.  `None` means "the default", so a fresh instance decides nothing.
        self.preserve_quotes: bool | None = None
        self.default_flow_style: bool = False
        self.width: int | None = None
        self.explicit_start: bool | None = None  # None = keep what the source had (B2)
        self.explicit_end: bool | None = None
        self.allow_duplicate_keys: bool = False
        self.line_break: str | None = None
        self.encoding: str = 'utf-8'  # only used when writing to a binary stream
        self.map_indent: int | None = None
        self.sequence_indent: int | None = None
        self.sequence_dash_offset: int | None = None
        self._version: tuple[int, int] | None = None

        self._output: WriteStream = output
        self._cm_docs: list[Any] | None = None
        #: The document records of the last-loaded stream's empty documents, by position.
        #: See "Empty documents" in the module docstring for why they live here.
        self._empty: dict[int, Doc] = {}

    def __repr__(self) -> str:
        return f'YAML(typ={self.typ!r})'

    # -- settings -------------------------------------------------------------------

    @property
    def version(self) -> tuple[int, int] | None:
        """The ``%YAML`` version to write, as ``(major, minor)``.

        Accepts ``(1, 2)`` or ``'1.2'``.  Setting it forces the directive *and* ``---``
        on every document dumped, as ruamel does; leaving it ``None`` re-emits whatever
        directive the source had.
        """
        return self._version

    @version.setter
    def version(self, value: tuple[int, int] | Sequence[int] | str | None) -> None:
        if value is None:
            self._version = None
            return
        try:
            parts = value.split('.') if isinstance(value, str) else list(value)
            major, minor = (int(p) for p in parts)
        except (TypeError, ValueError):
            raise ValueError(
                f'version must be (major, minor) or "major.minor", got {value!r}'
            ) from None
        self._version = (major, minor)

    def indent(
        self,
        mapping: int | None = None,
        sequence: int | None = None,
        offset: int | None = None,
    ) -> None:
        """Set the emitter's indentation, ruamel's signature.

        Sets :attr:`map_indent`, :attr:`sequence_indent` and
        :attr:`sequence_dash_offset`; an argument left ``None`` is left alone.  These
        apply to nodes you *created*: a node that came from a document and was not
        restyled reproduces its own layout (DIVERGENCES B5).
        """
        if mapping is not None:
            self.map_indent = mapping
        if sequence is not None:
            self.sequence_indent = sequence
        if offset is not None:
            self.sequence_dash_offset = offset

    def _emit_options(self) -> EmitOptions:
        """The settings as the FFI record.  ``None`` collapses to the documented default."""
        return EmitOptions(
            map_indent=2 if self.map_indent is None else self.map_indent,
            seq_indent=2 if self.sequence_indent is None else self.sequence_indent,
            seq_offset=0 if self.sequence_dash_offset is None else self.sequence_dash_offset,
            width=80 if self.width is None else self.width,
            line_break=self.line_break or '\n',
            explicit_start=bool(self.explicit_start),
            explicit_end=bool(self.explicit_end),
            default_flow_style=bool(self.default_flow_style),
            canonical=False,
            preserve_quotes=bool(self.preserve_quotes),
        )

    # -- the tag registry (DESIGN.md §5.5) --------------------------------------------

    def register_class(
        self, cls: type, *, tag: str | None = None, source: str | None = None
    ) -> type:
        """Register `cls` with *this instance's* registry.  Returns `cls`.

        ``tag`` overrides the tag name (default ``cls.yaml_tag`` or ``cls.__name__``),
        ``source`` pins the namespace and opts the class out of promotion (§5.2).
        """
        return self.registry.register_class(cls, tag=tag, source=source)

    #: Decorator form: ``@yaml.register`` above a class definition.
    register = register_class

    # -- loading ----------------------------------------------------------------------

    def load(self, stream: ReadStream) -> Any:
        """The single document in `stream`.  ``None`` for an empty stream."""
        documents = self.load_all(stream)
        if len(documents) > 1:
            raise ComposerError(
                'expected a single document in the stream',
                None,
                'but found another document',
                None,
            )
        return documents[0] if documents else None

    def load_all(self, stream: ReadStream) -> list[Any]:
        """Every document in `stream`, in order.

        A list rather than ruamel's generator: the parser reads the whole stream in one
        FFI call anyway, so a generator would only delay the errors.
        """
        parse = _extension().parse
        text = _read(stream)
        docs: list[Doc] = parse(text, allow_duplicate_keys=self.allow_duplicate_keys)
        from . import constructor

        built = [constructor.construct(doc, self) for doc in docs]
        # A document that constructed to `None` has nowhere to keep its record; keep it.
        pairs = enumerate(zip(docs, built, strict=True))
        self._empty = {i: doc for i, (doc, obj) in pairs if obj is None}
        return built

    # -- dumping ----------------------------------------------------------------------

    def dump(self, data: Any, stream: WriteStream = None) -> str | None:
        """Write `data` to `stream`, or return the text when `stream` is ``None``.

        Inside the context-manager form the document is collected instead, and the whole
        block is written as one stream on exit.
        """
        if self._cm_docs is not None:
            if stream is not None:
                raise YAMLStreamError(
                    'pass the stream to YAML(output=...) instead: inside the '
                    'context-manager form every dump goes to that one stream'
                )
            self._cm_docs.append(data)
            return None
        return self.dump_all([data], stream)

    def dump_all(self, documents: Iterable[Any], stream: WriteStream = None) -> str | None:
        """Write `documents` as one multi-document stream, or return the text."""
        emit = _extension().emit  # before representer, so "not built" is the first error
        from . import representer

        docs: list[Doc] = [
            representer.represent(d, self, carried=self._empty.get(i) if d is None else None)
            for i, d in enumerate(documents)
        ]
        for doc in docs:
            if self.explicit_start is not None:
                doc.explicit_start = self.explicit_start
            if self.explicit_end is not None:
                doc.explicit_end = self.explicit_end
            if self._version is not None:
                doc.version = self._version
                doc.explicit_start = True  # a %YAML directive requires the --- after it
        text: str = emit(docs, self._emit_options())
        return _write(text, stream, self.encoding)

    # -- the context-manager dump form -------------------------------------------------

    def __enter__(self) -> YAML:
        if self._output is None:
            raise YAMLStreamError(
                'the context-manager form needs somewhere to write: YAML(output=path '
                'or stream). Without it, use yaml.dump(data) and keep the returned text.'
            )
        self._cm_docs = []
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        collected, self._cm_docs = self._cm_docs, None
        if exc_type is None and collected:
            self.dump_all(collected, self._output)


#: The convenience registry behind the module-level :func:`register_class`.  A ``YAML()``
#: does **not** consult it -- pass ``YAML(registry=default_registry)`` to opt in.  The
#: per-instance registry is the real one (DIVERGENCES C2).
default_registry: Final = TagRegistry()


def register_class(cls: type, *, tag: str | None = None, source: str | None = None) -> type:
    """Register `cls` with :data:`default_registry`.  Returns `cls`, so it decorates."""
    return default_registry.register_class(cls, tag=tag, source=source)
