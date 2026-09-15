# Changelog

## 0.1.0 — unreleased

First alpha release of yamluna: edit YAML from Python while keeping the file readable.

### Features

- Load, edit, and save YAML with comments, blank lines, quotes, anchors, and layout.
- Work with familiar dictionaries and lists, including helpers to add comments and rename keys.
- Preserve comments when reordering or deleting entries, subject to the limits below.
- Read and write multiple documents with `load_all()` and `dump_all()`.
- Save Python classes with tags that distinguish their source packages; keep registrations separate for each `YAML()` instance.
- Choose styles for new strings and numbers, indentation, and document markers.
- Read YAML 1.2 and documents that explicitly declare YAML 1.1.
- Support Python 3.11+, with type annotations. Licensed under MIT or Apache-2.0.

The [comparison](https://fedonman.github.io/yamluna/comparison/) has preservation results and recorded performance measurements against ruamel.yaml 0.19.1.

### Known limitations

- Comments above a collection's first item can stay behind when the item moves.
- Inserting into commented lists can change the output's structure.
- Explicit indentation settings can produce invalid output for some loaded lists.
- Some CRLF files need `line_break = '\r\n'` to keep their line endings.
- Registered Python objects may lose their internal comments and formatting.
- Duplicate keys cannot both be retained in a Python dictionary.
- Empty documents need the same loader and their original stream positions to retain comments and markers.

See [known limitations](https://fedonman.github.io/yamluna/guide/limitations/) for workarounds and [migration](https://fedonman.github.io/yamluna/migrating/) for differences from ruamel.yaml.
