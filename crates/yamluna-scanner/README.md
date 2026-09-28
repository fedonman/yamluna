# yamluna-scanner

The YAML 1.2 scanner and parser under [yamluna](https://github.com/fedonman/yamluna). It is a fork
of [`saphyr-parser`](https://crates.io/crates/saphyr-parser) 0.1.0 that keeps the parts of the
source a round trip needs and upstream discards: comments, whether a collection was written in
block or flow style, anchor names, and the `%YAML` version.

Like upstream, it turns a stream of characters into a stream of events with spans. It does not
build a tree; [`yamluna-core`](https://crates.io/crates/yamluna-core) does that. If you want to
load YAML into Rust values, use [`saphyr`](https://crates.io/crates/saphyr) instead.

```rust
use yamluna_scanner::{Event, Parser};

let mut comments = Vec::new();
for item in Parser::new_from_str("a: 1  # one\n# two\n").keep_comments(true) {
    let (event, _span) = item.unwrap();
    if let Event::Comment(text) = event {
        comments.push(text.into_owned());
    }
}
assert_eq!(comments, ["# one", "# two"]);
```

## Differences from saphyr-parser

- `Parser::keep_comments(true)` interleaves `Event::Comment` events with the rest of the stream
  in source order. It is off by default.
- `Event::SequenceStart` and `Event::MappingStart` carry a `StructureStyle`, `Block` or `Flow`.
- Anchors are an `AnchorRef` holding both the interned id and the name as written, in place of
  upstream's bare `usize`.
- `Parser::version()` returns the current document's `%YAML` version.
- `Input::skip_ws_to_eol` takes an extra parameter, so a custom `Input` implementation written
  for upstream needs a one-line change.

The fork also fixes five upstream bugs: `%TAG` directives that replaced one another, the
`Display` output of a resolved `Tag`, quoted-scalar spans that ran past the closing quote,
`Marker` documentation that described byte offsets and 1-based columns, and a tab after `:`
inside a flow mapping being rejected. Each change is recorded, with its tests, in
[FORK.md](https://github.com/fedonman/yamluna/blob/main/crates/yamluna-scanner/FORK.md).

The crate passes the [YAML test suite](https://github.com/yaml/yaml-test-suite), which is its
regression check against every patch. It does not act on tags, so parsing a document never
constructs anything from it.

## Licence

This crate carries two sets of licences, from the two upstream authors: Chen Yuheng, who wrote
`yaml-rust`, and Ethiraric, who continued it as `saphyr-parser`. Each is under either the MIT
licence or the Apache License, Version 2.0, at your option, and a redistribution must include
both sets. The
[LICENSE](https://github.com/fedonman/yamluna/blob/main/crates/yamluna-scanner/LICENSE) file says
which code falls under which, and the licence texts are in
[`.licenses`](https://github.com/fedonman/yamluna/tree/main/crates/yamluna-scanner/.licenses).
yamluna's own changes are under MIT or Apache-2.0, at your option.
