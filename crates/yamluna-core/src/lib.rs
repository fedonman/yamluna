//! `yamluna-core` — the round-trip YAML document model, loader and emitter.
//!
//! The model (DESIGN §2) is owned and `'static`: the source text is kept beside the tree, never
//! borrowed by it. [`parse`] turns a source string into [`Document`]s whose nodes carry the raw
//! lexeme, the style, the position and the comments and blank lines in source order, which is
//! everything an emitter needs to reproduce the input byte for byte.
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
mod loader;
mod node;
mod trivia;

pub use charmap::CharMap;
pub use loader::{ErrorKind, ParseError, parse};
pub use node::{
    Document, DuplicateKey, Entry, Node, NodeId, NodeKind, NodeTag, Position, ScalarStyle, Style,
    TagDirective,
};
pub use trivia::{Trivia, Trivia4};
