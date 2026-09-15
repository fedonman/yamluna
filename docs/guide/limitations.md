# Known limitations

yamluna is in alpha. It preserves the project's round-trip corpus, but these cases still need care when editing files.

## Comments and list insertions

- A comment above a collection's **first item** stays at the top of the collection when that item moves or is deleted.
- Inserting or assigning a slice immediately before a list item with a comment above it can put a `-` on a separate line and change how the list loads.

Check the output after these edits. Prefer appending to a commented list when that fits your task.

## Indentation

Leave `indent()` unset when editing an existing file. Applying it to a loaded list whose dashes start at the parent key's column can change the layout; lists of mappings can become invalid YAML. Use it for new documents and check any loaded content you explicitly reformat.

## Line endings and encoding

For CRLF files, set `yaml.line_break = '\r\n'`. Automatic detection currently depends on multiline values, so some files otherwise save with LF endings. Output files use UTF-8 unless you set `yaml.encoding`.

## Registered classes

Converting a tagged mapping into a Python object can lose comments and formatting inside that object. Leave the tag unregistered when those details need to survive. See [custom classes](custom-classes.md).

## Duplicate keys

A Python dictionary cannot retain two entries with the same key. yamluna rejects duplicates by default; allowing them keeps the last value and emits a warning.

## Empty documents

Save empty or comment-only documents using the same `YAML()` instance, before loading another stream on it. If a multi-document stream includes empty documents, keep their positions when saving to preserve their comments and markers.

Found another case? [Report it](https://github.com/fedonman/yamluna/issues) with a small input, the edit you made, and the output you expected.
