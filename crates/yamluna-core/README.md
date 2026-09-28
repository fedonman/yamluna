# yamluna-core

The Rust core of [yamluna](https://github.com/fedonman/yamluna): the round-trip YAML document
model, the loader that builds it, and the emitter that writes it back.

`parse` turns a source string into one `Document` per YAML document in the stream. Every node
keeps the lexeme as written, its style, anchor, tag and position, and the comments and blank
lines around it in source order. `emit` writes that back, and a document you have not changed
comes out byte-identical to the input.

```rust
use yamluna_core::{EmitOptions, emit, parse};

let source = "# settings\nname: app   # the name\n\nports: [80, 443]\n";
let docs = parse(source).unwrap();
assert_eq!(emit(&docs, &EmitOptions::default()).unwrap(), source);
```

A `Document` owns its source text rather than borrowing it, so a node can cross an FFI boundary
and a subtree can move from one document into another. The parser underneath is
[`yamluna-scanner`](https://crates.io/crates/yamluna-scanner), a fork of `saphyr-parser` that
keeps the comments, collection styles and anchor names a round trip needs.

This crate is what the Python package is built on, and its API follows what that package needs.
If you want YAML in Python, install `yamluna` from [PyPI](https://pypi.org/project/yamluna/).
The Rust API is documented on [docs.rs](https://docs.rs/yamluna-core), and the Python package
on the [project site](https://fedonman.github.io/yamluna/).

## Licence

Licensed under either the [MIT licence](https://github.com/fedonman/yamluna/blob/main/LICENSE-MIT)
or the [Apache License, Version 2.0](https://github.com/fedonman/yamluna/blob/main/LICENSE-APACHE),
at your option.
