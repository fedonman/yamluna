"""Round-trip YAML for Python.

``yamluna`` loads a document, lets you change it, and writes it back with the comments,
blank lines, quoting, anchors and layout the author put there.  It replaces
``ruamel.yaml``'s ``typ='rt'`` and fixes the bugs that mode has (``docs/DIVERGENCES.md``).

::

    from pathlib import Path
    from yamluna import YAML

    yaml = YAML()                    # typ='rt' is the only mode
    yaml.preserve_quotes = True
    config = yaml.load(Path('config.yaml'))
    config['answer'] = 42
    yaml.dump(config, Path('config.yaml'))

Importing this package needs no Rust extension: the object model, the scalar types, the
error hierarchy and the tag registry are pure Python.  :meth:`YAML.load` and
:meth:`YAML.dump` are what need ``yamluna._yamluna``, and they say so if it is missing.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _dist_version

from .comments import (
    Anchor,
    Comment,
    CommentedBase,
    CommentedKeyMap,
    CommentedKeySeq,
    CommentedMap,
    CommentedSeq,
    CommentedSet,
    CommentMark,
    CommentToken,
    Format,
    LineCol,
    Tag,
    TaggedScalar,
)
from .error import (
    ComposerError,
    ConstructorError,
    DuplicateKeyError,
    DuplicateKeyFutureWarning,
    EmitterError,
    FileMark,
    Mark,
    MarkedYAMLError,
    MarkedYAMLFutureWarning,
    MarkedYAMLWarning,
    ParserError,
    RepresenterError,
    ReusedAnchorWarning,
    ScannerError,
    StreamMark,
    StringMark,
    YAMLError,
    YAMLFutureWarning,
    YAMLStreamError,
    YAMLWarning,
)
from .main import YAML, default_registry, register_class
from .registry import Registration, TagDirective, TagRegistry, WirePlan
from .scalarbool import ScalarBoolean
from .scalarfloat import ScalarFloat
from .scalarint import BinaryInt, HexInt, OctalInt, ScalarInt
from .scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    PreservedScalarString,
    ScalarString,
    SingleQuotedScalarString,
    preserve_literal,
    walk_tree,
)
from .timestamp import TimeStamp

try:
    __version__ = _dist_version('yamluna')
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = '0.1.0'

__all__ = [
    'YAML',
    'Anchor',
    'BinaryInt',
    'Comment',
    'CommentMark',
    'CommentToken',
    'CommentedBase',
    'CommentedKeyMap',
    'CommentedKeySeq',
    'CommentedMap',
    'CommentedSeq',
    'CommentedSet',
    'ComposerError',
    'ConstructorError',
    'DoubleQuotedScalarString',
    'DuplicateKeyError',
    'DuplicateKeyFutureWarning',
    'EmitterError',
    'FileMark',
    'FoldedScalarString',
    'Format',
    'HexInt',
    'LineCol',
    'LiteralScalarString',
    'Mark',
    'MarkedYAMLError',
    'MarkedYAMLFutureWarning',
    'MarkedYAMLWarning',
    'OctalInt',
    'ParserError',
    'PlainScalarString',
    'PreservedScalarString',
    'Registration',
    'RepresenterError',
    'ReusedAnchorWarning',
    'ScalarBoolean',
    'ScalarFloat',
    'ScalarInt',
    'ScalarString',
    'ScannerError',
    'SingleQuotedScalarString',
    'StreamMark',
    'StringMark',
    'Tag',
    'TagDirective',
    'TagRegistry',
    'TaggedScalar',
    'TimeStamp',
    'WirePlan',
    'YAMLError',
    'YAMLFutureWarning',
    'YAMLStreamError',
    'YAMLWarning',
    '__version__',
    'default_registry',
    'preserve_literal',
    'register_class',
    'walk_tree',
]
