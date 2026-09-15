# Errors

All YAML exceptions below inherit from `YAMLError` and can be imported from `yamluna`. Catch that base class to report YAML failures together.

| Exception | Meaning |
| --- | --- |
| `ScannerError` | Invalid YAML syntax or an undefined alias |
| `ComposerError` | `load()` received more than one document |
| `ConstructorError` | A value or tag could not be loaded |
| `DuplicateKeyError` | A mapping repeats a key; subclass of `ConstructorError` |
| `RepresenterError` | An object could not be converted to YAML |
| `EmitterError` | The document could not be written as YAML |
| `YAMLStreamError` | An unsupported input, output, or context-manager argument |

`MarkedYAMLError` adds `problem`, `problem_mark`, `context`, and `context_mark`. A mark has `name`, `line`, and `column` attributes. Line and column numbers start at zero. Not every error has a mark; use `getattr(error, 'problem_mark', None)`.

`ParserError` is available for ruamel.yaml compatibility; syntax errors from yamluna are reported as `ScannerError`.

Warnings inherit from `YAMLWarning`. With `allow_duplicate_keys = True`, a repeated key emits `DuplicateKeyFutureWarning` and the last value wins.

File-access errors remain ordinary Python exceptions such as `FileNotFoundError`. Invalid settings may raise `ValueError` or `TypeError`.

See [handle errors](../guide/errors.md) for a runnable example with output.
