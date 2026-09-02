"""Tests for the public API: the `yamluna.YAML` class and the names the package exports.

Everything reachable without the Rust extension is tested unconditionally. The cases that
need the real load and dump pipeline take the `pipeline` fixture, which skips until
`yamluna._yamluna`, `yamluna.constructor` and `yamluna.representer` all exist.
"""

from __future__ import annotations

import importlib.util
import io
from typing import TYPE_CHECKING, Any

import pytest

import yamluna
from yamluna import YAML, ComposerError, YAMLStreamError, default_registry
from yamluna.main import _decode, _read, _write

if TYPE_CHECKING:
    from pathlib import Path

EXTENSION_BUILT = importlib.util.find_spec('yamluna._yamluna') is not None


@pytest.fixture
def pipeline() -> None:
    """Skip the test unless the whole load and dump path exists."""
    for module in ('yamluna._yamluna', 'yamluna.constructor', 'yamluna.representer'):
        pytest.importorskip(module, reason=f'{module} does not exist yet: maturin develop')


def klass(name: str, module: str) -> type:
    """Return a new class with a chosen `__module__`, so registry paths are predictable.

    Args:
        name: The class name, which is also the default tag name.
        module: The value for `__module__`, which is where the registry reads the source
            from.

    Returns:
        The new class.

    """
    return type(name, (), {'__module__': module})


# -- typ ------------------------------------------------------------------------------


def test_default_is_round_trip() -> None:
    assert YAML().typ == ['rt']


@pytest.mark.parametrize('typ', ['rt', ['rt'], ('rt',)])
def test_rt_is_accepted_however_it_is_spelled(typ: Any) -> None:
    assert YAML(typ=typ).typ == ['rt']


@pytest.mark.parametrize('typ', ['safe', 'base', 'unsafe', ['safe'], ['rt', 'safe'], 42])
def test_every_other_typ_is_rejected(typ: Any) -> None:
    with pytest.raises(ValueError, match='README') as exc:
        YAML(typ=typ)
    assert "typ='rt' only" in str(exc.value)


# -- settings -------------------------------------------------------------------------


def test_a_fresh_instance_decides_nothing() -> None:
    yaml = YAML()
    assert yaml.preserve_quotes is None
    assert yaml.default_flow_style is False
    assert yaml.width is None
    assert yaml.explicit_start is None  # None keeps whatever the source document had
    assert yaml.explicit_end is None
    assert yaml.allow_duplicate_keys is False
    assert yaml.version is None
    assert yaml.line_break is None
    assert yaml.encoding == 'utf-8'
    assert (yaml.map_indent, yaml.sequence_indent, yaml.sequence_dash_offset) == (None,) * 3


def test_indent_sets_the_three_attributes() -> None:
    yaml = YAML()
    yaml.indent(mapping=4, sequence=6, offset=3)
    assert (yaml.map_indent, yaml.sequence_indent, yaml.sequence_dash_offset) == (4, 6, 3)


def test_indent_leaves_out_what_it_is_not_given() -> None:
    yaml = YAML()
    yaml.indent(sequence=4)
    assert yaml.map_indent is None and yaml.sequence_dash_offset is None


def test_defaults_reach_the_emitter_as_the_documented_values() -> None:
    options = YAML()._emit_options()
    assert (options.map_indent, options.seq_indent, options.seq_offset) == (2, 2, 0)
    assert options.width == 80  # ruamel: width=None and width=80 emit identically
    assert options.line_break == '\n'
    assert options.preserve_quotes is False
    assert options.explicit_start is False and options.explicit_end is False


def test_settings_reach_the_emitter() -> None:
    yaml = YAML()
    yaml.indent(mapping=4, sequence=6, offset=3)
    yaml.width = 20
    yaml.line_break = '\r\n'
    yaml.preserve_quotes = True
    yaml.explicit_start = True
    yaml.explicit_end = True
    yaml.default_flow_style = True
    options = yaml._emit_options()
    assert (options.map_indent, options.seq_indent, options.seq_offset) == (4, 6, 3)
    assert options.width == 20
    assert options.line_break == '\r\n'
    assert options.preserve_quotes is True
    assert options.explicit_start is True and options.explicit_end is True
    assert options.default_flow_style is True


@pytest.mark.parametrize(
    ('given', 'expected'),
    [((1, 2), (1, 2)), ('1.2', (1, 2)), ('1.1', (1, 1)), ([1, 2], (1, 2)), (None, None)],
)
def test_version_is_normalised(given: Any, expected: tuple[int, int] | None) -> None:
    yaml = YAML()
    yaml.version = given
    assert yaml.version == expected


@pytest.mark.parametrize('given', ['1', '1.2.3', (1,), (1, 2, 3), 'x.y', 12, ()])
def test_a_version_that_is_not_major_minor_is_rejected(given: Any) -> None:
    with pytest.raises(ValueError, match='major'):
        YAML().version = given


# -- the registry is per instance ------------------------------------------------------


def test_two_instances_register_the_same_tag_name_without_interfering() -> None:
    libx = klass('Circuit', 'libx.circuits')
    liby = klass('Circuit', 'liby.circuits')
    one, two = YAML(), YAML()

    one.register_class(libx)
    two.register_class(liby)

    # ruamel: both land in a class-level dict, the second wins, and which one that is
    # depends on import order.  Here each instance answers with its own class.
    assert (from_one := one.registry.resolve('!Circuit')) is not None
    assert (from_two := two.registry.resolve('!Circuit')) is not None
    assert from_one.cls is libx
    assert from_two.cls is liby
    assert YAML().registry.resolve('!Circuit') is None
    assert default_registry.resolve('!Circuit') is None


def test_registries_are_not_shared_between_instances() -> None:
    thing = klass('Isolated', 'libx')
    one = YAML()
    one.register_class(thing)
    assert one.registry is not YAML().registry
    assert YAML().registry.registrations() == []


def test_register_is_the_decorator_form() -> None:
    yaml = YAML()

    @yaml.register
    class Gate:
        pass

    assert yaml.registry.registration_for(Gate) is not None
    assert yaml.registry.resolve('!Gate').cls is Gate  # ty: ignore[unresolved-attribute]


def test_register_class_forwards_tag_and_source() -> None:
    yaml = YAML()
    cls = klass('Circuit', 'libx.circuits')
    assert yaml.register_class(cls, tag='Circ', source='pinned') is cls
    record = yaml.registry.registration_for(cls)
    assert record is not None
    assert (record.tag_name, record.source, record.pinned) == ('Circ', 'pinned', True)
    assert record.uri == 'tag:pinned/Circ'


def test_register_class_forwards_the_hooks() -> None:
    def write(_representer: Any, _obj: Any) -> int:
        return 0

    def read(_constructor: Any, _node: Any) -> None:
        return None

    yaml = YAML()
    cls = klass('Hooked', 'libx.circuits')
    assert yaml.register_class(cls, to_yaml=write, from_yaml=read) is cls
    record = yaml.registry.registration_for(cls)
    assert record is not None
    assert record.to_yaml is write
    assert record.from_yaml is read


def test_the_module_level_register_class_forwards_the_hooks() -> None:
    def write(_representer: Any, _obj: Any) -> int:
        return 0

    def read(_constructor: Any, _node: Any) -> None:
        return None

    thing = klass('ModuleLevelHooked', 'libz')
    yamluna.register_class(thing, to_yaml=write, from_yaml=read)
    record = default_registry.registration_for(thing)
    assert record is not None
    assert record.to_yaml is write
    assert record.from_yaml is read


def test_the_module_level_registry_is_opt_in() -> None:
    thing = klass('ModuleLevelThing', 'libz')
    assert yamluna.register_class(thing) is thing
    assert (from_default := default_registry.resolve('!ModuleLevelThing')) is not None
    assert from_default.cls is thing
    assert YAML().registry.resolve('!ModuleLevelThing') is None
    shared = YAML(registry=default_registry)
    assert (from_shared := shared.registry.resolve('!ModuleLevelThing')) is not None
    assert from_shared.cls is thing


# -- streams --------------------------------------------------------------------------


def test_a_str_stream_is_the_document_text_not_a_path() -> None:
    assert _read('a: 1\n') == 'a: 1\n'


def test_bytes_are_decoded_as_utf8() -> None:
    assert _read(b'a: caf\xc3\xa9\n') == 'a: café\n'
    assert _read(bytearray(b'a: 1\n')) == 'a: 1\n'


def test_a_path_is_read_as_bytes(tmp_path: Path) -> None:
    path = tmp_path / 'in.yaml'
    path.write_bytes(b'a: 1\n')
    assert _read(path) == 'a: 1\n'


def test_file_objects_are_read_text_or_binary(tmp_path: Path) -> None:
    assert _read(io.StringIO('a: 1\n')) == 'a: 1\n'
    assert _read(io.BytesIO(b'a: 1\n')) == 'a: 1\n'
    path = tmp_path / 'in.yaml'
    path.write_bytes(b'a: 1\n')
    with path.open('rb') as handle:
        assert _read(handle) == 'a: 1\n'


def test_a_stream_that_cannot_be_read_says_so() -> None:
    with pytest.raises(YAMLStreamError, match='read'):
        _read(object())  # ty: ignore[invalid-argument-type]


def test_the_bom_survives_decoding() -> None:
    # The BOM is source text the round trip has to reproduce (tests/corpus/text-bom.yaml).
    # Stripping it before the scanner sees it is the loader's job, not the decoder's.
    assert _decode(b'\xef\xbb\xbfa: 1\n') == '\ufeffa: 1\n'
    assert _decode(b'a: 1\n') == 'a: 1\n'


@pytest.mark.parametrize(
    ('bom', 'codec'),
    [
        (b'\xef\xbb\xbf', 'utf-8'),
        (b'\xfe\xff', 'utf-16-be'),
        (b'\xff\xfe', 'utf-16-le'),
        (b'\x00\x00\xfe\xff', 'utf-32-be'),
        (b'\xff\xfe\x00\x00', 'utf-32-le'),
    ],
)
def test_every_bom_picks_its_codec(bom: bytes, codec: str) -> None:
    # utf-32-le's BOM starts with utf-16-le's, so the detection order is load-bearing.
    text = 'a: caf\u00e9\n'
    assert _decode(bom + text.encode(codec)) == '\ufeff' + text


def test_dump_returns_the_text_when_there_is_no_stream() -> None:
    assert _write('a: 1\n', None, 'utf-8') == 'a: 1\n'


def test_writing_to_a_text_or_binary_stream() -> None:
    text_stream = io.StringIO()
    assert _write('a: café\n', text_stream, 'utf-8') is None
    assert text_stream.getvalue() == 'a: café\n'

    binary_stream = io.BytesIO()
    assert _write('a: café\n', binary_stream, 'utf-8') is None
    assert binary_stream.getvalue() == b'a: caf\xc3\xa9\n'


def test_writing_a_bom_to_a_binary_stream_writes_the_bytes() -> None:
    binary_stream = io.BytesIO()
    _write('\ufeffa: 1\n', binary_stream, 'utf-8')
    assert binary_stream.getvalue() == b'\xef\xbb\xbfa: 1\n'


def test_writing_to_a_path(tmp_path: Path) -> None:
    path = tmp_path / 'out.yaml'
    assert _write('a: café\n', path, 'utf-8') is None
    assert path.read_bytes() == b'a: caf\xc3\xa9\n'


def test_a_stream_that_cannot_be_written_says_so() -> None:
    with pytest.raises(YAMLStreamError, match='write'):
        _write('a: 1\n', 'out.yaml', 'utf-8')  # ty: ignore[invalid-argument-type]


# -- the context-manager dump form -----------------------------------------------------


def test_the_context_manager_needs_an_output() -> None:
    with pytest.raises(YAMLStreamError, match='output'), YAML():
        pass  # pragma: no cover


def test_documents_are_collected_inside_the_block() -> None:
    # Driven by hand rather than with a `with` block, so the assertions land before
    # __exit__ reaches the emitter, which is not built yet.
    yaml = YAML(output=io.StringIO())
    yaml.__enter__()
    assert yaml.dump({'a': 1}) is None
    assert yaml.dump({'b': 2}) is None
    assert yaml._cm_docs == [{'a': 1}, {'b': 2}]  # written as one stream on exit
    yaml.__exit__(RuntimeError, RuntimeError('boom'), None)
    assert yaml._cm_docs is None


def test_a_per_dump_stream_is_refused_inside_the_block() -> None:
    stream = io.StringIO()
    yaml = YAML(output=stream)
    with pytest.raises(YAMLStreamError, match='output'), yaml:
        yaml.dump({'a': 1}, io.StringIO())
    assert stream.getvalue() == ''


def test_nothing_is_written_when_the_block_raises() -> None:
    stream = io.StringIO()
    yaml = YAML(output=stream)
    msg = 'boom'
    # Two statements on purpose: the dump has to happen inside the block that then raises.
    with pytest.raises(RuntimeError), yaml:  # noqa: PT012
        yaml.dump({'a': 1})
        raise RuntimeError(msg)
    assert stream.getvalue() == ''


# -- the extension is not there yet ----------------------------------------------------


@pytest.mark.skipif(EXTENSION_BUILT, reason='the extension is built')
def test_load_says_how_to_build_the_extension() -> None:
    with pytest.raises(ImportError, match='maturin develop'):
        YAML().load('a: 1\n')
    with pytest.raises(ImportError, match='maturin develop'):
        YAML().load_all('a: 1\n')


@pytest.mark.skipif(EXTENSION_BUILT, reason='the extension is built')
def test_dump_says_how_to_build_the_extension() -> None:
    with pytest.raises(ImportError, match='maturin develop'):
        YAML().dump({'a': 1})
    with pytest.raises(ImportError, match='maturin develop'):
        YAML().dump_all([{'a': 1}])


def test_everything_else_works_without_the_extension() -> None:
    yaml = YAML()
    yaml.register_class(klass('NoExtensionNeeded', 'libx'))
    yaml.indent(mapping=4)
    document = yamluna.CommentedMap({'a': yamluna.CommentedSeq([1, 2])})
    document['b'] = yamluna.DoubleQuotedScalarString('x')
    assert document == {'a': [1, 2], 'b': 'x'}


# -- the whole pipeline, once it exists -------------------------------------------------


@pytest.mark.usefixtures('pipeline')
def test_load_and_dump_round_trip() -> None:
    yaml = YAML()
    source = '# a comment\na: 1\nb:\n  - x\n  - y\n'
    assert yaml.dump(yaml.load(source)) == source


@pytest.mark.usefixtures('pipeline')
def test_load_all_returns_every_document() -> None:
    assert len(YAML().load_all('a: 1\n---\nb: 2\n')) == 2


@pytest.mark.usefixtures('pipeline')
def test_load_refuses_a_multi_document_stream() -> None:
    with pytest.raises(ComposerError, match='single document'):
        YAML().load('a: 1\n---\nb: 2\n')


@pytest.mark.usefixtures('pipeline')
def test_load_of_an_empty_stream_is_none() -> None:
    assert YAML().load('') is None


@pytest.mark.usefixtures('pipeline')
def test_dump_writes_to_every_stream_kind(tmp_path: Path) -> None:
    yaml = YAML()
    data = yaml.load('a: 1\n')

    text_stream = io.StringIO()
    assert yaml.dump(data, text_stream) is None
    assert text_stream.getvalue() == 'a: 1\n'

    binary_stream = io.BytesIO()
    yaml.dump(data, binary_stream)
    assert binary_stream.getvalue() == b'a: 1\n'

    path = tmp_path / 'out.yaml'
    yaml.dump(data, path)
    assert path.read_bytes() == b'a: 1\n'


@pytest.mark.usefixtures('pipeline')
def test_the_context_manager_writes_one_stream(tmp_path: Path) -> None:
    path = tmp_path / 'out.yaml'
    with YAML(output=path) as yaml:
        yaml.dump(yaml.load('a: 1\n'))
        yaml.dump(yaml.load('b: 2\n'))
    assert path.read_text() == 'a: 1\n---\nb: 2\n'


@pytest.mark.usefixtures('pipeline')
def test_setting_version_forces_the_directive_and_the_marker() -> None:
    yaml = YAML()
    yaml.version = (1, 2)
    assert yaml.dump(yaml.load('a: 1\n')) == '%YAML 1.2\n---\na: 1\n'


# -- documents with no root object -----------------------------------------------------


@pytest.mark.usefixtures('pipeline')
def test_a_comment_only_file_still_loads_as_none() -> None:
    """A document with no root node loads as `None`, whether or not it carries comments."""
    assert YAML().load('# nothing but this\n') is None


@pytest.mark.usefixtures('pipeline')
def test_a_comment_only_file_round_trips() -> None:
    yaml = YAML()
    source = '# one\n# two\n\n# after a blank line\n'
    assert yaml.dump(yaml.load(source)) == source


@pytest.mark.usefixtures('pipeline')
def test_an_empty_document_keeps_its_marker_and_its_comment() -> None:
    yaml = YAML()
    source = '---\n# the whole document\n---\na: 1\n'
    assert yaml.dump_all(yaml.load_all(source)) == source


@pytest.mark.usefixtures('pipeline')
def test_each_empty_document_keeps_its_own_trivia() -> None:
    """The table is keyed by position, so document 2's comment cannot land on document 0."""
    yaml = YAML()
    source = '---\n# first\n---\nkept: 1\n---\n# third\n'
    documents = yaml.load_all(source)
    assert documents == [None, {'kept': 1}, None]
    assert yaml.dump_all(documents) == source


@pytest.mark.usefixtures('pipeline')
def test_the_table_is_replaced_by_the_next_load() -> None:
    """No record survives a load that did not produce it: a bare `None` dumps as `null`."""
    yaml = YAML()
    yaml.load('# a comment\n')
    yaml.load('a: 1\n')
    assert yaml.dump(None) == 'null\n'


@pytest.mark.usefixtures('pipeline')
def test_a_none_with_no_record_is_still_a_null_document() -> None:
    assert YAML().dump(None) == 'null\n'


@pytest.mark.usefixtures('pipeline')
def test_dumping_an_empty_document_twice_is_a_fixed_point() -> None:
    """A dump is a read: emitting the record must neither consume nor mutate it."""
    yaml = YAML()
    source = '---\n# the whole document\n'
    document = yaml.load(source)
    assert yaml.dump(document) == source
    assert yaml.dump(document) == source


# -- null spelling (constructor.NULL_ATTRIB) --------------------------------------------


@pytest.mark.parametrize('source', ['a: ~\n', 'a: null\n', 'a: Null\n', 'a: NULL\n', 'a:\n'])
@pytest.mark.usefixtures('pipeline')
def test_a_null_keeps_the_spelling_it_was_written_with(source: str) -> None:
    yaml = YAML()
    document = yaml.load(source)
    assert document['a'] is None
    assert yaml.dump(document) == source


@pytest.mark.usefixtures('pipeline')
def test_a_null_sequence_item_keeps_its_spelling() -> None:
    yaml = YAML()
    source = '- ~\n- null\n-\n'
    assert yaml.dump(yaml.load(source)) == source


@pytest.mark.usefixtures('pipeline')
def test_a_null_written_in_python_has_no_spelling_to_keep() -> None:
    """Nothing is invented for a key the user added: the empty lexeme is the default."""
    yaml = YAML()
    document = yaml.load('a: ~\n')
    document['b'] = None
    assert yaml.dump(document) == 'a: ~\nb:\n'


@pytest.mark.usefixtures('pipeline')
def test_replacing_a_null_drops_its_spelling() -> None:
    yaml = YAML()
    document = yaml.load('a: ~\n')
    document['a'] = 1
    assert yaml.dump(document) == 'a: 1\n'


# -- the package surface ---------------------------------------------------------------


def test_version_is_a_string() -> None:
    assert isinstance(yamluna.__version__, str) and yamluna.__version__


def test_every_exported_name_exists() -> None:
    missing = [name for name in yamluna.__all__ if not hasattr(yamluna, name)]
    assert missing == []


@pytest.mark.parametrize(
    'name',
    [
        'YAML',
        'CommentedMap',
        'CommentedSeq',
        'CommentedSet',
        'CommentedKeyMap',
        'CommentedKeySeq',
        'TaggedScalar',
        'LiteralScalarString',
        'FoldedScalarString',
        'SingleQuotedScalarString',
        'DoubleQuotedScalarString',
        'PlainScalarString',
        'ScalarInt',
        'HexInt',
        'OctalInt',
        'BinaryInt',
        'ScalarFloat',
        'ScalarBoolean',
        'TimeStamp',
        'YAMLError',
        'MarkedYAMLError',
        'ScannerError',
        'ParserError',
        'ComposerError',
        'ConstructorError',
        'RepresenterError',
        'EmitterError',
        'DuplicateKeyError',
        'YAMLStreamError',
        'Mark',
        'register_class',
        'TagRegistry',
    ],
)
def test_the_documented_surface_is_exported(name: str) -> None:
    assert name in yamluna.__all__
    assert getattr(yamluna, name) is not None
