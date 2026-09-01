//! The round-trip YAML document model, loader and emitter.
//!
//! A [`Document`] is owned and `'static`: the source text is kept beside the tree, never
//! borrowed by it, so a node can cross an FFI boundary and a subtree can move from one document
//! into another. [`parse`] turns a source string into one document per YAML document in the
//! stream. Every node carries the lexeme as written, the style, the position, and the comments
//! and blank lines in source order, which is everything [`emit`] needs to write the input back
//! byte for byte.
//!
//! Read [`Document`] and [`Node`] for the shape of the model, [`Trivia4`] for the four slots a
//! node hangs comments in, and [`EmitOptions`], [`analyze`] and [`choose_style`] for the
//! decisions the emitter makes about nodes you build yourself.
//!
//! # Examples
//!
//! ```
//! let docs = yamluna_core::parse("a: 1  # hi\n").unwrap();
//! let doc = &docs[0];
//! let root = doc.node(doc.root.unwrap());
//! let yamluna_core::NodeKind::Mapping { entries } = &root.kind else { unreachable!() };
//! let value = doc.node(entries[0].value);
//! assert_eq!(value.raw.as_deref(), Some("1"));
//! assert_eq!(value.trivia.eol.as_ref().and_then(yamluna_core::Trivia::text), Some("# hi"));
//! ```

#![warn(missing_docs)]

mod charmap;
mod emitter;
mod loader;
mod node;
mod trivia;

pub use charmap::CharMap;
pub use emitter::{
    EmitError, EmitOptions, LineBreak, ScalarAnalysis, ScalarContext, analyze, choose_style, emit,
};
pub use loader::{ErrorKind, ParseError, parse};
pub use node::{
    Document, DuplicateKey, Entry, Node, NodeId, NodeKind, NodeTag, Position, ScalarStyle, Style,
    TagDirective,
};
pub use trivia::{Trivia, Trivia4};
