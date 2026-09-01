# API reference

Every public name in `yamluna`, rendered from the docstrings in
[`python/yamluna/`](https://github.com/qilimanjaro-tech/yamluna/tree/master/python/yamluna).
These pages say what each class is and what each method does. The [Guide](../guide/index.md)
says when you would reach for one.

## The import surface

Everything public lives on the top-level package. There is no submodule to import from, no
plug-in to load, and no module-level `load()` / `dump()`:

```pycon
>>> from yamluna import YAML, LiteralScalarString
>>> yaml = YAML()
>>> data = yaml.load('note: hello\n')
>>> data['note'] = LiteralScalarString('one\ntwo\n')
>>> print(yaml.dump(data), end='')
note: |
  one
  two
```

`yamluna.__all__` holds 58 names. The tables below cover all of them, and the [Guide](../guide/index.md)
covers the handful you need for ordinary work: `YAML`, `CommentedMap`, `CommentedSeq` and the
scalar string classes.

## [The YAML object](yaml.md)

Loading, dumping, the emitter settings, and the two module-level names that give an
application one shared registry.

| Name | What it is |
|---|---|
| [`YAML`](yaml.md#yamluna.YAML) | Round-trip YAML reader and writer. One instance carries the settings, the registry and the last stream's records. |
| [`register_class`](yaml.md#yamluna.register_class) | Registers a class with `default_registry`. Works as a decorator. |
| [`default_registry`](yaml.md#yamluna.default_registry) | The registry that module-level `register_class` writes to. A plain `YAML()` does not consult it. |

## [Containers](containers.md)

What a load hands back, and the attributes each node carries: its comments, its position, its
anchor, its tag, its flow-or-block preference.

| Name | What it is |
|---|---|
| [`CommentedMap`](containers.md#yamluna.CommentedMap) | A YAML mapping, and a `dict` in every other respect. |
| [`CommentedSeq`](containers.md#yamluna.CommentedSeq) | A YAML sequence, and a `list` in every other respect. |
| [`CommentedSet`](containers.md#yamluna.CommentedSet) | A YAML `!!set`, and a `set` in every other respect. |
| [`CommentedKeyMap`](containers.md#yamluna.CommentedKeyMap) | A mapping standing as a mapping key: a `tuple` of pairs, so it hashes. |
| [`CommentedKeySeq`](containers.md#yamluna.CommentedKeySeq) | A sequence standing as a mapping key: a `tuple`, so it hashes. |
| [`CommentedBase`](containers.md#yamluna.CommentedBase) | The YAML attributes every node carries. Base of all five containers. |
| [`Comment`](containers.md#yamluna.Comment) | What `.ca` gives you: the comments attached to one node. |
| [`CommentToken`](containers.md#yamluna.CommentToken) | One piece of trivia: a comment, or a blank line when its value is blank. |
| [`CommentMark`](containers.md#yamluna.CommentMark) | The place a comment starts. |
| [`Anchor`](containers.md#yamluna.Anchor) | The `&name` on a node. |
| [`Tag`](containers.md#yamluna.Tag) | A tag, both as written and as resolved. |
| [`TaggedScalar`](containers.md#yamluna.TaggedScalar) | A scalar carrying a tag no registered class claims, so it round-trips as written. |
| [`Format`](containers.md#yamluna.Format) | The flow or block preference recorded on one node. |
| [`LineCol`](containers.md#yamluna.LineCol) | Where a node sat in the source, 0-based in both line and column. |

## [Scalar types](scalars.md)

One class per YAML scalar style and per number spelling. Each remembers the text it was
parsed from, which is what makes an untouched scalar come back byte for byte.

| Name | What it is |
|---|---|
| [`ScalarString`](scalars.md#yamluna.ScalarString) | A `str` that remembers which YAML scalar style wrote it. |
| [`LiteralScalarString`](scalars.md#yamluna.LiteralScalarString) | A literal block scalar, with its line breaks kept. |
| [`FoldedScalarString`](scalars.md#yamluna.FoldedScalarString) | A folded block scalar, whose line breaks fold back into spaces. |
| [`SingleQuotedScalarString`](scalars.md#yamluna.SingleQuotedScalarString) | A string in single quotes, where the only escape is `''`. |
| [`DoubleQuotedScalarString`](scalars.md#yamluna.DoubleQuotedScalarString) | A string in double quotes, with the full set of backslash escapes. |
| [`PlainScalarString`](scalars.md#yamluna.PlainScalarString) | A string written without quotes, whenever the value can be written that way. |
| [`PreservedScalarString`](scalars.md#yamluna.PreservedScalarString) | ruamel's older name for `LiteralScalarString`. The same class. |
| [`preserve_literal`](scalars.md#yamluna.preserve_literal) | Normalise the line breaks in a string and mark it for dumping as a literal block. |
| [`walk_tree`](scalars.md#yamluna.walk_tree) | Convert the strings in a loaded tree, in place and recursively. |
| [`ScalarInt`](scalars.md#yamluna.ScalarInt) | A decimal integer that keeps its width, underscores and explicit `+`. |
| [`HexInt`](scalars.md#yamluna.HexInt) | An integer written in base sixteen, as `0x1f` or `0x1F`. |
| [`OctalInt`](scalars.md#yamluna.OctalInt) | An integer written in base eight, as `0o755`. |
| [`BinaryInt`](scalars.md#yamluna.BinaryInt) | An integer written in base two, as `0b1010`. |
| [`ScalarFloat`](scalars.md#yamluna.ScalarFloat) | A float that round-trips its source spelling. |
| [`ScalarBoolean`](scalars.md#yamluna.ScalarBoolean) | A boolean that remembers whether the source said `true`, `True`, `yes` or `on`. |
| [`TimeStamp`](scalars.md#yamluna.TimeStamp) | A `datetime` carrying the exact text it was parsed from. |

## [Tag registry](registry.md)

Which class a tag names, and what a document writes it as. Pure Python, with no parser and no
emitter in it.

| Name | What it is |
|---|---|
| [`TagRegistry`](registry.md#yamluna.TagRegistry) | Which class a tag names, keyed on the fully qualified class path. |
| [`Registration`](registry.md#yamluna.Registration) | One registered class and the wire identity the registry gives it. |
| [`TagDirective`](registry.md#yamluna.TagDirective) | One `%TAG {handle} {prefix}` line. |
| [`WirePlan`](registry.md#yamluna.WirePlan) | What a document needs on the wire for the classes it uses. |

## [Errors](errors.md)

The exception and warning hierarchy, laid out one class per `ruamel.yaml.error` class that
`typ='rt'` can raise, plus the source positions the errors carry.

| Name | What it is |
|---|---|
| [`YAMLError`](errors.md#yamluna.YAMLError) | Base class of the YAML errors. Catch it to catch any of them. |
| [`MarkedYAMLError`](errors.md#yamluna.MarkedYAMLError) | A `YAMLError` that says where in the source it happened. |
| [`ScannerError`](errors.md#yamluna.ScannerError) | Raised when the source is not well-formed YAML. |
| [`ParserError`](errors.md#yamluna.ParserError) | Kept for compatibility with ruamel's name. yamluna does not raise it. |
| [`ComposerError`](errors.md#yamluna.ComposerError) | Raised by `YAML.load` when the stream holds more than one document. |
| [`ConstructorError`](errors.md#yamluna.ConstructorError) | Raised when a node cannot be turned into a Python object. |
| [`RepresenterError`](errors.md#yamluna.RepresenterError) | Raised when dumping meets an object it has no representation for. |
| [`EmitterError`](errors.md#yamluna.EmitterError) | Raised when the Rust emitter cannot write the model it was given. |
| [`DuplicateKeyError`](errors.md#yamluna.DuplicateKeyError) | Raised when a mapping repeats a key and `allow_duplicate_keys` is off. |
| [`YAMLStreamError`](errors.md#yamluna.YAMLStreamError) | Raised for a stream yamluna cannot read from or write to. |
| [`Mark`](errors.md#yamluna.Mark) | A position in a stream, with the text around it when there is any. |
| [`FileMark`](errors.md#yamluna.FileMark) | ruamel's name for a mark into a file. The same class as `Mark`. |
| [`StringMark`](errors.md#yamluna.StringMark) | ruamel's name for a mark into a string. The same class as `Mark`. |
| [`StreamMark`](errors.md#yamluna.StreamMark) | ruamel's name for a mark into a stream. The same class as `Mark`. |
| [`YAMLWarning`](errors.md#yamluna.YAMLWarning) | Base class of the warnings yamluna issues. |
| [`MarkedYAMLWarning`](errors.md#yamluna.MarkedYAMLWarning) | A `YAMLWarning` that says where in the source it happened. |
| [`ReusedAnchorWarning`](errors.md#yamluna.ReusedAnchorWarning) | ruamel's warning for a document that defines the same anchor twice. |
| [`YAMLFutureWarning`](errors.md#yamluna.YAMLFutureWarning) | Base class of the warnings about behaviour that is going to change. |
| [`MarkedYAMLFutureWarning`](errors.md#yamluna.MarkedYAMLFutureWarning) | A `YAMLFutureWarning` that says where in the source it applies. |
| [`DuplicateKeyFutureWarning`](errors.md#yamluna.DuplicateKeyFutureWarning) | Warned for a repeated mapping key when `allow_duplicate_keys` is on. |

## Version

`yamluna.__version__` is the installed distribution's version, read from the package
metadata. A source checkout with nothing installed has no metadata to read, so it reports
`'0.1.0'`.

::: yamluna.__version__
