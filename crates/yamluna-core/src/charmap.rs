//! The char→byte table of DESIGN §1.5.
//!
//! [`Marker::index`](yamluna_scanner::Marker::index) is a **char** offset. Every place that slices
//! the source by a marker goes through [`CharMap`], which is built once per document in a single
//! `O(n)` pass.

/// A char-offset → byte-offset table for one source string.
///
/// `offsets[i]` is the byte offset of the `i`-th character; there is one extra entry at the end
/// holding `src.len()`, so a half-open char range always maps to a half-open byte range.
#[derive(Clone, Debug)]
pub struct CharMap {
    offsets: Vec<u32>,
}

impl CharMap {
    /// Build the table for `src`.
    ///
    /// # Panics
    /// Panics if `src` is larger than 4 GiB.
    // ponytail: `u32` entries cap a document at 4 GiB; widen to `usize` if that ever bites.
    #[must_use]
    pub fn new(src: &str) -> Self {
        let len = u32::try_from(src.len()).expect("yamluna-core: source larger than 4 GiB");
        let mut offsets: Vec<u32> = Vec::with_capacity(src.len() + 1);
        offsets.extend(
            src.char_indices()
                .map(|(b, _)| u32::try_from(b).expect("checked above")),
        );
        offsets.push(len);
        Self { offsets }
    }

    /// The number of characters in the source.
    #[must_use]
    pub fn len(&self) -> usize {
        self.offsets.len() - 1
    }

    /// Whether the source is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The byte offset of character `index`, clamped to the end of the source.
    ///
    /// Clamping rather than panicking is deliberate: the parser emits synthetic events whose
    /// markers sit one past the last character.
    #[must_use]
    pub fn byte(&self, index: usize) -> usize {
        self.offsets[index.min(self.offsets.len() - 1)] as usize
    }

    /// The source text for the half-open char range `start..end`.
    ///
    /// An inverted range yields `""` — the parser produces a few of those for synthetic tokens.
    #[must_use]
    pub fn slice<'a>(&self, src: &'a str, start: usize, end: usize) -> &'a str {
        let a = self.byte(start);
        let b = self.byte(end);
        if b <= a { "" } else { &src[a..b] }
    }
}

#[cfg(test)]
mod tests {
    use super::CharMap;

    #[test]
    fn slices_by_char_offsets_not_byte_offsets() {
        // 🌍 is 4 bytes, é is 2, and "e\u{301}" is two characters (1 + 2 bytes).
        let src = "a🌍é\u{301}e\u{301}z";
        let map = CharMap::new(src);
        assert_eq!(map.len(), src.chars().count());
        assert_eq!(map.slice(src, 0, 1), "a");
        assert_eq!(map.slice(src, 1, 2), "🌍");
        assert_eq!(map.slice(src, 2, 3), "é");
        assert_eq!(map.slice(src, 3, 4), "\u{301}");
        assert_eq!(map.slice(src, 4, 6), "e\u{301}");
        assert_eq!(map.slice(src, 0, map.len()), src);
    }

    #[test]
    fn every_char_index_maps_to_its_byte_index() {
        let src = "# á̂̃ 日本語 👩‍💻\nkey: value\n";
        let map = CharMap::new(src);
        for (i, (b, _)) in src.char_indices().enumerate() {
            assert_eq!(map.byte(i), b, "char {i}");
        }
        assert_eq!(map.byte(map.len()), src.len());
        // Out of range clamps instead of panicking.
        assert_eq!(map.byte(map.len() + 10), src.len());
        assert_eq!(map.slice(src, 5, 2), "");
    }

    #[test]
    fn empty_source() {
        let map = CharMap::new("");
        assert!(map.is_empty());
        assert_eq!(map.byte(0), 0);
        assert_eq!(map.slice("", 0, 0), "");
    }
}
