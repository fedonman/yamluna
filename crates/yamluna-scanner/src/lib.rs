// Copyright 2015, Yuheng Chen.
// Copyright 2023, Ethiraric.
// See the LICENSE file at the top-level directory of this distribution.

//! YAML 1.2 scanner and parser for yamluna.
//!
//! This is a fork of [`saphyr-parser`](https://crates.io/crates/saphyr-parser) 0.1.0 that keeps
//! what a round trip needs and upstream discards: comments ([`Parser::keep_comments`]), whether a
//! collection was written in block or flow style ([`StructureStyle`]), anchor names
//! ([`AnchorRef`]), and the `%YAML` version ([`Parser::version`]). Like upstream, it turns a
//! stream of characters into a stream of [`Event`]s with [`Span`]s and does not build a tree;
//! [`yamluna-core`](https://crates.io/crates/yamluna-core) does that. To load YAML into Rust
//! values, use [`saphyr`](https://crates.io/crates/saphyr) instead.
//!
//! Every change from upstream is recorded in
//! [FORK.md](https://github.com/fedonman/yamluna/blob/main/crates/yamluna-scanner/FORK.md).
//!
//! # Usage
//!
//! ```sh
//! cargo add yamluna-scanner
//! ```
//!
//! # Features
//!
//! #### `debug_prints`
//! Enables the `debug` module and usage of debug prints in the scanner and the parser. Do not
//! enable if you are consuming the crate rather than working on it as this can significantly
//! decrease performance.
//!
//! This feature is _not_ `no_std` compatible.

#![warn(missing_docs, clippy::pedantic)]
#![no_std]

#[macro_use]
extern crate alloc;

#[cfg(feature = "debug_prints")]
extern crate std;

mod char_traits;
#[macro_use]
mod debug;
pub mod input;
mod parser;
mod scanner;

pub use crate::input::{BufferedInput, Input, str::StrInput};
pub use crate::parser::{
    AnchorRef, Event, EventReceiver, Parser, SpannedEventReceiver, StructureStyle, Tag,
};
pub use crate::scanner::{Marker, ScalarStyle, ScanError, Span};
