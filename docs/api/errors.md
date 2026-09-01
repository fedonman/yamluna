# Errors

The hierarchy mirrors `ruamel.yaml.error`, one class per ruamel class that `typ='rt'` can
raise, so an `except` block carried over from ruamel keeps compiling and keeps catching.
[Errors](../guide/errors.md) shows what each one looks like on a real broken document.

```text
Exception
├── YAMLError
│   └── MarkedYAMLError
│       ├── ScannerError
│       ├── ParserError
│       ├── ComposerError
│       ├── ConstructorError
│       ├── RepresenterError
│       ├── EmitterError
│       └── DuplicateKeyError
└── YAMLStreamError

Warning
├── YAMLWarning
│   ├── MarkedYAMLWarning
│   └── ReusedAnchorWarning
└── YAMLFutureWarning
    └── MarkedYAMLFutureWarning
        └── DuplicateKeyFutureWarning
```

Two things in that tree are worth knowing before you write the `except`. `YAMLStreamError`
sits beside `YAMLError` rather than under it, which is where ruamel puts it, so it is not
caught by `except YAMLError`. And two of the names never fire: the Rust core reports every
parse failure as a `ScannerError`, so `ParserError` is an import-compatibility shim, and
yamluna keeps both definitions of a re-used anchor, so `ReusedAnchorWarning` is never issued.
Catching `YAMLError` covers everything yamluna raises out of a load or a dump.

## Errors

::: yamluna.YAMLError

::: yamluna.MarkedYAMLError

::: yamluna.ScannerError

::: yamluna.ParserError

::: yamluna.ComposerError

::: yamluna.ConstructorError

::: yamluna.RepresenterError

::: yamluna.EmitterError

::: yamluna.DuplicateKeyError

::: yamluna.YAMLStreamError

## Warnings

::: yamluna.YAMLWarning

::: yamluna.MarkedYAMLWarning

::: yamluna.ReusedAnchorWarning

::: yamluna.YAMLFutureWarning

::: yamluna.MarkedYAMLFutureWarning

::: yamluna.DuplicateKeyFutureWarning

## Positions

A `MarkedYAMLError` carries up to two marks, `context_mark` and `problem_mark`, and both are
`Mark` objects. `line` and `column` are 0-based; the rendered message prints them 1-based.
`FileMark`, `StringMark` and `StreamMark` are ruamel's three names for the same thing, and in
yamluna they are the same class: a mark with no `buffer` behaves exactly as ruamel's
`StreamMark` does.

::: yamluna.Mark

::: yamluna.FileMark

::: yamluna.StringMark

::: yamluna.StreamMark
