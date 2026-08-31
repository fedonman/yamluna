# yamluna-scanner

[yamluna-scanner](https://github.com/saphyr-rs/yamluna-scanner) is a fully compliant YAML 1.2
parser implementation written in pure Rust.

**If you want to load to a YAML Rust structure or manipulate YAML objects, use
`saphyr` instead of `yamluna-scanner`. This crate contains only the parser.**

This work is based on [`yaml-rust`](https://github.com/chyh1990/yaml-rust) with
fixes towards being compliant to the [YAML test
suite](https://github.com/yaml/yaml-test-suite/). `yaml-rust`'s parser is
heavily influenced by `libyaml` and `yaml-cpp`.

`yamluna-scanner` is a pure Rust YAML 1.2 implementation that benefits from the
memory safety and other benefits from the Rust language.

## Installing
Add the following to your Cargo.toml:

```toml
[dependencies]
yamluna-scanner = "0.0.3"
```
or use `cargo add` to get the latest version automatically:
```sh
cargo add yamluna-scanner
```

## TODO how-to

## Security

This library does not try to interpret any type specifiers in a YAML document,
so there is no risk of, say, instantiating a socket with fields and
communicating with the outside world just by parsing a YAML document.

## Specification Compliance

This implementation is fully compatible with the YAML 1.2 specification. In
order to help with compliance, `yamluna-scanner` tests against (and passes) the [YAML
test suite](https://github.com/yaml/yaml-test-suite/).

## License

Licensed under either of

 * Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0)
 * MIT license (http://opensource.org/licenses/MIT)

at your option.

Since this repository was originally maintained by
[chyh1990](https://github.com/chyh1990), there are 2 sets of licenses.
A license of each set must be included in redistributions. See the
[LICENSE](LICENSE) file for more details.

You can find licences in the [`.licenses`](.licenses) subfolder.

## Contribution

[Fork this repository](https://github.com/saphyr-rs/yamluna-scanner/fork) and
[Create a Pull Request on Github](https://github.com/saphyr-rs/yamluna-scanner/compare/master...saphyr-rs:yamluna-scanner:master).
You may need to click on "compare across forks" and select your fork's branch.

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in the work by you, as defined in the Apache-2.0 license, shall
be dual licensed as above, without any additional terms or conditions.

## Links

* [yamluna-scanner source code repository](https://github.com/saphyr-rs/yamluna-scanner)

* [yamluna-scanner releases on crates.io](https://crates.io/crates/yamluna-scanner)

* [yamluna-scanner documentation on docs.rs](https://docs.rs/yamluna-scanner/latest/yamluna-scanner/)

* [yaml-test-suite](https://github.com/yaml/yaml-test-suite)
