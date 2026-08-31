# yamluna

Round-trip YAML for Python. Loads a document, lets you change it, and writes it back with the
comments, blank lines, quoting, anchors and layout the author put there — with a Rust core doing
the scanning and emitting.

It replaces `ruamel.yaml`'s `typ='rt'`, and it fixes the bugs that mode has.

```python
import yamluna as yaml
```

## Status

Under construction. See [docs/DESIGN.md](docs/DESIGN.md) for the design contract,
[docs/DIVERGENCES.md](docs/DIVERGENCES.md) for every place yamluna deliberately behaves
differently from ruamel, and [tests/README.md](tests/README.md) for what the test layers assert.

## What it is not

`typ='rt'` only. No `safe`/`base`/`unsafe` modes, no `!!python/object:`, no component
substitution (`yaml.Parser = MyParser`), no plug-ins, no `scan()`/`compose()`/`serialize()`
pipeline, no legacy module-level `load()`/`dump()`. Those are deliberate omissions, not gaps.

## Licence

MIT OR Apache-2.0. `crates/yamluna-scanner` is a fork of
[saphyr-parser](https://github.com/saphyr-rs/saphyr) 0.0.12 under the same terms; see
[FORK.md](crates/yamluna-scanner/FORK.md) for every change made to it.
