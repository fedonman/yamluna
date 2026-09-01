//! Scalar analysis and scalar writing.
//!
//! Two jobs live here, kept apart:
//!
//! * [`analyze`] and [`choose_style`] answer which styles a value may be written in. They are
//!   consulted only for a node with no `raw`, meaning one the user constructed or modified. A
//!   node loaded and left alone is re-emitted from its `raw` lexeme and never reaches this
//!   module, which is what makes an untouched round trip byte-exact.
//! * [`write`] renders a value in a style. It enforces only what is syntactically impossible,
//!   not what would resolve as another type: a caller that knows the intended tag (an `int`
//!   node whose text is `42`) writes it plain by calling [`write`] directly. [`analyze`] is the
//!   one that refuses `42`, because for a value known only as a string a plain `42` would come
//!   back as an integer.
//!
//! ## Two deliberate refusals
//!
//! * No multi-line plain or single-quoted scalars. Both styles can span lines, but only by
//!   encoding a line break as a blank line and losing every space adjacent to a fold. The gain
//!   is cosmetic and the failure mode is a silently different value, so a value containing a
//!   line break is written as a block scalar or double-quoted.
//! * No block indentation indicator. `|2` means "the parent node's indentation level plus 2",
//!   and a [`ScalarContext`] carries the absolute column its content must reach rather than
//!   the increment, so the digit cannot be computed here, and a wrong digit produces a document
//!   that re-reads as empty. A value whose first non-empty line starts with white space
//!   therefore cannot be written as a block scalar and falls back to double-quoted, which is
//!   lossless.

use crate::ScalarStyle;

/// Everything the emitter can refuse to write.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum EmitError {
    /// The style cannot express this value at this point in the document.
    #[error("a {style:?} scalar cannot represent this value here: {reason}")]
    Scalar {
        /// The style that was asked for.
        style: ScalarStyle,
        /// Why it does not work.
        reason: &'static str,
    },
    /// Anything else the emitter refuses to write.
    #[error("{0}")]
    Other(String),
}

/// Where a scalar is being written, which decides what styles are legal.
///
/// The default folds at column 80, writes `\n` for a line break, and leaves non-ASCII
/// characters unescaped.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ScalarContext {
    /// Column the scalar's continuation lines (and block-scalar content) must reach.
    pub indent: usize,
    /// Inside `[]` or `{}`, where `,`, `]` and `}` become hostile.
    pub in_flow: bool,
    /// An implicit key: no line breaks, and `: ` is hostile.
    pub is_key: bool,
    /// Fold target column; `0` disables folding.
    pub width: usize,
    /// The line break to write.
    pub line_break: &'static str,
    /// When `false`, non-ASCII characters are escaped in double-quoted output.
    pub allow_unicode: bool,
}

impl Default for ScalarContext {
    fn default() -> Self {
        Self {
            indent: 0,
            in_flow: false,
            is_key: false,
            width: 80,
            line_break: "\n",
            allow_unicode: true,
        }
    }
}

/// What styles are legal for a value, and the shape of the value that decides it.
// Seven independent facts about one string; grouping them into sub-structs would buy nothing.
#[allow(clippy::struct_excessive_bools)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ScalarAnalysis {
    /// A plain scalar is legal here, and the text would not resolve as a bool, int, float, date
    /// or null.
    ///
    /// False for `42` and for `true`: a string carrying that text has to be quoted to come back
    /// as a string. A node that carries an integer tag is a different case, and the emitter
    /// writes it plainly without consulting this field.
    pub allow_plain: bool,
    /// A single-quoted scalar is legal.
    pub allow_single_quoted: bool,
    /// A literal (`|`) block scalar is legal.
    pub allow_literal: bool,
    /// A folded (`>`) block scalar is legal.
    pub allow_folded: bool,
    /// The value contains a line break.
    pub multiline: bool,
    /// The value starts with a space or a tab.
    pub leading_space: bool,
    /// The value ends with a space or a tab.
    pub trailing_space: bool,
}

// ---------------------------------------------------------------------------------------------
// character classes
// ---------------------------------------------------------------------------------------------

/// The indicator characters a plain scalar may not start with.
const INDICATORS: &[char] = &[
    '-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%', '@', '`',
];

/// Whether `c` is `c-printable`, the set of characters a document may hold as written in
/// section 5.1 of the YAML 1.2 spec.
///
/// `DEL` and the C1 controls are not printable.
fn printable(c: char) -> bool {
    matches!(c,
        '\t' | '\n' | '\r'
        | '\u{20}'..='\u{7e}'
        | '\u{85}'
        | '\u{a0}'..='\u{d7ff}'
        | '\u{e000}'..='\u{fffd}'
        | '\u{10000}'..='\u{10ffff}')
}

/// Whether `c` is a character that a style with no escape mechanism can never carry: not
/// printable, a line break in some YAML version, or a byte-order mark.
fn needs_escape(c: char) -> bool {
    !printable(c) || matches!(c, '\r' | '\u{85}' | '\u{2028}' | '\u{2029}' | '\u{feff}')
}

// ---------------------------------------------------------------------------------------------
// type resolution
// ---------------------------------------------------------------------------------------------

/// Whether a plain scalar with this text would come back as something other than a string.
fn resolves_as_non_string(v: &str) -> bool {
    // The union of the YAML 1.1 and 1.2 core resolvers, on purpose: over-quoting is invisible,
    // under-quoting turns a string into a bool.
    if v.is_empty() {
        return true;
    }
    if matches!(
        v,
        "~" | "null"
            | "Null"
            | "NULL"
            | "true"
            | "True"
            | "TRUE"
            | "false"
            | "False"
            | "FALSE"
            | "yes"
            | "Yes"
            | "YES"
            | "no"
            | "No"
            | "NO"
            | "on"
            | "On"
            | "ON"
            | "off"
            | "Off"
            | "OFF"
            | "<<"
            | "="
    ) {
        return true;
    }
    is_number(v) || is_timestamp(v)
}

/// Whether `s` is `[0-9_]*` with at least one digit, an optional `.` fraction and an optional
/// exponent.
fn is_decimal_or_float(s: &str) -> bool {
    let (mantissa, exponent) = match s.find(['e', 'E']) {
        Some(i) => (&s[..i], Some(&s[i + 1..])),
        None => (s, None),
    };
    if let Some(e) = exponent {
        let e = e.strip_prefix(['+', '-']).unwrap_or(e);
        if e.is_empty() || !e.bytes().all(|b| b.is_ascii_digit()) {
            return false;
        }
    }
    if mantissa.starts_with('_') {
        return false;
    }
    let mut seen_dot = false;
    let mut seen_digit = false;
    for c in mantissa.chars() {
        match c {
            '0'..='9' => seen_digit = true,
            '_' => {}
            '.' if !seen_dot => seen_dot = true,
            _ => return false,
        }
    }
    seen_digit
}

/// Whether `v` is an int or a float in any spelling the 1.1 and 1.2 resolvers accept,
/// sexagesimals included.
fn is_number(v: &str) -> bool {
    if matches!(v, ".nan" | ".NaN" | ".NAN") {
        return true;
    }
    let body = v.strip_prefix(['+', '-']).unwrap_or(v);
    if matches!(body, ".inf" | ".Inf" | ".INF") {
        return true;
    }
    if body.is_empty() {
        return false;
    }
    for (prefix, radix) in [("0b", 2u32), ("0o", 8), ("0x", 16)] {
        if let Some(digits) = body.strip_prefix(prefix) {
            return digits.chars().any(|c| c.is_digit(radix))
                && digits.chars().all(|c| c == '_' || c.is_digit(radix));
        }
    }
    if body.contains(':') {
        // sexagesimal: `1:30`, `1:30:00.5`
        return body.split(':').all(|p| {
            !p.is_empty()
                && p.bytes()
                    .all(|b| b.is_ascii_digit() || b == b'_' || b == b'.')
        });
    }
    is_decimal_or_float(body)
}

/// Whether `v` satisfies the 1.1 timestamp resolver, loosely: `yyyy-` followed by nothing but
/// digits and date or time punctuation.
fn is_timestamp(v: &str) -> bool {
    let b = v.as_bytes();
    b.len() >= 8
        && b[..4].iter().all(u8::is_ascii_digit)
        && b[4] == b'-'
        && b[5].is_ascii_digit()
        && v.chars().all(|c| {
            c.is_ascii_digit()
                || matches!(
                    c,
                    '-' | ':' | '.' | '+' | 'T' | 't' | 'Z' | 'z' | ' ' | '\t'
                )
        })
}

// ---------------------------------------------------------------------------------------------
// legality
// ---------------------------------------------------------------------------------------------

/// Returns the reason a plain scalar would not parse back as this value here, ignoring type
/// resolution, and `None` when one would.
fn plain_syntax_reason(v: &str, ctx: &ScalarContext) -> Option<&'static str> {
    if v.is_empty() {
        return Some("a plain scalar cannot be empty");
    }
    if v.chars()
        .any(|c| needs_escape(c) || matches!(c, '\n' | '\t'))
    {
        return Some("a plain scalar carries no escapes and is kept to one line");
    }
    // `-`, `?` and `:` only end the scalar when white space follows, per the `ns-plain-first`
    // production in section 7.3.3 of YAML 1.2: `-42` and `-not-a-sequence` are plain scalars,
    // while `- x` is a sequence entry.
    if v.starts_with(INDICATORS)
        && (!v.starts_with(['-', '?', ':'])
            || v.chars().nth(1).is_none_or(|c| c == ' ' || c == '\t'))
    {
        return Some("a plain scalar cannot start with an indicator");
    }
    if v.starts_with(' ') || v.ends_with(' ') {
        return Some("a plain scalar cannot start or end with a space");
    }
    if v.starts_with("---") || v.starts_with("...") {
        return Some("a plain scalar cannot start with a document marker");
    }
    if v.contains(": ") || v.ends_with(':') {
        return Some("`: ` ends a plain scalar");
    }
    if v.contains(" #") {
        return Some("` #` starts a comment");
    }
    if ctx.in_flow && v.contains([',', '[', ']', '{', '}']) {
        return Some("a flow indicator ends a plain scalar in flow context");
    }
    None
}

/// Returns the reason a single-quoted scalar could not carry this value, and `None` when it
/// can.
fn single_syntax_reason(v: &str) -> Option<&'static str> {
    if v.chars().any(|c| needs_escape(c) || c == '\n') {
        return Some("a single-quoted scalar carries no escapes and is kept to one line");
    }
    None
}

/// Returns how many line breaks the value ends with. The chomping indicator is a function of
/// that count.
fn trailing_breaks(v: &str) -> usize {
    v.chars().rev().take_while(|c| *c == '\n').count()
}

/// Returns the reason a block scalar could not carry this value here, and `None` when it can.
fn block_syntax_reason(v: &str, ctx: &ScalarContext, folded: bool) -> Option<&'static str> {
    if ctx.in_flow {
        return Some("a block scalar is not legal in flow context");
    }
    if ctx.is_key {
        return Some("a block scalar cannot be an implicit key");
    }
    if ctx.indent == 0 {
        return Some("block scalar content must be indented");
    }
    if v.is_empty() {
        return Some("a block scalar cannot be empty");
    }
    if v.chars().any(needs_escape) {
        return Some("a block scalar carries no escapes");
    }
    if v.chars().all(|c| c == '\n') {
        return Some("a block scalar cannot hold nothing but line breaks");
    }
    if v.split('\n')
        .find(|l| !l.is_empty())
        .is_some_and(|l| l.starts_with([' ', '\t']))
    {
        return Some("an indentation indicator would be needed, and is not derivable here");
    }
    if folded {
        if v.starts_with('\n') {
            return Some("a folded scalar cannot start with a line break");
        }
        if v.split('\n').any(|l| l.starts_with([' ', '\t'])) {
            return Some("a folded scalar cannot fold a more-indented line");
        }
        if trailing_breaks(v) > 1 {
            return Some("a folded scalar cannot keep more than one trailing line break");
        }
    }
    None
}

/// Reports which styles are legal for `value` at this point, and the shape of the value behind
/// that answer.
///
/// `allow_plain` is the strict field: it is false for text that would read back as another
/// type. `analyze("42", ..).allow_plain` is false, because a string whose text is `42` has to
/// be quoted to survive a round trip. Emitting `42` plainly is still correct for a node that
/// means the integer, and the emitter takes that path from the node's own requested style
/// rather than from this analysis.
///
/// # Examples
///
/// ```
/// use yamluna_core::{ScalarContext, analyze};
///
/// let ctx = ScalarContext::default();
/// assert!(analyze("hello", &ctx).allow_plain);
/// assert!(!analyze("42", &ctx).allow_plain);
/// assert!(analyze("42", &ctx).allow_single_quoted);
/// ```
#[must_use]
pub fn analyze(value: &str, ctx: &ScalarContext) -> ScalarAnalysis {
    ScalarAnalysis {
        allow_plain: plain_syntax_reason(value, ctx).is_none() && !resolves_as_non_string(value),
        allow_single_quoted: single_syntax_reason(value).is_none(),
        allow_literal: block_syntax_reason(value, ctx, false).is_none(),
        allow_folded: block_syntax_reason(value, ctx, true).is_none(),
        multiline: value.contains(['\n', '\r']),
        leading_space: value.starts_with([' ', '\t']),
        trailing_space: value.ends_with([' ', '\t']),
    }
}

/// Whether a plain scalar can carry this value here, ignoring what it would resolve as.
///
/// [`analyze`] refuses `42` because, for a value that is known only as a string, a plain `42`
/// reads back as an integer. A node that explicitly asks for [`ScalarStyle::Plain`] has already
/// made that judgement, which is how a typed value (an `int` whose text is `42`) says so, so it
/// needs only the syntactic half.
pub(super) fn plain_writable(value: &str, ctx: &ScalarContext) -> bool {
    plain_syntax_reason(value, ctx).is_none()
}

/// Picks a style for a value that has no source lexeme, meaning one the user built or modified.
///
/// `requested` is honoured when it is legal here. Otherwise the fallback ladder runs plain,
/// then single-quoted, then double-quoted. It never reaches for a block style on its own,
/// because a block scalar changes the shape of the line; a block style is used only when asked
/// for.
///
/// A requested plain style is refused for text that would read back as another type, `42`
/// included. The emitter deals with a node that explicitly asks to be plain before it gets
/// here, so an integer node still emits as `42`.
///
/// # Arguments
///
/// * `value`: the text to be written.
/// * `requested`: the style the node asks for, if it asks for one.
/// * `ctx`: where the scalar lands, which decides what is legal there.
///
/// # Examples
///
/// ```
/// use yamluna_core::{ScalarContext, ScalarStyle, choose_style};
///
/// let ctx = ScalarContext::default();
/// assert_eq!(choose_style("hello", None, &ctx), ScalarStyle::Plain);
/// assert_eq!(choose_style("true", None, &ctx), ScalarStyle::SingleQuoted);
/// ```
#[must_use]
pub fn choose_style(
    value: &str,
    requested: Option<ScalarStyle>,
    ctx: &ScalarContext,
) -> ScalarStyle {
    let a = analyze(value, ctx);
    let legal = |style: ScalarStyle| match style {
        ScalarStyle::Plain => a.allow_plain,
        ScalarStyle::SingleQuoted => a.allow_single_quoted,
        ScalarStyle::DoubleQuoted => true,
        ScalarStyle::Literal => a.allow_literal,
        ScalarStyle::Folded => a.allow_folded,
    };
    if let Some(style) = requested.filter(|s| legal(*s)) {
        return style;
    }
    if a.allow_plain {
        ScalarStyle::Plain
    } else if a.allow_single_quoted {
        ScalarStyle::SingleQuoted
    } else {
        ScalarStyle::DoubleQuoted
    }
}

// ---------------------------------------------------------------------------------------------
// writing
// ---------------------------------------------------------------------------------------------

/// Whether a line break may replace `atoms[i]`, which the caller has checked is a lone space.
fn breakable(atoms: &[String], i: usize) -> bool {
    // The break folds back into exactly one space, so the space must be the only one there, and
    // a continuation line starting with `#` would be read as a comment.
    i > 0
        && atoms[i - 1] != " "
        && atoms
            .get(i + 1)
            .is_some_and(|n| n != " " && !n.starts_with('#'))
}

/// Writes indivisible output chunks, folding at spaces to keep lines under `ctx.width`.
fn write_atoms(atoms: &[String], ctx: &ScalarContext, out: &mut String) {
    // A continuation line at column 0 would be read as a document marker or a block indicator.
    // One extra column of indentation is always stripped back off by folding, so it is free.
    let continuation = " ".repeat(ctx.indent.max(1));
    let folding = ctx.width > 0 && !ctx.is_key;
    let mut col = ctx.indent;
    for (i, atom) in atoms.iter().enumerate() {
        if folding && col >= ctx.width && atom == " " && breakable(atoms, i) {
            out.push_str(ctx.line_break);
            out.push_str(&continuation);
            col = continuation.len();
            continue;
        }
        out.push_str(atom);
        col += atom.chars().count();
    }
}

/// Returns the double-quoted rendering of one character: the shortest unambiguous escape.
///
/// A character that needs no escape comes back as itself.
fn double_quoted_atom(c: char, allow_unicode: bool) -> String {
    let named = match c {
        '"' => Some("\\\""),
        '\\' => Some("\\\\"),
        '\0' => Some("\\0"),
        '\u{7}' => Some("\\a"),
        '\u{8}' => Some("\\b"),
        '\t' => Some("\\t"),
        '\n' => Some("\\n"),
        '\u{b}' => Some("\\v"),
        '\u{c}' => Some("\\f"),
        '\r' => Some("\\r"),
        '\u{1b}' => Some("\\e"),
        '\u{85}' => Some("\\N"),
        '\u{a0}' => Some("\\_"),
        '\u{2028}' => Some("\\L"),
        '\u{2029}' => Some("\\P"),
        _ => None,
    };
    if let Some(e) = named {
        return e.to_owned();
    }
    if c == ' ' || c.is_ascii_graphic() {
        return c.to_string();
    }
    if printable(c) && c != '\u{feff}' && allow_unicode {
        return c.to_string();
    }
    let u = c as u32;
    if u <= 0xff {
        format!("\\x{u:02x}")
    } else if u <= 0xffff {
        format!("\\u{u:04x}")
    } else {
        format!("\\U{u:08x}")
    }
}

/// Breaks one block-scalar line at spaces so that re-folding restores it exactly.
fn fold_at_spaces(text: &str, ctx: &ScalarContext) -> Vec<String> {
    if ctx.width == 0 {
        return vec![text.to_owned()];
    }
    let atoms: Vec<String> = text.chars().map(|c| c.to_string()).collect();
    let mut lines = Vec::new();
    let mut current = String::new();
    let mut col = ctx.indent;
    for (i, atom) in atoms.iter().enumerate() {
        if col >= ctx.width && atom == " " && breakable(&atoms, i) {
            lines.push(std::mem::take(&mut current));
            col = ctx.indent;
            continue;
        }
        current.push_str(atom);
        col += 1;
    }
    lines.push(current);
    lines
}

/// Returns the body lines of a folded (`>`) block that re-folds back to `body`.
fn folded_lines(body: &str, ctx: &ScalarContext) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut empties = 0usize;
    for segment in body.split('\n') {
        if segment.is_empty() {
            empties += 1;
            continue;
        }
        // A run of `j` line breaks in the value is written as `j` blank lines, which is
        // `j + 1` breaks in the block. A single break with no blank line would fold back into a
        // space instead.
        if !lines.is_empty() {
            for _ in 0..=empties {
                lines.push(String::new());
            }
        }
        lines.extend(fold_at_spaces(segment, ctx));
        empties = 0;
    }
    lines
}

/// Writes a literal or folded block scalar: header, chomping indicator, then the indented body.
///
/// The line break that terminates the last body line is not written, matching `Node::raw`.
fn write_block(
    value: &str,
    style: ScalarStyle,
    ctx: &ScalarContext,
    out: &mut String,
) -> Result<(), EmitError> {
    let folded = style == ScalarStyle::Folded;
    if let Some(reason) = block_syntax_reason(value, ctx, folded) {
        return Err(EmitError::Scalar { style, reason });
    }
    out.push(if folded { '>' } else { '|' });
    match trailing_breaks(value) {
        0 => out.push('-'),
        1 => {}
        // A `|+` block at the end of a stream is complete on its own, so no `...` document-end
        // marker is written here or by the caller.
        _ => out.push('+'),
    }
    // One trailing break belongs to the caller, exactly as for any other scalar.
    let body = value.strip_suffix('\n').unwrap_or(value);
    let lines = if folded {
        folded_lines(body, ctx)
    } else {
        body.split('\n').map(str::to_owned).collect()
    };
    let pad = " ".repeat(ctx.indent);
    for line in &lines {
        out.push_str(ctx.line_break);
        if !line.is_empty() {
            out.push_str(&pad);
            out.push_str(line);
        }
    }
    Ok(())
}

/// Writes `value` in `style` into `out`.
///
/// Only syntactic legality is enforced: `write(.., Plain, ..)` writes `42` happily, because a
/// caller emitting an integer node needs it to. [`analyze`] is where the type-resolution guard
/// lives, so a plain string never comes back as a number.
///
/// # Errors
///
/// Returns [`EmitError::Scalar`] when the style cannot express the value at this point.
pub fn write(
    value: &str,
    style: ScalarStyle,
    ctx: &ScalarContext,
    out: &mut String,
) -> Result<(), EmitError> {
    match style {
        ScalarStyle::Plain => {
            if let Some(reason) = plain_syntax_reason(value, ctx) {
                return Err(EmitError::Scalar { style, reason });
            }
            let atoms: Vec<String> = value.chars().map(|c| c.to_string()).collect();
            write_atoms(&atoms, ctx, out);
        }
        ScalarStyle::SingleQuoted => {
            if let Some(reason) = single_syntax_reason(value) {
                return Err(EmitError::Scalar { style, reason });
            }
            let atoms: Vec<String> = value
                .chars()
                .map(|c| {
                    if c == '\'' {
                        "''".to_owned()
                    } else {
                        c.to_string()
                    }
                })
                .collect();
            out.push('\'');
            write_atoms(&atoms, ctx, out);
            out.push('\'');
        }
        ScalarStyle::DoubleQuoted => {
            let atoms: Vec<String> = value
                .chars()
                .map(|c| double_quoted_atom(c, ctx.allow_unicode))
                .collect();
            out.push('"');
            write_atoms(&atoms, ctx, out);
            out.push('"');
        }
        ScalarStyle::Literal | ScalarStyle::Folded => write_block(value, style, ctx, out)?,
    }
    Ok(())
}

#[cfg(test)]
#[allow(clippy::too_many_lines)]
mod tests {
    use super::{
        EmitError, ScalarAnalysis, ScalarContext, ScalarStyle, analyze, choose_style,
        plain_syntax_reason, plain_writable, write,
    };
    use crate::{Document, NodeKind, parse};

    const ALL_STYLES: [ScalarStyle; 5] = [
        ScalarStyle::Plain,
        ScalarStyle::SingleQuoted,
        ScalarStyle::DoubleQuoted,
        ScalarStyle::Literal,
        ScalarStyle::Folded,
    ];

    /// The adversarial table. Every entry is a string a user could put in a document.
    const VALUES: &[&str] = &[
        // trivial and whitespace-only
        "",
        " ",
        "  ",
        "\t",
        "\n",
        "\n\n",
        "\n\n\n",
        "a",
        "hello world",
        // every core-schema lookalike
        "null",
        "Null",
        "NULL",
        "~",
        "true",
        "True",
        "TRUE",
        "false",
        "False",
        "FALSE",
        "yes",
        "Yes",
        "YES",
        "no",
        "No",
        "NO",
        "on",
        "On",
        "ON",
        "off",
        "Off",
        "OFF",
        "1",
        "-1",
        "+1",
        "0",
        "007",
        "1_000",
        "0x1F",
        "0o17",
        "0b1010",
        "1.0",
        "-1.0",
        ".5",
        "1.",
        "1e3",
        "1.5e-3",
        ".inf",
        "-.inf",
        ".nan",
        ".NaN",
        "2001-12-15",
        "2001-12-14t21:59:43.10-05:00",
        "12:30",
        "1:30:00",
        "<<",
        "=",
        // strings that only look like the above
        "1.2.3",
        "0x",
        "e5",
        "_1",
        "nullish",
        "Yes!",
        "2001-x",
        "http://example.com/x",
        // leading and trailing space
        " lead",
        "trail ",
        "  both  ",
        "a  b",
        "a\tb",
        "\tlead",
        "trail\t",
        // embedded newlines
        "a\nb",
        "a\nb\n",
        "a\n\nb\n",
        "line\n",
        "line\n\n",
        "line\n\n\n",
        "\nlead\n",
        "a\r\nb",
        "a\rb",
        "only\nnewlines",
        "  indented\ntext\n",
        "text\n  indented\n",
        "text\n\ttabbed\n",
        // starting with each indicator
        "-x",
        "?x",
        ":x",
        ",x",
        "[x",
        "]x",
        "{x",
        "}x",
        "#x",
        "&x",
        "*x",
        "!x",
        "|x",
        ">x",
        "'x",
        "\"x",
        "%x",
        "@x",
        "`x",
        // hostile interiors
        "- x",
        "a: b",
        "a #b",
        "a# b",
        "a:b",
        "x:",
        "::",
        "a, b",
        "a[b]",
        "a{b}",
        "it's",
        "it''s",
        "say \"hi\"",
        "back\\slash",
        "a#b",
        // document markers
        "---",
        "...",
        "--- x",
        "...x",
        "a---b",
        // unicode, control characters, emoji
        "héllo",
        "日本語",
        "🎉 party",
        "a\u{0}b",
        "a\u{7f}b",
        "a\u{85}b",
        "a\u{a0}b",
        "a\u{2028}b",
        "a\u{2029}b",
        "\u{feff}",
        "\u{1b}[0m",
        "a\u{9f}b",
        "\u{fffd}",
        // long lines, to exercise folding
        "the quick brown fox jumps over the lazy dog and keeps running for a very long time indeed",
        "averyverylongsinglewordwithnospacesatallthatcannotbefoldedanywherewhatsoeverokay",
        "one  two   three    four     five      six       seven        eight",
    ];

    // -- extraction helpers ---------------------------------------------------------------------

    fn map_entry(doc: &Document, id: u32, key: bool) -> Option<String> {
        let NodeKind::Mapping { entries } = &doc.node(id).kind else {
            return None;
        };
        let e = entries.first()?;
        doc.node(if key { e.key } else { e.value }).value.clone()
    }

    fn root_map_value(doc: &Document) -> Option<String> {
        map_entry(doc, doc.root?, false)
    }

    fn root_map_key(doc: &Document) -> Option<String> {
        map_entry(doc, doc.root?, true)
    }

    fn nested_map_value(doc: &Document) -> Option<String> {
        let NodeKind::Mapping { entries } = &doc.node(doc.root?).kind else {
            return None;
        };
        map_entry(doc, entries.first()?.value, false)
    }

    fn root_seq_item(doc: &Document) -> Option<String> {
        let NodeKind::Sequence { items } = &doc.node(doc.root?).kind else {
            return None;
        };
        doc.node(*items.first()?).value.clone()
    }

    struct Case {
        name: &'static str,
        ctx: ScalarContext,
        wrap: fn(&str) -> String,
        pick: fn(&Document) -> Option<String>,
    }

    fn cases(width: usize) -> Vec<Case> {
        let base = ScalarContext {
            width,
            ..ScalarContext::default()
        };
        vec![
            Case {
                name: "block map value",
                ctx: ScalarContext { indent: 2, ..base },
                wrap: |s| format!("k: {s}\n"),
                pick: root_map_value,
            },
            Case {
                name: "block map key",
                ctx: ScalarContext {
                    indent: 0,
                    is_key: true,
                    ..base
                },
                wrap: |s| format!("{s}: v\n"),
                pick: root_map_key,
            },
            Case {
                name: "block seq item",
                ctx: ScalarContext { indent: 2, ..base },
                wrap: |s| format!("- {s}\n"),
                pick: root_seq_item,
            },
            Case {
                name: "nested block map value",
                ctx: ScalarContext { indent: 4, ..base },
                wrap: |s| format!("m:\n  k: {s}\n"),
                pick: nested_map_value,
            },
            Case {
                name: "flow seq item",
                ctx: ScalarContext {
                    indent: 2,
                    in_flow: true,
                    ..base
                },
                wrap: |s| format!("[{s}]\n"),
                pick: root_seq_item,
            },
            Case {
                name: "flow map key",
                ctx: ScalarContext {
                    indent: 2,
                    in_flow: true,
                    is_key: true,
                    ..base
                },
                wrap: |s| format!("{{{s}: v}}\n"),
                pick: root_map_key,
            },
        ]
    }

    fn allowed(a: ScalarAnalysis, style: ScalarStyle) -> bool {
        match style {
            ScalarStyle::Plain => a.allow_plain,
            ScalarStyle::SingleQuoted => a.allow_single_quoted,
            ScalarStyle::DoubleQuoted => true,
            ScalarStyle::Literal => a.allow_literal,
            ScalarStyle::Folded => a.allow_folded,
        }
    }

    /// Writes `value` in `style`, drops it into `case`, re-reads it and checks nothing changed.
    fn check(value: &str, style: ScalarStyle, case: &Case) {
        let mut scalar = String::new();
        write(value, style, &case.ctx, &mut scalar).unwrap_or_else(|e| {
            panic!(
                "{}: write({value:?}, {style:?}) refused an allowed style: {e}",
                case.name
            )
        });
        let source = (case.wrap)(&scalar);
        let docs = parse(&source).unwrap_or_else(|e| {
            panic!(
                "{}: {value:?} as {style:?} produced unparseable YAML {source:?}: {e}",
                case.name
            )
        });
        let got = docs
            .first()
            .and_then(|d| (case.pick)(d))
            .unwrap_or_else(|| panic!("{}: {source:?} did not re-read as expected", case.name));
        assert_eq!(
            got, value,
            "{}: {value:?} as {style:?} round-tripped through {source:?} as {got:?}",
            case.name
        );
    }

    #[test]
    fn every_legal_style_round_trips_through_the_parser() {
        let mut checked = 0usize;
        for width in [0usize, 20, 80] {
            for case in cases(width) {
                for value in VALUES {
                    let a = analyze(value, &case.ctx);
                    for style in ALL_STYLES {
                        if allowed(a, style) {
                            check(value, style, &case);
                            checked += 1;
                        }
                    }
                }
            }
        }
        assert!(checked > 3000, "the property loop only ran {checked} times");
    }

    #[test]
    fn the_default_choice_round_trips_through_the_parser() {
        for width in [0usize, 20, 80] {
            for case in cases(width) {
                for value in VALUES {
                    for requested in [
                        None,
                        Some(ScalarStyle::Plain),
                        Some(ScalarStyle::SingleQuoted),
                        Some(ScalarStyle::DoubleQuoted),
                        Some(ScalarStyle::Literal),
                        Some(ScalarStyle::Folded),
                    ] {
                        check(value, choose_style(value, requested, &case.ctx), &case);
                    }
                }
            }
        }
    }

    #[test]
    fn ascii_only_output_when_unicode_is_not_allowed() {
        let ctx = ScalarContext {
            indent: 2,
            allow_unicode: false,
            ..ScalarContext::default()
        };
        for value in VALUES {
            let mut out = String::new();
            write(value, ScalarStyle::DoubleQuoted, &ctx, &mut out).unwrap();
            assert!(out.is_ascii(), "{value:?} left non-ASCII in {out:?}");
            let source = format!("k: {out}\n");
            let docs = parse(&source).unwrap();
            assert_eq!(root_map_value(&docs[0]).as_deref(), Some(*value));
        }
    }

    // -- type resolution ------------------------------------------------------------------------

    #[test]
    fn a_string_that_looks_like_another_type_is_never_plain() {
        let ctx = ScalarContext::default();
        for value in [
            "",
            "~",
            "null",
            "Null",
            "NULL",
            "true",
            "True",
            "TRUE",
            "false",
            "False",
            "FALSE",
            "yes",
            "Yes",
            "YES",
            "no",
            "No",
            "NO",
            "on",
            "On",
            "ON",
            "off",
            "Off",
            "OFF",
            "1",
            "-1",
            "+1",
            "0",
            "007",
            "1_000",
            "0x1F",
            "0o17",
            "0b1010",
            "1.0",
            ".5",
            "1.",
            "1e3",
            "1.5e-3",
            ".inf",
            "-.inf",
            "+.inf",
            ".nan",
            ".NaN",
            ".NAN",
            "2001-12-15",
            "2001-12-14t21:59:43.10-05:00",
            "12:30",
            "1:30:00",
            "<<",
            "=",
        ] {
            assert!(
                !analyze(value, &ctx).allow_plain,
                "{value:?} would come back as a non-string"
            );
        }
    }

    #[test]
    fn a_string_that_only_looks_like_another_type_stays_plain() {
        let ctx = ScalarContext::default();
        for value in [
            "1.2.3",
            "0x",
            "0b",
            "0X1f",
            "e5",
            "_1",
            "nullish",
            "Yes!",
            "2001-x",
            "y",
            "n",
            "http://example.com/x",
            "hello",
            "a1",
            "1a",
            "12:ab",
            "..",
            "1__0.0.0",
        ] {
            assert!(
                analyze(value, &ctx).allow_plain,
                "{value:?} should be plain"
            );
        }
    }

    // -- targeted expectations ------------------------------------------------------------------

    fn rendered(value: &str, style: ScalarStyle, ctx: &ScalarContext) -> String {
        let mut out = String::new();
        write(value, style, ctx, &mut out).unwrap();
        out
    }

    #[test]
    fn double_quoted_escapes_exactly_what_it_must() {
        let ctx = ScalarContext {
            width: 0,
            ..ScalarContext::default()
        };
        let dq = |v: &str| rendered(v, ScalarStyle::DoubleQuoted, &ctx);
        assert_eq!(dq("plain"), r#""plain""#);
        assert_eq!(dq("a\"b\\c"), r#""a\"b\\c""#);
        assert_eq!(
            dq("\0\u{7}\u{8}\t\n\u{b}\u{c}\r\u{1b}"),
            r#""\0\a\b\t\n\v\f\r\e""#
        );
        assert_eq!(dq("\u{85}\u{a0}\u{2028}\u{2029}"), r#""\N\_\L\P""#);
        assert_eq!(dq("\u{7f}"), r#""\x7f""#);
        assert_eq!(dq("\u{feff}"), r#""\ufeff""#);
        assert_eq!(dq("héllo 🎉"), "\"héllo 🎉\"");
        let ascii = ScalarContext {
            allow_unicode: false,
            ..ctx
        };
        assert_eq!(
            rendered("héllo 🎉", ScalarStyle::DoubleQuoted, &ascii),
            r#""h\xe9llo \U0001f389""#
        );
    }

    #[test]
    fn single_quoted_doubles_its_quotes() {
        let ctx = ScalarContext {
            width: 0,
            ..ScalarContext::default()
        };
        assert_eq!(rendered("it's", ScalarStyle::SingleQuoted, &ctx), "'it''s'");
        assert_eq!(
            rendered(" pad ", ScalarStyle::SingleQuoted, &ctx),
            "' pad '"
        );
    }

    #[test]
    fn chomping_follows_the_trailing_break_count() {
        let ctx = ScalarContext {
            indent: 2,
            width: 0,
            ..ScalarContext::default()
        };
        assert_eq!(rendered("x", ScalarStyle::Literal, &ctx), "|-\n  x");
        assert_eq!(rendered("x\n", ScalarStyle::Literal, &ctx), "|\n  x");
        assert_eq!(rendered("x\n\n", ScalarStyle::Literal, &ctx), "|+\n  x\n");
        assert_eq!(
            rendered("x\n\n\n", ScalarStyle::Literal, &ctx),
            "|+\n  x\n\n"
        );
        assert_eq!(rendered("a b", ScalarStyle::Folded, &ctx), ">-\n  a b");
        assert_eq!(
            rendered("a\nb\n", ScalarStyle::Folded, &ctx),
            ">\n  a\n\n  b"
        );
    }

    /// ruamel writes a spurious `...` after a `|+` block at the end of a stream. This one does
    /// not, and the value still re-reads unchanged.
    #[test]
    fn keep_chomping_does_not_gain_a_document_end_marker() {
        let ctx = ScalarContext {
            indent: 2,
            width: 0,
            ..ScalarContext::default()
        };
        let out = rendered("x\n\n", ScalarStyle::Literal, &ctx);
        assert!(!out.contains("..."), "{out:?}");
        let docs = parse(&format!("k: {out}\n")).unwrap();
        assert_eq!(root_map_value(&docs[0]).as_deref(), Some("x\n\n"));
    }

    #[test]
    fn block_styles_are_refused_where_they_cannot_work() {
        let ctx = ScalarContext {
            indent: 2,
            ..ScalarContext::default()
        };
        // A first content line that starts with a space needs an indentation indicator.
        assert!(!analyze("  x\ny\n", &ctx).allow_literal);
        assert!(matches!(
            write("  x\ny\n", ScalarStyle::Literal, &ctx, &mut String::new()),
            Err(EmitError::Scalar { .. })
        ));
        // A more-indented line cannot be folded.
        assert!(!analyze("a\n  b\n", &ctx).allow_folded);
        assert!(analyze("a\n  b\n", &ctx).allow_literal);
        // Nothing but line breaks, and the empty value, have no block spelling.
        assert!(!analyze("\n\n", &ctx).allow_literal);
        assert!(!analyze("", &ctx).allow_literal);
        // Flow context, key position and column 0 rule block scalars out entirely.
        for hostile in [
            ScalarContext {
                in_flow: true,
                ..ctx
            },
            ScalarContext {
                is_key: true,
                ..ctx
            },
            ScalarContext { indent: 0, ..ctx },
        ] {
            assert!(!analyze("x\n", &hostile).allow_literal);
            assert!(!analyze("x\n", &hostile).allow_folded);
        }
    }

    #[test]
    fn choose_style_falls_back_when_the_request_is_illegal() {
        let ctx = ScalarContext {
            indent: 2,
            ..ScalarContext::default()
        };
        assert_eq!(
            choose_style("a: b", Some(ScalarStyle::Plain), &ctx),
            ScalarStyle::SingleQuoted
        );
        assert_eq!(
            choose_style("a\nb", Some(ScalarStyle::SingleQuoted), &ctx),
            ScalarStyle::DoubleQuoted
        );
        assert_eq!(
            choose_style("x\n", Some(ScalarStyle::Literal), &ctx),
            ScalarStyle::Literal
        );
        assert_eq!(choose_style("plain", None, &ctx), ScalarStyle::Plain);
        assert_eq!(choose_style("true", None, &ctx), ScalarStyle::SingleQuoted);
        // A block style is never reached for on its own.
        assert_eq!(
            choose_style("a\nb\n", None, &ctx),
            ScalarStyle::DoubleQuoted
        );
    }

    #[test]
    fn write_is_syntactic_so_a_typed_node_can_force_a_plain_scalar() {
        let ctx = ScalarContext::default();
        assert!(!analyze("42", &ctx).allow_plain);
        assert_eq!(rendered("42", ScalarStyle::Plain, &ctx), "42");
        assert_eq!(rendered("true", ScalarStyle::Plain, &ctx), "true");
    }

    #[test]
    fn folding_never_changes_the_value() {
        let ctx = ScalarContext {
            indent: 2,
            width: 20,
            ..ScalarContext::default()
        };
        let long = "the quick brown fox jumps over the lazy dog";
        for style in [
            ScalarStyle::Plain,
            ScalarStyle::SingleQuoted,
            ScalarStyle::DoubleQuoted,
        ] {
            let out = rendered(long, style, &ctx);
            assert!(out.contains('\n'), "{style:?} did not fold: {out:?}");
            let docs = parse(&format!("k: {out}\n")).unwrap();
            assert_eq!(root_map_value(&docs[0]).as_deref(), Some(long));
        }
        // A run of spaces is never a fold point: folding there would lose spaces.
        let runs = "aaaaaaaaaaaaaaaaaaaaaaaaa  bbbbbbbbbbbbbbbbbbbbbbbbb  ccccc";
        let out = rendered(runs, ScalarStyle::Plain, &ctx);
        assert!(!out.contains('\n'), "{out:?}");
    }

    /// `-`, `?` and `:` only close a plain scalar when white space follows, so `-42` and
    /// `-not-a-sequence` are plain. Quoting them would differ from what the source wrote.
    #[test]
    fn a_leading_dash_only_ends_a_plain_scalar_before_white_space() {
        let ctx = ScalarContext::default();
        for plain in ["-42", "-not-a-sequence", "?x", ":x", "-"] {
            assert_eq!(
                plain_syntax_reason(plain, &ctx).is_none(),
                plain != "-",
                "{plain}"
            );
        }
        for quoted in ["- x", "? x", ": x", "#c", ",x"] {
            assert!(plain_syntax_reason(quoted, &ctx).is_some(), "{quoted}");
        }
        // ... and `-42` still resolves as a number, so a *string* with that text is quoted.
        assert!(!analyze("-42", &ctx).allow_plain);
        assert!(analyze("-not-a-sequence", &ctx).allow_plain);
        assert!(plain_writable("-42", &ctx));
    }

    #[test]
    fn analysis_reports_the_shape_of_the_value() {
        let ctx = ScalarContext::default();
        let a = analyze(" x\ny ", &ctx);
        assert!(a.multiline && a.leading_space && a.trailing_space);
        let b = analyze("x", &ctx);
        assert!(!b.multiline && !b.leading_space && !b.trailing_space);
    }
}
