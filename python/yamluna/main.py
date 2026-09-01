"""The `YAML` entry point.

`YAML(typ='rt')` is the whole public API. `typ` accepts `'rt'` and nothing else; any
other value raises `ValueError`. The safe, base and unsafe modes, `!!python/object:`,
component substitution, plug-ins and the low-level `scan`/`compose`/`serialize` pipeline
are deliberate omissions.

The two halves of the pipeline are one line each:

```text
load:  yamluna._yamluna.parse(text)  ->  list[Doc]  ->  constructor.construct
dump:  representer.represent         ->  list[Doc]  ->  yamluna._yamluna.emit
```

The Rust extension is imported lazily, so importing `yamluna`, registering classes,
building `CommentedMap` objects by hand and every scalar type work with no extension
built. `YAML.load` and `YAML.dump` raise `ImportError` naming the build command when it
is missing.

A document with no root, a file that is only comments or a bare `---`, loads as `None`.
Its directives, its markers and its own comments are held on the `YAML` instance that
loaded it, and `dump_all` hands each set back to the `None` at the same position in the
stream, so such a document is re-emitted as it was read. The association is positional:
loading again replaces the table, and dumping a different number of documents than were
loaded can hand a record to the wrong empty document.

Each instance owns its tag registry, so two `YAML()` objects in one process can register
different classes under the same tag name without disturbing each other.
`default_registry` and the module-level `register_class` cover the case of one registry
for a whole application, and an instance shares it only when you construct it with
`YAML(registry=default_registry)`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Final, Self, cast

from ._record import Doc, EmitOptions
from .error import ComposerError, YAMLStreamError
from .registry import TagRegistry

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import ModuleType

__all__ = ['YAML', 'default_registry', 'register_class']

# A `str` is the text and never a path: that is ruamel's rule, and too much code in the
# wild passes a document as a string for this to be worth diverging on.
ReadStream = str | bytes | bytearray | os.PathLike[str] | IO[str] | IO[bytes]
"""Anything `YAML.load` accepts.

A `str` is the document text, not a path; pass a `Path` for a file. Bytes are decoded
from their byte-order mark, or as UTF-8 when there is none.
"""

WriteStream = os.PathLike[str] | IO[str] | IO[bytes] | None
"""Anything `YAML.dump` accepts. `None` asks for the text to be returned instead."""

# Longest mark first. `\xff\xfe\x00\x00` (UTF-32-LE) starts with the UTF-16-LE mark, so
# testing the shorter one first would match it and decode the rest as UTF-16.
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
    '!!python/object:, component substitution, plug-ins and the low-level '
    'scan/compose/serialize pipeline are deliberate omissions, not gaps -- see '
    '"What it is not" in the README.'
)


def _extension() -> ModuleType:
    """Return the Rust extension module, or raise an `ImportError` saying how to build it."""
    try:
        # Deliberately lazy: importing yamluna, registering classes and building the object
        # model all work with no extension built, and only load and dump need it. ty cannot
        # resolve the name because maturin compiles the module and it ships no stub.
        from . import _yamluna  # noqa: PLC0415  # ty: ignore[unresolved-import]
    except ImportError as exc:  # pragma: no cover: covered by the "not built" test
        raise ImportError(_NO_EXTENSION) from exc
    return _yamluna


def _decode(data: bytes) -> str:
    r"""Decode source bytes, keeping a byte-order mark as a leading `\ufeff`.

    Falls back to UTF-8 when the bytes carry no mark, which is what YAML requires.
    """
    # The mark stays in the text because it is source that the round trip has to
    # reproduce (tests/corpus/text-bom.yaml). The loader strips it again before scanning.
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return '\ufeff' + data[len(bom) :].decode(codec)
    return data.decode('utf-8')


def _read(stream: ReadStream) -> str:
    """Read the source text out of `stream`.

    Args:
        stream: The YAML source. See `ReadStream` for what that may be.

    Returns:
        The source text.

    Raises:
        YAMLStreamError: `stream` is not a `str`, `bytes`, a path, or an object with a
            `.read()` method.
    """
    if isinstance(stream, str):
        return stream
    if isinstance(stream, bytes | bytearray):
        return _decode(bytes(stream))
    if isinstance(stream, os.PathLike):
        return _decode(Path(stream).read_bytes())
    read = getattr(stream, 'read', None)
    if read is None:
        msg = (
            f'cannot read YAML from {type(stream).__name__}: expected str, bytes, a '
            'path, or an object with a .read() method'
        )
        raise YAMLStreamError(msg)
    data = read()
    return data if isinstance(data, str) else _decode(bytes(data))


def _write(text: str, stream: WriteStream, encoding: str) -> str | None:
    """Write `text` to `stream`, or return it when `stream` is `None`."""
    if stream is None:
        return text
    if isinstance(stream, os.PathLike):
        Path(stream).write_bytes(text.encode(encoding))
        return None
    if not hasattr(stream, 'write'):
        msg = (
            f'cannot write YAML to {type(stream).__name__}: expected a path, an object '
            'with a .write() method, or None to get the text back'
        )
        raise YAMLStreamError(msg)
    try:
        # A binary stream is detected by trying the text write first: `BytesIO.write(str)`
        # raises `TypeError` before writing anything, so the fallback cannot double-write.
        # Which of the two a stream is is a runtime fact, so each branch casts to the one
        # it is about to try.
        cast('IO[str]', stream).write(text)
    except TypeError:
        cast('IO[bytes]', stream).write(text.encode(encoding))
    return None


class YAML:
    """Round-trip YAML reader and writer.

    One instance carries the emitter settings, the tag registry and the records of the
    stream it loaded last. `dump` with no stream returns the text.

    Example:
        ```python
        yaml = YAML()  # typ='rt' is the only mode
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        data = yaml.load(Path('config.yaml'))
        data['answer'] = 42
        yaml.dump(data, Path('config.yaml'))
        ```

        The context-manager form collects every document dumped inside the block and
        writes them as one multi-document stream when the block ends:

        ```python
        with YAML(output=Path('out.yaml')) as yaml:
            yaml.dump(first)
            yaml.dump(second)
        ```

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
        """Create a reader and writer with ruamel's round-trip defaults.

        Args:
            typ: The mode. Only `'rt'`, or a one-element sequence holding it, is accepted.
            output: Where the context-manager form writes. Nothing else reads it, so a
                plain `dump` still needs its own stream or returns the text.
            registry: The tag registry this instance uses. A fresh empty `TagRegistry` by
                default, so two instances never share registrations unless you hand the
                same registry to both.

        Raises:
            ValueError: `typ` is anything other than `'rt'`.

        """
        requested = list(typ) if isinstance(typ, list | tuple) else [typ]
        if requested != ['rt']:
            raise ValueError(_BAD_TYP.format(typ=typ))

        self.typ: list[str] = ['rt']

        # One registry per instance. ruamel's `register_class` is a classmethod mutating
        # process-global tables, so two of its `YAML()` objects poison each other.
        self.registry: TagRegistry = TagRegistry() if registry is None else registry
        """This instance's tag registry. Shared only when you pass one to `YAML()`."""

        # -- settings -----------------------------------------------------------------

        self.preserve_quotes: bool | None = None
        """Whether a scalar you changed keeps the quoting style it was loaded with.

        `None` and `False` let the emitter re-decide the quoting of a changed scalar. A
        scalar you did not touch reproduces its source lexeme either way.
        """

        self.default_flow_style: bool = False
        """Whether collections you build are written in flow style (`{a: 1}`).

        `False` gives each collection the style its `.fa` asks for, and block style when
        it asks for nothing. A collection that came from a document keeps its own style.
        """

        self.width: int | None = None
        """Column that a scalar the emitter lays out is folded at. `None` means 80.

        A node that still remembers where the source wrote it is never re-wrapped, so this
        applies to values you created.
        """

        self.explicit_start: bool | None = None
        """Whether every document gets a `---` marker. `None` keeps what each document had."""

        self.explicit_end: bool | None = None
        """Whether every document gets a `...` marker. `None` keeps what each document had."""

        self.allow_duplicate_keys: bool = False
        """Whether a mapping may repeat a key.

        `False` raises `DuplicateKeyError`. `True` warns `DuplicateKeyFutureWarning` and
        the last value wins. A repeated `<<` merge key is an error under both.
        """

        self.line_break: str | None = None
        """The line break to write: `'\\n'`, `'\\r\\n'` or `'\\r'`.

        `None` takes the break from the documents, using `'\\r\\n'` when some lexeme spans
        lines with it and `'\\n'` otherwise. A break between two lines is not something the
        model records, so a CRLF file with no multi-line scalar in it comes back with
        `'\\n'`; set `'\\r\\n'` for those. Any other value raises `ValueError` on dump.
        """

        self.encoding: str = 'utf-8'
        """The encoding used whenever the text is written as bytes.

        That is a path destination and a stream that rejects `str`. A text stream and the
        text returned by `dump(data)` are unaffected.
        """

        self.map_indent: int | None = None
        """Columns a nested mapping is indented by. `None` means 2."""

        self.sequence_indent: int | None = None
        """Columns a sequence's items are indented by, from the key holding them.

        `None` means 2. Like the other indent settings it lays out nodes you built; a node
        that came from a document and was not restyled reproduces its own indentation.
        """

        self.sequence_dash_offset: int | None = None
        """Columns the `-` itself is indented by, inside `sequence_indent`. `None` means 0."""

        self._version: tuple[int, int] | None = None

        self._output: WriteStream = output
        self._cm_docs: list[Any] | None = None

        # Records of the documents in the last-loaded stream that constructed to `None`,
        # keyed by position. `None` is a singleton and cannot carry the per-document
        # record every other root carries, so without this table the `---`, the `%YAML`
        # and `%TAG` directives and the comments of a document with no content would have
        # nowhere to live, and the file would round-trip to `null`. A sentinel object was
        # rejected because `load` returning `None` is ruamel's contract and what every
        # `if data is None:` in the wild tests; an empty `CommentedMap` carrier was
        # rejected because it is falsely a mapping. The price is that the association is
        # positional and every load replaces the table, which is the same safe way to be
        # wrong as a stale `.lc` and is exact for the load-edit-dump cycle this library
        # exists for.
        self._empty: dict[int, Doc] = {}

    def __repr__(self) -> str:
        """Return `repr(self)`, which names the mode and nothing else."""
        return f'YAML(typ={self.typ!r})'

    # -- settings -------------------------------------------------------------------

    @property
    def version(self) -> tuple[int, int] | None:
        """The `%YAML` version to write, as `(major, minor)`.

        Assignment also accepts `'1.2'`. Setting it forces the directive and a `---` onto
        every document dumped, as ruamel does, and it selects the resolution rules used
        when a value you created is spelled: under `(1, 1)` the strings `yes` and `on` are
        booleans, so a string spelled that way is quoted. Leaving it `None` re-emits
        whatever directive the source had and spells new values by YAML 1.2.

        Returns:
            The forced version, or `None` when no version is forced.

        Raises:
            ValueError: The assigned value is neither a pair of integers nor a
                `'major.minor'` string.

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
            msg = f'version must be (major, minor) or "major.minor", got {value!r}'
            raise ValueError(msg) from None
        self._version = (major, minor)

    def indent(
        self,
        mapping: int | None = None,
        sequence: int | None = None,
        offset: int | None = None,
    ) -> None:
        """Set the emitter's indentation, with ruamel's signature.

        These lay out nodes you created. A node that came from a document and was not
        restyled reproduces its own layout, so this cannot re-indent a file you loaded.

        Args:
            mapping: Columns a nested mapping is indented by. `None` leaves `map_indent`
                as it was.
            sequence: Columns a sequence's items are indented by, measured from the key
                that holds them. `None` leaves `sequence_indent` as it was.
            offset: Columns the `-` itself is indented by, inside `sequence`. `None`
                leaves `sequence_dash_offset` as it was.

        """
        if mapping is not None:
            self.map_indent = mapping
        if sequence is not None:
            self.sequence_indent = sequence
        if offset is not None:
            self.sequence_dash_offset = offset

    def _emit_options(self) -> EmitOptions:
        """Return the settings as the FFI record, with every `None` resolved to its default."""
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

    # -- the tag registry -------------------------------------------------------------

    def register_class(
        self, cls: type, *, tag: str | None = None, source: str | None = None
    ) -> type:
        """Register `cls` with this instance's registry.

        Instances of `cls` then dump with a tag, and that tag loads back as an instance.
        No other `YAML` sees the registration unless it was given the same registry.

        Args:
            cls: The class to register.
            tag: The tag name to write, without the leading `!`. Defaults to
                `cls.yaml_tag`, then to `cls.__name__`.
            source: The namespace the tag is written in. Defaults to `cls.yaml_source`,
                then to the root package of `cls.__module__`. Passing it also pins the
                class: when two registrations collide on the same source and tag name the
                unpinned ones are rewritten to their full module path, and a pinned class
                keeps the source you asked for.

        Returns:
            `cls`, so this also works as a decorator.

        Example:
            ```python
            @yaml.register_class
            class Circuit: ...
            ```

        """
        return self.registry.register_class(cls, tag=tag, source=source)

    register = register_class
    """Shorter name for `register_class`, for use as `@yaml.register`."""

    # -- loading ----------------------------------------------------------------------

    # The root of a loaded document is an arbitrary Python object, so `Any` is its type
    # rather than a placeholder for one nobody wrote down. Same for `dump`'s `data`.
    def load(self, stream: ReadStream) -> Any:  # noqa: ANN401
        """Load the one document in `stream`.

        Args:
            stream: The YAML source. A `str` is the document text, not a path; pass a
                `Path` for a file.

        Returns:
            The document root: a `CommentedMap`, a `CommentedSeq`, a scalar, or `None`
            for a stream with no document and for a document with no content.

        Raises:
            ImportError: The Rust extension is not built.
            YAMLStreamError: `stream` is not a string, bytes, a path, or an object with a
                `read()` method.
            ScannerError: The source is not well-formed YAML. An alias naming an anchor
                that no node defines is reported here too.
            ComposerError: The stream holds more than one document. Use `load_all`.
            ConstructorError: A node cannot be built: a tag that claims a namespace this
                registry knows but matches no class in it, a tag that matches two
                registered classes, a `!!binary`, `!!bool`, `!!int`, `!!float` or
                `!!timestamp` whose text does not parse, or a registered class whose state
                is not a mapping and that has no `from_yaml`.
            DuplicateKeyError: A mapping repeats a key while `allow_duplicate_keys` is
                false, or repeats the `<<` merge key at all.

        Example:
            ```python
            yaml = YAML()
            config = yaml.load(Path('config.yaml'))
            ```

        """
        documents = self.load_all(stream)
        if len(documents) > 1:
            context = 'expected a single document in the stream'
            raise ComposerError(context, None, 'but found another document', None)
        return documents[0] if documents else None

    def load_all(self, stream: ReadStream) -> list[Any]:
        """Load every document in `stream`, in order.

        Args:
            stream: The YAML source. A `str` is the document text, not a path; pass a
                `Path` for a file.

        Returns:
            One root object per document, as a list. Empty for a stream with no document.

        Raises:
            ImportError: The Rust extension is not built.
            YAMLStreamError: `stream` is not a string, bytes, a path, or an object with a
                `read()` method.
            ScannerError: The source is not well-formed YAML. An alias naming an anchor
                that no node defines is reported here too.
            ConstructorError: A node cannot be built, for the reasons `load` lists.
            DuplicateKeyError: A mapping repeats a key while `allow_duplicate_keys` is
                false, or repeats the `<<` merge key at all.

        """
        # A list rather than ruamel's generator: the parser reads the whole stream in one
        # FFI call anyway, so a generator would only delay the errors.
        parse = _extension().parse
        text = _read(stream)
        docs: list[Doc] = parse(text, allow_duplicate_keys=self.allow_duplicate_keys)
        # Lazy so that `import yamluna` costs nothing until a document is actually loaded.
        from . import constructor  # noqa: PLC0415

        built = [constructor.construct(doc, self) for doc in docs]
        # A document that constructed to `None` has nowhere to keep its record; keep it.
        pairs = enumerate(zip(docs, built, strict=True))
        self._empty = {i: doc for i, (doc, obj) in pairs if obj is None}
        return built

    # -- dumping ----------------------------------------------------------------------

    def dump(self, data: Any, stream: WriteStream = None) -> str | None:  # noqa: ANN401
        r"""Write `data` to `stream`, or return the text when `stream` is `None`.

        Inside the context-manager form the document is collected instead of written, and
        the whole block goes out as one stream when the block ends.

        Args:
            data: The document root to write.
            stream: A path, an object with a `write()` method, or `None` for the text.
                Inside the context-manager form it must be `None`.

        Returns:
            The emitted text when `stream` is `None`, otherwise `None`. Always `None`
            inside the context-manager form.

        Raises:
            ImportError: The Rust extension is not built.
            YAMLStreamError: `stream` is neither a path, an object with a `write()`
                method, nor `None`; or a stream was passed inside the context-manager
                form, which has only the one destination.
            RepresenterError: An object has no built-in representation and no registered
                class, or a `to_yaml` hook returned something other than a node index.
            EmitterError: The model cannot be written as YAML.
            ValueError: `line_break` is not `'\n'`, `'\r\n'` or `'\r'`.

        """
        if self._cm_docs is not None:
            if stream is not None:
                msg = (
                    'pass the stream to YAML(output=...) instead: inside the '
                    'context-manager form every dump goes to that one stream'
                )
                raise YAMLStreamError(msg)
            self._cm_docs.append(data)
            return None
        return self.dump_all([data], stream)

    def dump_all(self, documents: Iterable[Any], stream: WriteStream = None) -> str | None:
        r"""Write `documents` as one multi-document stream, or return the text.

        A `None` document is written back as the empty document at that position in the
        stream this instance loaded last, directives, markers and comments included.

        Args:
            documents: The document roots, in the order they are written.
            stream: A path, an object with a `write()` method, or `None` for the text.

        Returns:
            The emitted text when `stream` is `None`, otherwise `None`.

        Raises:
            ImportError: The Rust extension is not built.
            YAMLStreamError: `stream` is neither a path, an object with a `write()`
                method, nor `None`.
            RepresenterError: An object has no built-in representation and no registered
                class, or a `to_yaml` hook returned something other than a node index.
            EmitterError: The model cannot be written as YAML.
            ValueError: `line_break` is not `'\n'`, `'\r\n'` or `'\r'`.

        """
        emit = _extension().emit  # before representer, so "not built" is the first error
        # Lazy so that `import yamluna` costs nothing until a document is actually dumped.
        from . import representer  # noqa: PLC0415

        # A document that loaded as `None` has no content the user could have edited, so
        # its record is the whole document and is handed back untouched.
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

    def __enter__(self) -> Self:
        """Start collecting documents for a single stream.

        Returns:
            This instance, so `with YAML(output=...) as yaml:` binds it.

        Raises:
            YAMLStreamError: The instance was constructed without `output`, so the block
                has nowhere to write.

        """
        if self._output is None:
            msg = (
                'the context-manager form needs somewhere to write: YAML(output=path '
                'or stream). Without it, use yaml.dump(data) and keep the returned text.'
            )
            raise YAMLStreamError(msg)
        self._cm_docs = []
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Write everything the block dumped, then stop collecting.

        A block that ends by raising writes nothing, and the exception propagates.
        """
        collected, self._cm_docs = self._cm_docs, None
        if exc_type is None and collected:
            self.dump_all(collected, self._output)


default_registry: Final = TagRegistry()
"""The registry the module-level `register_class` writes to.

A plain `YAML()` does not consult it. Construct the instance as
`YAML(registry=default_registry)` to opt in.
"""


def register_class(cls: type, *, tag: str | None = None, source: str | None = None) -> type:
    """Register `cls` with `default_registry`.

    Only a `YAML` constructed with `registry=default_registry` sees the registration.

    Args:
        cls: The class to register.
        tag: The tag name to write, without the leading `!`. Defaults to `cls.yaml_tag`,
            then to `cls.__name__`.
        source: The namespace the tag is written in. Defaults to `cls.yaml_source`, then
            to the root package of `cls.__module__`. Passing it also pins the class, so a
            collision never rewrites its source to the full module path.

    Returns:
        `cls`, so this also works as a decorator.

    """
    return default_registry.register_class(cls, tag=tag, source=source)
