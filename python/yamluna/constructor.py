"""Turns the flat records the Rust core produced into the Python objects you hold.

The core hands this module a `Doc` record per document, an arena of flat nodes; this
module hands back the `CommentedMap` / `CommentedSeq` / scalar tree that a load returns.
No YAML text is parsed here. Every scalar has already been cooked and every comment
already attached, so all that is left is choosing which Python object each record becomes
and where its trivia hangs.

Two rules decide most of what you get back.

**The lexeme is the truth.** A value comes back as a bare `int`, `str` or `bool` only when
re-rendering that builtin reproduces the source text exactly. `+12`, `0x1F`, `1_000.5`,
`TRUE` and `2002-1-4` therefore arrive as `ScalarInt`, `HexInt`, `ScalarFloat`,
`ScalarBoolean` and `TimeStamp`, each carrying the form it was written in, so a dump
writes it back unchanged.

**An alias is the same object.** `*use` gives you the very object `&base` named, so
`doc['use'] is doc['base']` holds. Containers are created empty, registered under their
anchor, and only then filled, so a recursive anchor (`&sm` containing `*sm`) loads without
recursing forever; `tests/corpus/anchors-recursive.yaml` covers it.

## Duplicate keys

A `CommentedMap` is a `dict`, so two keys that compare equal are one entry. yamluna keeps
the **last** occurrence, matching both YAML's own convention and `dict`. By default a
repeated key raises `DuplicateKeyError` naming both positions. Under
`allow_duplicate_keys=True` you get a `DuplicateKeyFutureWarning` naming both positions
instead, every time; ruamel keeps the first value and says nothing. A repeated merge key
`<<` raises either way.

The records keep every duplicate, so the round trip is intact up to the point where the
tree is built. A `dict` cannot hold both entries, so a duplicate key is the one document
shape whose round trip the Python object model loses, with or without
`allow_duplicate_keys`, since a dump is written from the tree.

## Two documents that will not load

Two ill-formed shapes arrive here as a duplicate key you cannot see coming, and both are
reported as one: `DuplicateKeyError` by default, a warning and a single surviving entry
under `allow_duplicate_keys=True`.

An alias used as a key of the mapping its own anchor is defined in, `{&a [x]: 1, *a : 2}`
(`yaml-test-suite` case X38W), does not merely equal the first key: it is that key, one
object reached twice, so the two entries carry one key and a `dict` holds one of them.
YAML requires the keys of a mapping node to be unique, and an anchor and its alias are the
same node, so the document is ill-formed at the data-model level and an error is the
honest answer. ruamel raises here too; PyYAML never reaches the question, because its keys
are plain lists and it reports an unhashable key first. Pinned by
`tests/test_constructor.py::test_an_alias_to_a_key_of_its_own_mapping_is_a_duplicate`.

Two empty keys, `: a` over `: b` (`yaml-test-suite` case 2JQS), is the same shape with the
alias taken away and gets the same answer. An empty key is the null key, so both entries
carry `None`; the suite tags the case `duplicate-key` itself. Pinned by
`tests/test_constructor.py::test_two_empty_keys_are_one_null_key`.

The Rust core round-trips both documents; what is lost is lost at the Python object
model, and no record field or emitter change reaches it.
"""

from __future__ import annotations

import base64
import binascii
import re
import warnings
from collections.abc import Iterable, Mapping
from typing import Any, Final

from yamluna._record import (
    KIND_ALIAS,
    KIND_MAPPING,
    KIND_SCALAR,
    KIND_SEQUENCE,
    STYLE_DOUBLE,
    STYLE_FLOW,
    STYLE_FOLDED,
    STYLE_LITERAL,
    STYLE_PLAIN,
    STYLE_SINGLE,
    Doc,
    Node,
    Trivia,
)
from yamluna.comments import (
    C_KEY_EOL,
    C_KEY_PRE,
    C_VALUE_EOL,
    C_VALUE_POST,
    CommentedBase,
    CommentedKeyMap,
    CommentedKeySeq,
    CommentedMap,
    CommentedSeq,
    CommentedSet,
    CommentMark,
    CommentToken,
    LineCol,
    Tag,
    TaggedScalar,
)
from yamluna.error import DuplicateKeyFutureWarning, MarkedYAMLError, make_error
from yamluna.registry import ConstructorError as _RegistryError
from yamluna.registry import Registration, TagRegistry
from yamluna.scalarbool import ScalarBoolean
from yamluna.scalarfloat import from_lexeme as _float_from_lexeme
from yamluna.scalarint import ScalarInt
from yamluna.scalarint import from_lexeme as _int_from_lexeme
from yamluna.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    ScalarString,
    SingleQuotedScalarString,
)
from yamluna.timestamp import from_lexeme as _timestamp_from_lexeme

__all__ = [
    'DOC_ATTRIB',
    'EXPLICIT_ATTRIB',
    'FLOW_SEPS_ATTRIB',
    'NODE_ATTRIB',
    'NULL_ATTRIB',
    'SOURCE_ATTRIB',
    'UNRESOLVED',
    'Constructor',
    'construct',
    'construct_all',
    'resolve',
]

# `%YAML`, `%TAG`, `---` and `...` belong to the document, and the object model has no
# document object: a load returns the root. They ride on the root as one `Doc` with an
# empty arena, and `yamluna.representer` reads them back off it.
DOC_ATTRIB: Final = '_yaml_doc'
"""Attribute name under which a loaded root parks its document-level facts.

A dump reproduces the directives and the markers the source had. A document whose root is
a bare `str` or `int`, or that is empty, has nowhere to park them and loses them, the same
class of gap as a bare scalar losing its lexeme.
"""

# `.ca` has no slot for this and ruamel has no notion of it at all, so it is yamluna's
# own; `yamluna.representer` reads it back into `Node.explicit`.
EXPLICIT_ATTRIB: Final = '_yaml_explicit'
"""Attribute name under which a mapping records which keys were written as `? key`.

A `frozenset` of keys on the `CommentedMap`. A key added since the load is not in it, and
is written in the implicit form.
"""

# `Node.flow_seps` verbatim: one run in front of each child and one in front of the
# closing bracket. `CommentedMap` and `CommentedSeq` have no slot of their own for it, and
# `yamluna.representer` reads it back.
FLOW_SEPS_ATTRIB: Final = '_yaml_flow_seps'
"""Attribute name for what a flow collection's source wrote between its lexemes.

The emitter believes the recorded spacing only while its length still matches the
children, so an insertion or a deletion retires it.
"""

# `~`, `null`, `Null`, `NULL` and the empty lexeme all construct to the one `None`
# singleton, which, unlike every other scalar, has nowhere of its own to keep the lexeme
# it came from. Without this store the representer can only guess, and the round trip
# rewrites `tilde: ~` as `tilde:`. A null *key* is not recorded: the empty spelling
# reparses as the same null, and a two-slot record for `? ~: x` is not worth the code.
# Neither is a `!!null` tag.
NULL_ATTRIB: Final = '_yaml_null'
"""Attribute name for the source spelling of a container's `None` children.

A `{key or index: lexeme}` dict on the parent, keyed the way `.lc` is, allocated only when
a null is written as something other than nothing.
"""

# A bare `str`, `int`, `bool`, `bytes` or `None` takes no attribute at all, so for those
# the parent is the only place a record can live. What that saves is everything the value
# alone cannot say. A tag has nowhere to live on what a tagged scalar constructs to
# (`!!str 123` is a bare `str`, `!!int "42"` an `int`, `!!binary |` a `bytes`); an anchor
# on a null has the same problem; and the lexeme rides along with them, because a node
# that keeps its tag and loses its spelling is still not a round trip. Without the store
# the emitter is handed a bare node and reformats what the Rust core had preserved:
# `!!str 123` comes out as `'123'`, a `!!binary` block scalar as a re-wrapped
# double-quoted one, `&empty` as nothing. A lexeme-less scalar at line 0 column 0 is also
# the emitter's one signal for "the user built this", so losing it there lays the whole
# document out afresh.
# ponytail: two stores for one idea (`NULL_ATTRIB` is this, for the one untagged,
# unanchored value that cannot carry its own lexeme); fold them together if a third case
# turns up.
SOURCE_ATTRIB: Final = '_yaml_source'
"""Attribute name for the records a container's children could not keep themselves.

A `{key or index: (value, value record, key record)}` dict on the parent, keyed the way
`NULL_ATTRIB` is. `key record` is `None` for a sequence, which has keys nowhere.
`yamluna.representer` applies the value's record only while the entry still holds the
value that was loaded, so an edited value is written from scratch like any other. A key
needs no such test: it is what the record was looked up by.
"""

# Parked on the object itself, so it travels with the object, and a value rebuilt in
# Python has none. A value that cannot hold an attribute (a bare `str`, `int`, `None`)
# gets the same facts through `SOURCE_ATTRIB`, which its parent already keeps for it, and
# which is exactly the set of scalars that can carry an anchor or a tag at all.
NODE_ATTRIB: Final = '_yaml_node'
"""Attribute name under which a loaded object keeps the record it was built from.

The source facts the object model has no slot for and no use for, such as where the
`&anchor` and the tag were written and where each entry's `:` went, are opaque here. This
layer never reads them; it hands them back to the emitter for a node it did not change,
and the emitter believes a recorded position only while the output is still on the line it
names.
"""

# The `tag:yaml.org,2002:` namespace: the tags a YAML processor knows without being told.
YAML_ORG: Final = 'tag:yaml.org,2002:'

UNRESOLVED: Final = object()
"""Sentinel `resolve` returns for a lexeme that is not a core-schema scalar, so a string."""

_NULL: Final = frozenset(('', '~', 'null', 'Null', 'NULL'))

# YAML 1.2 core schema booleans. `true` and `false` are the canonical spellings, so those
# two, and only those two, may become a bare `bool`.
_BOOL_12: Final = {
    'true': True, 'True': True, 'TRUE': True,
    'false': False, 'False': False, 'FALSE': False,
}
# The extra YAML 1.1 spellings, live only under an explicit `%YAML 1.1`.
_BOOL_11: Final = _BOOL_12 | {
    'y': True, 'Y': True, 'yes': True, 'Yes': True, 'YES': True, 'on': True,
    'On': True, 'ON': True,
    'n': False, 'N': False, 'no': False, 'No': False, 'NO': False, 'off': False,
    'Off': False, 'OFF': False,
}
_CANONICAL_BOOL: Final = frozenset(('true', 'false'))

# Capital `0X`, `0O` and `0B` are integers here; ruamel drops them to strings.
_INT_RE: Final = re.compile(
    r'[-+]?(?:0[bB][01_]+|0[oO][0-7_]+|0[xX][0-9a-fA-F_]+|[0-9][0-9_]*)'
)
_FLOAT_RE: Final = re.compile(
    r'[-+]?(?:\.[0-9_]+|[0-9][0-9_]*(?:\.[0-9_]*)?)(?:[eE][-+]?[0-9_]+)?'
)
_INF_NAN_RE: Final = re.compile(r'[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)')
# Cheap gate before the much larger timestamp pattern is tried.
_DATE_HEAD: Final = re.compile(r'[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}')

_STYLE_CLASS: Final[dict[int, type[ScalarString]]] = {
    STYLE_PLAIN: PlainScalarString,
    STYLE_SINGLE: SingleQuotedScalarString,
    STYLE_DOUBLE: DoubleQuotedScalarString,
    STYLE_LITERAL: LiteralScalarString,
    STYLE_FOLDED: FoldedScalarString,
}
# ruamel's `TaggedScalar.style` spelling.
_STYLE_CHAR: Final[dict[int, str | None]] = {
    STYLE_PLAIN: None,
    STYLE_SINGLE: "'",
    STYLE_DOUBLE: '"',
    STYLE_LITERAL: '|',
    STYLE_FOLDED: '>',
}
_QUOTED: Final = (STYLE_SINGLE, STYLE_DOUBLE)


def resolve(lexeme: str, version: tuple[int, int] | None = None) -> Any:
    """Resolves a plain scalar lexeme against the core schema.

    The result carries the lexeme whenever the value alone cannot reproduce it.

    Args:
        lexeme: The scalar's source text, exactly as written.
        version: The document's YAML version. `(1, 1)` widens the booleans to the YAML 1.1
            spellings (`yes`, `on`, `n`, and the rest); every other value, `None`
            included, uses the YAML 1.2 core schema.

    Returns:
        `None` for a null spelling, a bare `bool`, `int` or `float` where that reproduces
        the lexeme, a `ScalarBoolean` / `ScalarInt` / `ScalarFloat` / `TimeStamp` carrying
        the source form where it does not, or `UNRESOLVED` when the lexeme is a plain
        string.

    Example:
        ```pycon
        >>> resolve('12'), resolve('+12').lexeme(), resolve('0X1F').lexeme()
        (12, '+12', '0X1F')
        >>> resolve('true'), resolve('TRUE').lexeme()
        (True, 'TRUE')
        ```
    """
    if '\n' in lexeme:
        return UNRESOLVED
    if lexeme in _NULL:
        return None
    bools = _BOOL_11 if version == (1, 1) else _BOOL_12
    if lexeme in bools:
        value = bools[lexeme]
        return value if lexeme in _CANONICAL_BOOL else ScalarBoolean(value, lexeme=lexeme)
    if _INT_RE.fullmatch(lexeme):
        return _int(lexeme)
    if _INF_NAN_RE.fullmatch(lexeme) or _FLOAT_RE.fullmatch(lexeme):
        return _float_from_lexeme(lexeme)
    if _DATE_HEAD.match(lexeme):
        try:
            return _timestamp_from_lexeme(lexeme)
        except ValueError:
            pass
    return UNRESOLVED


def _int(lexeme: str) -> Any:
    """A `ScalarInt` subclass, or a bare `int` when `str(int)` reproduces the lexeme."""
    value = _int_from_lexeme(lexeme)
    plain = (
        type(value) is ScalarInt
        and value._width is None
        and not value._underscore
        and value._sign in ('', '-')
    )
    return int(value) if plain else value


class Constructor:
    """The builder that turns `Doc` records into the Python object tree.

    One instance per load is enough: anchors are reset for each document, as YAML
    requires. `source` and `name` are used only to give errors a snippet and a filename.
    """

    __slots__ = (
        '_anchors',
        '_directives',
        '_nodes',
        '_version',
        'allow_duplicate_keys',
        'name',
        'preserve_quotes',
        'registry',
        'source',
        'version',
    )

    def __init__(
        self,
        *,
        registry: TagRegistry | None = None,
        preserve_quotes: bool = False,
        allow_duplicate_keys: bool = False,
        version: tuple[int, int] | None = None,
        source: str | None = None,
        name: str = '<unicode string>',
    ) -> None:
        """Sets up a constructor with the settings one load runs under.

        Args:
            registry: The `TagRegistry` that maps a tag to a registered class. With
                `None`, a tagged node stays a plain container or a `TaggedScalar`.
            preserve_quotes: Give quoted scalars back as `SingleQuotedScalarString` and
                `DoubleQuotedScalarString` instead of a bare `str`, so the quoting style
                survives an edit.
            allow_duplicate_keys: Warn instead of raising when a mapping repeats a key.
                The last occurrence wins either way, and a repeated `<<` still raises.
            version: The YAML version to resolve scalars under when the document carries
                no `%YAML` directive of its own.
            source: The document text, used to put a snippet in an error message.
            name: The filename an error reports.
        """
        self.registry = registry
        self.preserve_quotes = preserve_quotes
        self.allow_duplicate_keys = allow_duplicate_keys
        self.version = version
        self.source = source
        self.name = name
        self._nodes: list[Node] = []
        self._anchors: dict[str, Any] = {}
        self._directives: dict[str, str] = {}
        self._version: tuple[int, int] | None = version

    # -- entry points ---------------------------------------------------------------------

    @classmethod
    def for_yaml(cls, yaml: Any, **overrides: Any) -> Constructor:
        """Returns a constructor taking its settings from a `YAML` instance.

        Args:
            yaml: A `yamluna.main.YAML`. Every setting is read with `getattr`, so an
                object carrying only some of them works.
            **overrides: Keyword arguments for `Constructor`, each winning over the
                setting read from `yaml`.

        Returns:
            A constructor configured for that `YAML`.
        """
        return cls(
            **{
                'registry': getattr(yaml, 'registry', None),
                'preserve_quotes': bool(getattr(yaml, 'preserve_quotes', False)),
                'allow_duplicate_keys': bool(getattr(yaml, 'allow_duplicate_keys', False)),
                'version': getattr(yaml, 'version', None),
                **overrides,
            }
        )

    def construct_all(self, docs: Iterable[Doc]) -> list[Any]:
        """Returns one Python tree per document record, in stream order.

        Args:
            docs: The document records of one stream.

        Returns:
            One root object per document. Anchors do not cross a document boundary.

        Raises:
            ComposerError: An alias names an anchor its document never defines.
            ConstructorError: A node cannot be built from its tag or its text.
            DuplicateKeyError: A mapping repeats a key, or repeats the merge key `<<`.
        """
        return [self.construct(d) for d in docs]

    def construct(self, doc: Doc) -> Any:
        """Returns the object tree of one document record.

        Anchors are reset first, so one constructor serves a whole stream. A registered
        class's `from_yaml` hook runs here, so whatever it raises propagates.

        Args:
            doc: One document record: the node arena, plus everything outside the root.

        Returns:
            The document's root object: a `CommentedMap`, a `CommentedSeq`, a scalar, or
            `None` for a document with no content. A bare builtin root is promoted to the
            scalar class that can hold the document's directives and markers whenever the
            document has any to hold.

        Raises:
            ComposerError: An alias names an anchor the document never defines.
            ConstructorError: A node cannot be built. This covers an unknown node kind, a
                `!!binary` payload that is not base64, a `!!bool`, `!!int`, `!!float` or
                `!!timestamp` whose text does not parse, a `<<` whose value is neither a
                mapping nor a sequence of mappings, a tag that claims a registered
                namespace but matches no registration or matches several, and a registered
                class with no `from_yaml` that cannot be built from the constructed state.
            DuplicateKeyError: A mapping repeats a key. Under `allow_duplicate_keys` a
                `DuplicateKeyFutureWarning` naming both positions is issued instead and
                the last value wins. A repeated merge key `<<` raises either way.
        """
        self._nodes = doc.nodes
        self._anchors = {}
        self._directives = dict(doc.tag_directives)
        self._version = doc.version or self.version
        if doc.root is None:
            return None
        node = doc.nodes[doc.root]
        root = self._build(doc.root)
        if not isinstance(root, CommentedBase) and _has_document_facts(doc):
            root = _park(self._promote(root, node), node)
        if isinstance(root, CommentedBase) or hasattr(type(root), 'lexeme'):
            if isinstance(root, CommentedBase):
                self._prepend_pre(root, self._tokens(doc.leading) + self._tokens(node.before))
                if node.eol is not None:
                    self._set_own_eol(root, node.eol)
                if doc.trailing:
                    root.ca.end = list(root.ca.end) + self._tokens(doc.trailing)
            setattr(root, DOC_ATTRIB, Doc(
                version=doc.version,
                tag_directives=list(doc.tag_directives),
                explicit_start=doc.explicit_start,
                explicit_end=doc.explicit_end,
                leading=list(doc.leading),
                trailing=list(doc.trailing),
                bom=doc.bom,
                final_line_break=doc.final_line_break,
                tags_before_version=doc.tags_before_version,
                directives_raw=doc.directives_raw,
                stream_tail=doc.stream_tail,
                line_space=doc.line_space,
            ))
        return root

    def _promote(self, value: Any, node: Node) -> Any:
        """A bare builtin root as the scalar class that can hold the document's own facts."""
        # A mapping or a sequence root carries the directives and the markers itself; a
        # bare `str` or `int` cannot, so it is promoted to the class that can, the same
        # promotion `_anchored` does for a scalar that has to hold an anchor. `None`,
        # `bytes` and an empty document have nowhere to go and lose them.
        lexeme = node.raw if node.raw is not None else (node.value or '')
        if isinstance(value, bool):
            promoted: Any = ScalarBoolean(value, lexeme=lexeme)
        elif isinstance(value, int):
            promoted = _int_from_lexeme(lexeme)
        elif isinstance(value, float):
            promoted = _float_from_lexeme(lexeme)
        elif isinstance(value, str) and not isinstance(value, ScalarString):
            promoted = _STYLE_CLASS.get(node.style, PlainScalarString)(value, lexeme=node.raw)
        else:
            return value
        # A root has no parent to place it, so it carries its own position. A scalar's
        # `.lc` reads as `None` until assigned; it is a descriptor, not `CommentedBase`'s
        # property.
        promoted.lc = LineCol(node.line, node.col)
        return promoted

    # -- dispatch -------------------------------------------------------------------------

    def _build(self, index: int, as_key: bool = False) -> Any:
        node = self._nodes[index]
        if node.kind == KIND_ALIAS:
            try:
                return self._anchors[node.anchor]
            except KeyError:
                raise self._error(
                    'composer', f'found undefined alias {node.anchor!r}', node
                ) from None
        if node.kind == KIND_SCALAR:
            built = self._anchored(self._scalar(node), node)
        elif node.kind == KIND_SEQUENCE:
            built = self._sequence(node, as_key)
        elif node.kind == KIND_MAPPING:
            built = self._mapping(node, as_key)
        else:
            raise self._error('constructor', f'unknown node kind {node.kind!r}', node)
        # An alias returned above skips this: its site is not where its anchor was
        # written, so the record it would park is the anchor's, and the object holds that
        # one already.
        return _park(built, node)

    # -- scalars --------------------------------------------------------------------------

    def _scalar(self, node: Node) -> Any:
        tag = self._tag(node)
        if tag is None or tag[0] == '!':
            # No tag, or the non-specific `!`: resolve by content.
            return self._plain(node)
        written, resolved = tag
        registration = self._registered(written, node)
        if registration is not None:
            return self._registered_object(registration, node, node.value or '')
        if resolved.startswith(YAML_ORG):
            return self._standard_scalar(resolved[len(YAML_ORG) :], node)
        scalar = TaggedScalar(node.value or '', _STYLE_CHAR.get(node.style), Tag(*node.tag))
        scalar.lc.line, scalar.lc.col = node.line, node.col
        return scalar

    def _plain(self, node: Node) -> Any:
        """An untagged scalar, resolved against the core schema if the style allows it."""
        if node.style != STYLE_PLAIN:
            return self._string(node)  # A quoted or block scalar is always a string.
        lexeme = node.raw if node.raw is not None else (node.value or '')
        value = resolve(lexeme, self._version)
        return self._string(node) if value is UNRESOLVED else value

    def _string(self, node: Node) -> str:
        """The style's `ScalarString` subclass, or a bare `str` where that loses nothing.

        A plain scalar comes back as a bare `str` wherever the value reproduces the
        lexeme. One folded over several lines does not, so it keeps its class and with it
        its lexeme. Quoted scalars keep their class only under `preserve_quotes`, and an
        anchored scalar always keeps a class, because a bare `str` has nowhere to hold
        `&name`.
        """
        value = node.value if node.value is not None else ''
        cls = _STYLE_CLASS.get(node.style, PlainScalarString)
        if node.style == STYLE_PLAIN:
            # The same rule `_int` uses: bare only where the value reproduces the lexeme,
            # so the round trip stays exact.
            bare = node.raw is None or node.raw == value
        else:
            bare = node.style in _QUOTED and not self.preserve_quotes
        if bare and not node.anchor:
            return str(value)
        return cls(value, lexeme=node.raw)

    def _standard_scalar(self, kind: str, node: Node) -> Any:
        """A `tag:yaml.org,2002:` scalar tag: the type is forced, not resolved."""
        value = node.value if node.value is not None else ''
        if kind == 'null':
            return None
        if kind == 'binary':
            try:
                return base64.b64decode(value)
            except (binascii.Error, ValueError) as exc:
                raise self._error(
                    'constructor', f'failed to decode base64 data: {exc}', node
                ) from None
        if kind == 'bool':
            if (found := _BOOL_11.get(value.strip())) is None:
                raise self._error('constructor', f'not a boolean: {value!r}', node)
            return found
        if kind in ('int', 'float', 'timestamp'):
            build = {
                'int': _int, 'float': _float_from_lexeme, 'timestamp': _timestamp_from_lexeme
            }[kind]
            try:
                return build(value.strip())
            except ValueError:
                raise self._error(
                    'constructor', f'not a valid {kind}: {value!r}', node
                ) from None
        return self._string(node)  # !!str, and any other yaml.org scalar tag.

    # -- collections ----------------------------------------------------------------------

    def _sequence(self, node: Node, as_key: bool) -> Any:
        if as_key:
            # A key must hash, so it is a tuple and cannot be filled after the fact.
            seq: Any = CommentedKeySeq(self._build(i, True) for i in node.children)
            self._decorate(seq, node)
            self._place_children(seq, enumerate(node.children))
            # A key is built in one go, so its anchor can only be bound once the key
            # exists. Bound it must be: `{&a [x]: 1, *a: 2}` aliases a key from inside its
            # own mapping.
            return self._register(node, seq)
        seq = CommentedSeq()
        self._register(node, seq)
        for position, child in enumerate(node.children):
            item = self._nodes[child]
            value = self._build(child)
            seq.append(value)
            self._entry_trivia(seq, position, item, item, value)
            self._note_null(seq, position, value, item)
            self._note_source(seq, position, value, item)
            seq.lc.add_idx_line_col(position, [item.line, item.col])
        self._decorate(seq, node)
        return self._tagged_container(seq, node)

    def _place_children(self, owner: Any, items: Iterable[tuple[int, int]]) -> None:
        """Records where each child of a key collection was written."""
        # A key is built in one go, since it has to hash before it can be stored, so it
        # misses the per-item bookkeeping the mutable containers do inline. Without this
        # the emitter lays the key's contents out afresh instead of echoing them.
        for position, child in items:
            item = self._nodes[child]
            owner.lc.add_idx_line_col(position, [item.line, item.col])

    def _mapping(self, node: Node, as_key: bool) -> Any:
        pairs = list(zip(node.children[::2], node.children[1::2], strict=True))
        if as_key:
            built = [(self._build(k, True), self._build(v, True)) for k, v in pairs]
            key_map: Any = CommentedKeyMap(built)
            self._register(node, key_map)
            self._decorate(key_map, node)
            for (key, _), (key_index, value_index) in zip(built, pairs, strict=True):
                k, v = self._nodes[key_index], self._nodes[value_index]
                key_map.lc.add_kv_line_col(key, [k.line, k.col, v.line, v.col])
            return key_map

        merge_positions = set(node.merge)
        explicit_positions = set(node.explicit)
        explicit_keys: list[Any] = []
        mapping = CommentedMap()
        self._register(node, mapping)
        merges: list[Mapping[Any, Any]] = []
        merge_pos: int | None = None
        seen: dict[Any, Node] = {}

        for entry, (key_index, value_index) in enumerate(pairs):
            key_node, value_node = self._nodes[key_index], self._nodes[value_index]
            if 2 * entry in merge_positions:
                if merge_pos is not None:
                    raise self._error(
                        'duplicatekey',
                        'found duplicate merge key "<<".  Duplicate merge keys are never '
                        'allowed, not even when allow_duplicate_keys is True',
                        key_node,
                    )
                merge_pos = entry
                merges.extend(self._merge_values(value_index, value_node))
                self._entry_trivia(mapping, '<<', key_node, value_node, None)
                mapping.lc.add_kv_line_col(
                    '<<', [key_node.line, key_node.col, value_node.line, value_node.col]
                )
                continue

            key = self._build(key_index, as_key=True)
            if 2 * entry in explicit_positions:
                explicit_keys.append(key)
            if key in seen:
                self._duplicate(key, seen[key], key_node)
            seen[key] = key_node
            value = self._build(value_index)
            mapping[key] = value
            self._entry_trivia(mapping, key, key_node, value_node, value)
            self._note_null(mapping, key, value, value_node)
            self._note_source(mapping, key, value, value_node, key_node)
            mapping.lc.add_kv_line_col(
                key, [key_node.line, key_node.col, value_node.line, value_node.col]
            )

        if explicit_keys:
            setattr(mapping, EXPLICIT_ATTRIB, frozenset(explicit_keys))
        if merges:
            mapping.add_yaml_merge(merges)
            mapping.merge.merge_pos = merge_pos or 0
        self._decorate(mapping, node)
        return self._tagged_container(mapping, node)

    def _merge_values(self, index: int, node: Node) -> list[Mapping[Any, Any]]:
        """The mappings behind a `<<`: one alias, or a sequence of them."""
        value = self._build(index)
        found = list(value) if isinstance(value, list) else [value]
        for item in found:
            if not isinstance(item, Mapping):
                raise self._error(
                    'constructor',
                    f'expected a mapping for merging, but found {type(item).__name__}',
                    node,
                )
        return found

    def _tagged_container(self, container: Any, node: Node) -> Any:
        """Applies the node's tag to an already-built container (registered class, `!!set`).

        The container was registered under its anchor before it was filled, so a recursive
        anchor works. When the tag replaces it with something else, the anchor is
        re-pointed at the replacement.
        """
        # ponytail: an alias inside a registered class's own subtree still sees the raw
        # container; write the two-phase protocol if a class ever needs it.
        tag = self._tag(node)
        if tag is None or tag[0] == '!':
            return container
        written, resolved = tag
        registration = self._registered(written, node)
        if registration is not None:
            return self._register(node, self._registered_object(registration, node, container))
        if resolved == YAML_ORG + 'set' and isinstance(container, dict):
            members = CommentedSet(container)
            container.copy_attributes(members)
            # `copy_attributes` knows only ruamel's attributes; the three yamluna adds are
            # ours to carry, and `? a` is written with the first of them.
            for attrib in (EXPLICIT_ATTRIB, FLOW_SEPS_ATTRIB, NULL_ATTRIB, SOURCE_ATTRIB):
                if (carried := getattr(container, attrib, None)) is not None:
                    setattr(members, attrib, carried)
            return self._register(node, members)
        return container

    # -- registered classes ---------------------------------------------------------------

    def _registered(self, written: str, node: Node) -> Registration | None:
        if self.registry is None:
            return None
        try:
            return self.registry.resolve(written, self._directives)
        except _RegistryError as exc:
            # The registry raises for an unresolvable or ambiguous tag and names every
            # candidate; re-raise it as this package's marked error, message intact.
            raise self._error('constructor', _message(exc), node) from None

    def _registered_object(self, registration: Registration, node: Node, state: Any) -> Any:
        cls = registration.cls
        hook = getattr(cls, 'from_yaml', None)
        if hook is not None:
            return hook(self, node)  # ruamel's (constructor, node) signature
        obj = cls.__new__(cls)
        setstate = getattr(obj, '__setstate__', None)
        if setstate is not None:
            setstate(state)
        elif isinstance(state, Mapping):
            obj.__dict__.update(state)
        else:
            raise self._error(
                'constructor',
                f'cannot construct {registration.path} from a '
                f'{type(state).__name__}: give it a from_yaml classmethod',
                node,
            )
        return obj

    # -- shared node decoration -----------------------------------------------------------

    def _register(self, node: Node, obj: Any) -> Any:
        """Binds `&name` to `obj` before the container is filled, so recursion terminates."""
        if node.anchor:
            self._anchors[node.anchor] = obj
        return obj

    def _decorate(self, obj: Any, node: Node) -> None:
        """Sets a container's style, anchor, tag, position and own trivia from `node`."""
        if node.style == STYLE_FLOW:
            obj.fa.set_flow_style()
        else:
            obj.fa.set_block_style()
        if node.anchor:
            # always_dump: an anchor in the source is source text, so it is always
            # re-emitted, however few times it is referenced.
            obj.yaml_set_anchor(node.anchor, always_dump=True)
        if node.tag is not None:
            obj.tag = Tag(*node.tag)
        obj.lc.line, obj.lc.col = node.line, node.col
        if node.flow_seps:
            setattr(obj, FLOW_SEPS_ATTRIB, node.flow_seps)
        if node.inner:
            self._prepend_pre(obj, self._tokens(node.inner))
        if node.after:
            obj.ca.end = list(obj.ca.end) + self._tokens(node.after)

    def _anchored(self, value: Any, node: Node) -> Any:
        """Registers a scalar's anchor, promoting the value if a builtin cannot hold one."""
        if not node.anchor:
            return value
        if not hasattr(value, 'yaml_set_anchor'):
            lexeme = node.raw if node.raw is not None else (node.value or '')
            if isinstance(value, bool):
                value = ScalarBoolean(value, lexeme=lexeme)
            elif isinstance(value, int):
                value = _int_from_lexeme(lexeme)
            elif isinstance(value, str):
                value = PlainScalarString(value, lexeme=node.raw)
            else:
                # None and bytes have nowhere to hold it; the record keeps the anchor.
                return self._register(node, value)
        value.yaml_set_anchor(node.anchor, always_dump=True)
        return self._register(node, value)

    @staticmethod
    def _note_null(owner: Any, key: Any, value: Any, node: Node) -> None:
        """Records how a `None` child was spelled, when it was spelled at all.

        Only `None` has this problem: every other scalar type carries its own lexeme. See
        `NULL_ATTRIB`.
        """
        if value is not None or not node.raw:
            return
        store = getattr(owner, NULL_ATTRIB, None)
        if store is None:
            store = {}
            setattr(owner, NULL_ATTRIB, store)
        store[key] = node.raw

    @staticmethod
    def _note_source(
        owner: Any, key: Any, value: Any, node: Node, key_node: Node | None = None
    ) -> None:
        """Keeps the records of one entry's children that could not keep them themselves.

        `_park` slides off a bare `str`, `int` or `None`, and then the parent is the only
        place left. See `SOURCE_ATTRIB`.
        """
        if getattr(value, NODE_ATTRIB, None) is node and (
            key_node is None or getattr(key, NODE_ATTRIB, None) is key_node
        ):
            return  # Both children kept their own; nothing for the parent to hold.
        store = getattr(owner, SOURCE_ATTRIB, None)
        if store is None:
            store = {}
            setattr(owner, SOURCE_ATTRIB, store)
        store[key] = (value, node, key_node)

    # -- trivia ---------------------------------------------------------------------------

    # Every comment is written through `_ca_record()`, `ca.comment` or `ca.end`, the
    # identity-keyed store that `comments.py` projects `.ca.items` from. Nothing here
    # writes `.ca.items`.
    #
    # Where each of a node's four trivia slots lands, given a parent container P and a
    # child node:
    #
    #   key `before`     P._ca_record(key)[C_KEY_PRE]
    #   key `eol`        P._ca_record(key)[C_KEY_EOL]
    #   value `eol`      P._ca_record(key)[C_VALUE_EOL]
    #   value `before`   the value's own ca.comment[1] if it is a container, else
    #                    P._ca_record(key)[C_VALUE_POST], ahead of its `after` tokens
    #   value `after`    the value's own ca.end if it is a container, else C_VALUE_POST
    #   own `inner`      this node's ca.comment[1]
    #   root/document    Doc.leading prefixes the root's ca.comment[1], Doc.trailing
    #                    extends its ca.end, and the root's eol is ca.comment[0]
    #
    # A sequence element uses the same three key slots (C_ELEM_PRE, C_ELEM_EOL and
    # C_ELEM_POST), keyed by index into the element-parallel store.

    def _entry_trivia(
        self, owner: CommentedBase, key: Any, key_node: Node, value_node: Node, value: Any
    ) -> None:
        """Writes one entry's trivia into `owner`'s store, allocating a record only if needed.

        For a sequence element `key_node` and `value_node` are the same node, so the
        element gets `C_ELEM_PRE` and `C_ELEM_EOL`, which are the key slots, and its own
        `eol` is not written twice.
        """
        same = value_node is key_node
        post: list[CommentToken] = []
        if isinstance(value, CommentedBase):
            if not same:  # `after` is already on the container's own ca.end via _decorate
                self._prepend_pre(value, self._tokens(value_node.before))
        else:
            if not same:
                post = self._tokens(value_node.before)
            post += self._tokens(value_node.after)
        pre = self._tokens(key_node.before)
        key_eol = None if key_node.eol is None else self._token(key_node.eol)
        value_eol = None if same or value_node.eol is None else self._token(value_node.eol)
        if not (pre or post or key_eol or value_eol):
            return
        record = owner._ca_record(key)
        record[C_KEY_PRE] = pre or None
        record[C_KEY_EOL] = key_eol
        record[C_VALUE_EOL] = value_eol
        record[C_VALUE_POST] = post or None

    def _tokens(self, trivia: Iterable[Trivia]) -> list[CommentToken]:
        """One comment token per trivium, and `n` tokens for a run of `n` blank lines."""
        out: list[CommentToken] = []
        for item in trivia:
            if item.blank_lines:
                out += [
                    CommentToken('\n', CommentMark(item.col))
                    for _ in range(item.blank_lines)
                ]
            elif item.text is not None:
                out.append(self._token(item))
        return out

    def _token(self, trivia: Trivia) -> CommentToken:
        """One trivium as a `CommentToken`.

        An own-line comment carries the trailing newline and an end-of-line comment does
        not, which is `comments.CommentToken`'s convention and what makes the flag survive
        the trip.
        """
        if trivia.blank_lines or trivia.text is None:
            return CommentToken('\n' * max(trivia.blank_lines, 1), CommentMark(trivia.col))
        text = trivia.text + '\n' if trivia.own_line else trivia.text
        return CommentToken(text, CommentMark(trivia.col))

    def _prepend_pre(self, obj: CommentedBase, tokens: list[CommentToken]) -> None:
        """Puts `tokens` in front of the own-line comments `obj` already carries."""
        if not tokens:
            return
        current = obj.ca.comment
        if current is None:
            obj.ca.comment = [None, tokens]
        else:
            current[1] = tokens + list(current[1] or [])

    def _set_own_eol(self, obj: CommentedBase, trivia: Trivia) -> None:
        """Sets `obj`'s own end-of-line comment, leaving its own-line comments alone."""
        current = obj.ca.comment
        if current is None:
            obj.ca.comment = [self._token(trivia), None]
        else:
            current[0] = self._token(trivia)

    # -- errors ---------------------------------------------------------------------------

    def _duplicate(self, key: Any, first: Node, second: Node) -> None:
        """Raises or warns for a key the mapping already holds, naming both positions."""
        # The two entries collapse into one `dict` entry, and for an alias used as a key
        # of its own mapping, or for two empty keys, they are one key object reached
        # twice. No wrapper rescues those: a wrapper keyed on identity cannot separate an
        # object from itself, and only the entry's position in the source could. Keying a
        # `Mapping` on source position would break `doc[key]` for every well-formed
        # document to rescue an ill-formed one, and special-casing the empty key alone
        # would go on to accept `a: 1` over `a: 2`.
        where = (
            f'{key!r} first at line {first.line + 1}, column {first.col + 1}, '
            f'again at line {second.line + 1}, column {second.col + 1}'
        )
        if not self.allow_duplicate_keys:
            raise self._error('duplicatekey', f'found duplicate key {where}', second)
        warnings.warn(
            f'duplicate key {where}; the last value wins',
            DuplicateKeyFutureWarning,
            stacklevel=2,
        )

    def _error(self, kind: str, message: str, node: Node) -> MarkedYAMLError:
        """The marked error of `kind`, positioned at `node` and carrying the source snippet."""
        return make_error(
            kind, message, node.line, node.col, source=self.source, name=self.name
        )

    # -- tags -----------------------------------------------------------------------------

    def _tag(self, node: Node) -> tuple[str, str] | None:
        """`(as written, resolved)`, filling in a missing `resolved` for hand-built nodes."""
        if node.tag is None:
            return None
        handle, suffix, resolved = node.tag
        handle, suffix = handle or '', suffix or ''
        if not resolved:
            resolved = YAML_ORG + suffix if handle == '!!' else handle + suffix
        return handle + suffix, resolved


def _park(value: Any, node: Node) -> Any:
    """Parks on `value` the record it was built from, and returns `value`.

    A bare `str`, `int` or `None` is left alone; the parent keeps its record under
    `SOURCE_ATTRIB` instead.
    """
    # Those builtins have no `__dict__` and no slot for the attribute either, so the
    # `setattr` below would raise on them.
    if hasattr(value, '__dict__') or hasattr(type(value), NODE_ATTRIB):
        setattr(value, NODE_ATTRIB, node)
    return value


def _has_document_facts(doc: Doc) -> bool:
    """Whether keeping a bare builtin root bare would throw anything away.

    Covers the document's own facts and the root node's. A bare `str` keeps no tag, no
    anchor, no comment and no record of where its properties were written, and a root has
    no parent to keep them for it.
    """
    # Promoting the root to a class that can hold `NODE_ATTRIB` is the whole of the fix;
    # nothing here reads what is on the record.
    root = None if doc.root is None else doc.nodes[doc.root]
    return bool(
        doc.version
        or doc.tag_directives
        or doc.explicit_start
        or doc.explicit_end
        or doc.leading
        or doc.trailing
        or doc.bom
        or not doc.final_line_break
        or doc.directives_raw
        or doc.stream_tail
        or doc.line_space
        or (root is not None and (root.tag or root.anchor or root.eol or root.after))
    )


def _message(exc: Exception) -> str:
    """The registry's message, whichever `ConstructorError` it happened to raise."""
    return str(exc) if str(exc) else repr(exc)


def construct(doc: Doc, yaml: Any = None, **options: Any) -> Any:
    """Returns the Python tree of one document record.

    Args:
        doc: The document record to build.
        yaml: An optional `yamluna.main.YAML` whose settings supply the defaults.
        **options: `Constructor` keyword arguments, each overriding what `yaml` supplies.

    Returns:
        The document's root object, or `None` for a document with no content.

    Raises:
        ComposerError: An alias names an anchor the document never defines.
        ConstructorError: A node cannot be built from its tag or its text.
        DuplicateKeyError: A mapping repeats a key, or repeats the merge key `<<`.
    """
    return _for(yaml, options).construct(doc)


def construct_all(docs: Iterable[Doc], yaml: Any = None, **options: Any) -> list[Any]:
    """Returns one Python tree per document record of a stream.

    Args:
        docs: The document records, in stream order.
        yaml: An optional `yamluna.main.YAML` whose settings supply the defaults.
        **options: `Constructor` keyword arguments, each overriding what `yaml` supplies.

    Returns:
        One root object per document, in the same order. Anchors do not cross a document
        boundary.

    Raises:
        ComposerError: An alias names an anchor its document never defines.
        ConstructorError: A node cannot be built from its tag or its text.
        DuplicateKeyError: A mapping repeats a key, or repeats the merge key `<<`.
    """
    return _for(yaml, options).construct_all(docs)


def _for(yaml: Any, options: dict[str, Any]) -> Constructor:
    """The constructor for `yaml` and `options`, with `options` winning."""
    return Constructor(**options) if yaml is None else Constructor.for_yaml(yaml, **options)
