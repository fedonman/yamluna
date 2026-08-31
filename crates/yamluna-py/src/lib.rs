//! The `PyO3` boundary of DESIGN §3: `yamluna_core::Document` ⟷ the record classes.
//!
//! The record types are defined **once, in Python** (`python/yamluna/_record.py`). This crate
//! imports that module once, caches a [`Py<PyType>`] per class in a [`PyOnceLock`], builds
//! instances through the C API on load and reads their attributes back on dump. There is
//! deliberately no `#[pyclass]` here: a second definition of `Node` would be a second contract.
//!
//! Two rules the rest of the file exists to keep:
//!
//! * **The GIL is released around the core.** [`yamluna_core::parse`] and the emitter touch
//!   nothing Python, so [`Python::detach`] (`allow_threads` in older `PyO3`) around them is
//!   trivially safe, and it is what lets several threads load YAML at once.
//! * **Errors cross as data.** A [`ParseError`] carries an [`ErrorKind`]; that discriminant picks
//!   the exception class, via `yamluna.error.make_error`. Nothing on either side of the boundary
//!   ever string-matches a message.
//!
//! Positions are **char** offsets with 0-based line and column all the way to Python — a byte
//! offset in `Mark.pointer` slices mid-character on any document with an accent or an emoji.
//! [`yamluna_scanner::Marker::index`] is already a char offset, so this is a matter of not
//! "helpfully" converting it.

#![deny(missing_docs)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyList, PyTuple, PyType};
use pyo3::{IntoPyObjectExt, intern};

use yamluna_core::{
    Document, EmitError, EmitOptions, Entry, ErrorKind, LineBreak, Node, NodeId, NodeKind, NodeTag,
    ParseError, Position, ScalarStyle, Style, TagDirective, Trivia, Trivia4,
};

/// The module that owns the record classes. Imported once; see [`PyOnceLock::import`].
const RECORD: &str = "yamluna._record";

/// The module that owns the exception hierarchy and `make_error`.
const ERROR: &str = "yamluna.error";

// -- the cached record classes ------------------------------------------------------------

macro_rules! cached {
    ($name:ident, $module:expr, $attr:literal, $ty:ty) => {
        fn $name(py: Python<'_>) -> PyResult<&Bound<'_, $ty>> {
            static CELL: PyOnceLock<Py<$ty>> = PyOnceLock::new();
            CELL.import(py, $module, $attr)
        }
    };
}

cached!(node_class, RECORD, "Node", PyType);
cached!(trivia_class, RECORD, "Trivia", PyType);
cached!(doc_class, RECORD, "Doc", PyType);
cached!(make_error, ERROR, "make_error", PyAny);
cached!(emitter_error, ERROR, "EmitterError", PyType);

// -- the kind and style codes of `_record.py` ---------------------------------------------

const KIND_SCALAR: u8 = 0;
const KIND_SEQUENCE: u8 = 1;
const KIND_MAPPING: u8 = 2;
const KIND_ALIAS: u8 = 3;

/// `Style` → the `STYLE_*` constant. 0..=4 are [`ScalarStyle`] in declaration order, 5 and 6 the
/// collection styles; they cannot collide because a node is never both.
fn style_code(style: Style) -> u8 {
    match style {
        Style::Scalar(ScalarStyle::Plain) => 0,
        Style::Scalar(ScalarStyle::SingleQuoted) => 1,
        Style::Scalar(ScalarStyle::DoubleQuoted) => 2,
        Style::Scalar(ScalarStyle::Literal) => 3,
        Style::Scalar(ScalarStyle::Folded) => 4,
        Style::Block => 5,
        Style::Flow => 6,
    }
}

/// The inverse of [`style_code`], for a record built in Python.
fn style_from_code(code: u8) -> PyResult<Style> {
    Ok(match code {
        0 => Style::Scalar(ScalarStyle::Plain),
        1 => Style::Scalar(ScalarStyle::SingleQuoted),
        2 => Style::Scalar(ScalarStyle::DoubleQuoted),
        3 => Style::Scalar(ScalarStyle::Literal),
        4 => Style::Scalar(ScalarStyle::Folded),
        5 => Style::Block,
        6 => Style::Flow,
        other => return Err(PyValueError::new_err(format!("unknown Node.style {other}"))),
    })
}

// -- core -> record -----------------------------------------------------------------------

fn build_trivia<'py>(py: Python<'py>, t: &Trivia) -> PyResult<Bound<'py, PyAny>> {
    let cls = trivia_class(py)?;
    match t {
        Trivia::Comment {
            text,
            own_line,
            col,
        } => cls.call1((text, *own_line, *col, 0u32)),
        // A blank run has no text and no column; `_record.py` prints neither.
        Trivia::BlankLines(n) => cls.call1((py.None(), true, 0u32, *n)),
    }
}

fn build_trivia_list<'py>(py: Python<'py>, ts: &[Trivia]) -> PyResult<Bound<'py, PyList>> {
    PyList::new(
        py,
        ts.iter()
            .map(|t| build_trivia(py, t))
            .collect::<PyResult<Vec<_>>>()?,
    )
}

fn build_node<'py>(py: Python<'py>, n: &Node) -> PyResult<Bound<'py, PyAny>> {
    // `Node.anchor` is the anchor the node *defines*, except on an alias where it is the one the
    // node *references* (`NodeKind::Alias { anchor }`). A node never does both.
    let (kind, anchor) = match &n.kind {
        NodeKind::Scalar => (KIND_SCALAR, n.anchor.clone()),
        NodeKind::Sequence { .. } => (KIND_SEQUENCE, n.anchor.clone()),
        NodeKind::Mapping { .. } => (KIND_MAPPING, n.anchor.clone()),
        NodeKind::Alias { anchor } => (KIND_ALIAS, Some(anchor.clone())),
    };
    // `merge` and `explicit` hold positions in `children`, so an entry's key sits at twice its
    // entry index.
    let positions = |pick: fn(&Entry) -> bool| -> Vec<usize> {
        match &n.kind {
            NodeKind::Mapping { entries } => entries
                .iter()
                .enumerate()
                .filter(|(_, e)| pick(e))
                .map(|(i, _)| i * 2)
                .collect(),
            _ => Vec::new(),
        }
    };
    let merge = positions(|e| e.merge);
    let explicit = positions(|e| e.explicit);
    let tag = n
        .tag
        .as_ref()
        .map(|t| (t.handle.clone(), t.suffix.clone(), t.resolved.clone()));

    let args = [
        kind.into_bound_py_any(py)?,
        style_code(n.style).into_bound_py_any(py)?,
        anchor.into_bound_py_any(py)?,
        tag.into_bound_py_any(py)?,
        n.value.as_deref().into_bound_py_any(py)?,
        n.raw.as_deref().into_bound_py_any(py)?,
        n.pos.line.into_bound_py_any(py)?,
        n.pos.col.into_bound_py_any(py)?,
        n.children().into_bound_py_any(py)?,
        merge.into_bound_py_any(py)?,
        explicit.into_bound_py_any(py)?,
        build_trivia_list(py, &n.trivia.before)?.into_any(),
        match &n.trivia.eol {
            Some(t) => build_trivia(py, t)?,
            None => py.None().into_bound(py),
        },
        build_trivia_list(py, &n.trivia.inner)?.into_any(),
        build_trivia_list(py, &n.trivia.after)?.into_any(),
        n.tag_first.into_bound_py_any(py)?,
    ];
    node_class(py)?.call1(PyTuple::new(py, args)?)
}

fn build_doc<'py>(py: Python<'py>, d: &Document) -> PyResult<Bound<'py, PyAny>> {
    let nodes = PyList::new(
        py,
        d.nodes
            .iter()
            .map(|n| build_node(py, n))
            .collect::<PyResult<Vec<_>>>()?,
    )?;
    let directives: Vec<(&str, &str)> = d
        .tag_directives
        .iter()
        .map(|t| (t.handle.as_str(), t.prefix.as_str()))
        .collect();
    doc_class(py)?.call1((
        d.version,
        directives,
        d.explicit_start,
        d.explicit_end,
        d.root,
        nodes,
        build_trivia_list(py, &d.leading)?,
        build_trivia_list(py, &d.trailing)?,
        d.bom,
        d.final_line_break,
        d.tags_before_version,
    ))
}

// -- record -> core -----------------------------------------------------------------------

fn read_trivia(o: &Bound<'_, PyAny>) -> PyResult<Trivia> {
    let py = o.py();
    let blank: u32 = o.getattr(intern!(py, "blank_lines"))?.extract()?;
    if blank > 0 {
        return Ok(Trivia::BlankLines(blank));
    }
    Ok(Trivia::Comment {
        text: o
            .getattr(intern!(py, "text"))?
            .extract::<Option<String>>()?
            .unwrap_or_default(),
        own_line: o.getattr(intern!(py, "own_line"))?.extract()?,
        col: o.getattr(intern!(py, "col"))?.extract()?,
    })
}

fn read_trivia_list(o: &Bound<'_, PyAny>) -> PyResult<Vec<Trivia>> {
    o.try_iter()?.map(|t| read_trivia(&t?)).collect()
}

fn read_node(o: &Bound<'_, PyAny>) -> PyResult<Node> {
    let py = o.py();
    let kind: u8 = o.getattr(intern!(py, "kind"))?.extract()?;
    let anchor: Option<String> = o.getattr(intern!(py, "anchor"))?.extract()?;
    let children: Vec<NodeId> = o.getattr(intern!(py, "children"))?.extract()?;
    let merge: Vec<usize> = o.getattr(intern!(py, "merge"))?.extract()?;
    let explicit: Vec<usize> = o.getattr(intern!(py, "explicit"))?.extract()?;

    let kind = match kind {
        KIND_SCALAR => NodeKind::Scalar,
        KIND_SEQUENCE => NodeKind::Sequence { items: children },
        KIND_MAPPING => {
            if children.len() % 2 != 0 {
                return Err(PyValueError::new_err(
                    "a mapping Node needs an even number of children (k, v, k, v, ...)",
                ));
            }
            NodeKind::Mapping {
                entries: children
                    .chunks_exact(2)
                    .enumerate()
                    .map(|(i, kv)| Entry {
                        key: kv[0],
                        value: kv[1],
                        merge: merge.contains(&(i * 2)),
                        explicit: explicit.contains(&(i * 2)),
                    })
                    .collect(),
            }
        }
        KIND_ALIAS => NodeKind::Alias {
            anchor: anchor.clone().ok_or_else(|| {
                PyValueError::new_err("an alias Node needs the referenced name in `anchor`")
            })?,
        },
        other => return Err(PyValueError::new_err(format!("unknown Node.kind {other}"))),
    };

    let tag: Option<(String, String, String)> = o.getattr(intern!(py, "tag"))?.extract()?;
    let eol = o.getattr(intern!(py, "eol"))?;
    Ok(Node {
        anchor: if matches!(kind, NodeKind::Alias { .. }) {
            None
        } else {
            anchor
        },
        kind,
        tag: tag.map(|(handle, suffix, resolved)| NodeTag {
            handle,
            suffix,
            resolved,
        }),
        tag_first: o.getattr(intern!(py, "tag_first"))?.extract()?,
        style: style_from_code(o.getattr(intern!(py, "style"))?.extract()?)?,
        value: o.getattr(intern!(py, "value"))?.extract()?,
        raw: o.getattr(intern!(py, "raw"))?.extract()?,
        pos: Position {
            line: o.getattr(intern!(py, "line"))?.extract()?,
            col: o.getattr(intern!(py, "col"))?.extract()?,
        },
        // The flow collection's own punctuation — where its `,`s and its closing bracket went,
        // and which of its keys were written bare. The record contract does not carry these yet,
        // so a document that has been through Python gets the emitter's layout for them.
        flow_comma: None,
        flow_end: None,
        flow_bare_key: false,
        trivia: Trivia4 {
            before: read_trivia_list(&o.getattr(intern!(py, "before"))?)?,
            eol: if eol.is_none() {
                None
            } else {
                Some(read_trivia(&eol)?)
            },
            inner: read_trivia_list(&o.getattr(intern!(py, "inner"))?)?,
            after: read_trivia_list(&o.getattr(intern!(py, "after"))?)?,
        },
    })
}

fn read_doc(o: &Bound<'_, PyAny>) -> PyResult<Document> {
    let py = o.py();
    let nodes: Vec<Node> = o
        .getattr(intern!(py, "nodes"))?
        .try_iter()?
        .map(|n| read_node(&n?))
        .collect::<PyResult<_>>()?;

    // The arena is the emitter's only map of the tree; a dangling index would panic deep inside
    // it, with nothing left to say which record was wrong.
    let len = NodeId::try_from(nodes.len())
        .map_err(|_| PyValueError::new_err("a Doc cannot hold more than u32::MAX nodes"))?;
    let check = |id: NodeId, what: &str| -> PyResult<()> {
        if id >= len {
            return Err(PyValueError::new_err(format!(
                "{what} is node {id}, but the Doc has {len} nodes"
            )));
        }
        Ok(())
    };
    let root: Option<NodeId> = o.getattr(intern!(py, "root"))?.extract()?;
    if let Some(root) = root {
        check(root, "Doc.root")?;
    }
    for (i, n) in nodes.iter().enumerate() {
        for child in n.children() {
            check(child, &format!("a child of node {i}"))?;
        }
    }

    let directives: Vec<(String, String)> = o.getattr(intern!(py, "tag_directives"))?.extract()?;
    Ok(Document {
        version: o.getattr(intern!(py, "version"))?.extract()?,
        tag_directives: directives
            .into_iter()
            .map(|(handle, prefix)| TagDirective { handle, prefix })
            .collect(),
        tags_before_version: o.getattr(intern!(py, "tags_before_version"))?.extract()?,
        explicit_start: o.getattr(intern!(py, "explicit_start"))?.extract()?,
        explicit_end: o.getattr(intern!(py, "explicit_end"))?.extract()?,
        bom: o.getattr(intern!(py, "bom"))?.extract()?,
        final_line_break: o.getattr(intern!(py, "final_line_break"))?.extract()?,
        root,
        nodes,
        leading: read_trivia_list(&o.getattr(intern!(py, "leading"))?)?,
        trailing: read_trivia_list(&o.getattr(intern!(py, "trailing"))?)?,
        duplicate_keys: Vec::new(),
    })
}

/// Read an `EmitOptions` record into the core's options.
///
/// Three fields cannot be carried across as they stand, and each collapse is deliberate:
///
/// * `line_break` is a plain `str` on the record and `main.py` writes `self.line_break or '\n'`,
///   so "unset" and "LF" are the same value by the time they get here. `'\n'` therefore means
///   [`LineBreak::Auto`] — the emitter then takes the break from the lexemes, which is what keeps
///   a CRLF file byte-identical through a default `YAML()`.
///   ponytail: an `Optional[str]` slot on the record would make the two cases distinguishable.
/// * `explicit_start`, `explicit_end` and `default_flow_style` are `Option<bool>` in the core —
///   `None` keeps what each document had, `Some` overrides it — and plain `bool` on the record,
///   where `False` is what an unset `YAML.explicit_start` collapses to. `False` is therefore
///   "leave the documents alone", which is the only reading under which an unmodified round trip
///   survives.
/// * `canonical` has no counterpart in the core emitter and is ignored; `allow_unicode` has no
///   counterpart on the record and keeps its default.
fn read_opts(o: &Bound<'_, PyAny>) -> PyResult<EmitOptions> {
    let py = o.py();
    let force = |name: &str| -> PyResult<Option<bool>> {
        Ok(if o.getattr(name)?.extract::<bool>()? {
            Some(true)
        } else {
            None
        })
    };
    let line_break: String = o.getattr(intern!(py, "line_break"))?.extract()?;
    Ok(EmitOptions {
        map_indent: o.getattr(intern!(py, "map_indent"))?.extract()?,
        seq_indent: o.getattr(intern!(py, "seq_indent"))?.extract()?,
        seq_offset: o.getattr(intern!(py, "seq_offset"))?.extract()?,
        width: o.getattr(intern!(py, "width"))?.extract()?,
        line_break: match line_break.as_str() {
            "\n" => LineBreak::Auto,
            "\r\n" => LineBreak::CrLf,
            "\r" => LineBreak::Cr,
            other => {
                return Err(PyValueError::new_err(format!(
                    "EmitOptions.line_break must be one of '\\n', '\\r\\n', '\\r', not {other:?}"
                )));
            }
        },
        explicit_start: force("explicit_start")?,
        explicit_end: force("explicit_end")?,
        default_flow_style: force("default_flow_style")?,
        preserve_quotes: o.getattr(intern!(py, "preserve_quotes"))?.extract()?,
        ..EmitOptions::default()
    })
}

// -- errors -------------------------------------------------------------------------------

/// The [`ErrorKind`] discriminant as `make_error` spells it. This match is the *whole* of error
/// classification: adding a variant to `ErrorKind` breaks this build, which is the point.
fn kind_name(kind: ErrorKind) -> &'static str {
    match kind {
        ErrorKind::Scanner => "scanner",
    }
}

/// Turn an [`EmitError`] into `yamluna.error.EmitterError`.
///
/// It goes to the class directly rather than through `make_error`: an emit failure has no
/// position in any source text, and a `Mark` pointing at line 1 column 1 would be a lie. The
/// class is still chosen by the Rust type, never by matching the message.
fn emit_error(py: Python<'_>, e: &EmitError) -> PyErr {
    let built = emitter_error(py)
        .and_then(|cls| cls.call1((py.None(), py.None(), e.to_string(), py.None())));
    match built {
        Ok(exc) => PyErr::from_value(exc),
        Err(err) => err,
    }
}

/// Turn a core [`ParseError`] into the exception `yamluna.error` says it is.
///
/// `source` must be the text the core actually scanned (BOM stripped), because `index` is a char
/// offset into *that*, and `Mark` slices `source` by it to build the snippet.
fn parse_error(py: Python<'_>, e: &ParseError, source: &str, name: &str) -> PyErr {
    let built = make_error(py).and_then(|f| {
        f.call1((
            kind_name(e.kind),
            &e.message,
            e.line,
            e.col,
            e.index,
            source,
            name,
        ))
    });
    match built {
        Ok(exc) => PyErr::from_value(exc),
        // `yamluna.error` failed to import or to build: report that, not a swallowed error.
        Err(err) => err,
    }
}

// -- the module -----------------------------------------------------------------------------

/// Load every document of `source` into a list of `Doc` records.
///
/// `allow_duplicate_keys` is accepted for signature compatibility and deliberately not acted on
/// here: `yamluna.constructor` reports duplicates, because it compares *constructed* keys and
/// owns the ruamel-shaped message. A second detector in Rust, keyed on the canonical rendering of
/// the key, would disagree with it on exactly the interesting cases.
#[pyfunction]
#[pyo3(signature = (source, *, allow_duplicate_keys = true, name = "<unicode string>"))]
fn parse<'py>(
    py: Python<'py>,
    source: &str,
    allow_duplicate_keys: bool,
    name: &str,
) -> PyResult<Bound<'py, PyList>> {
    let _ = allow_duplicate_keys;
    // The core reports positions relative to the text it scanned, which is BOM-stripped.
    let scanned = strip_bom(source);
    let docs = py
        .detach(|| load(scanned, source))
        .map_err(|e| parse_error(py, &e, scanned, name))?;
    PyList::new(
        py,
        docs.iter()
            .map(|d| build_doc(py, d))
            .collect::<PyResult<Vec<_>>>()?,
    )
}

/// Emit a list of `Doc` records as one YAML stream.
#[pyfunction]
fn emit(py: Python<'_>, docs: &Bound<'_, PyAny>, opts: &Bound<'_, PyAny>) -> PyResult<String> {
    let docs: Vec<Document> = docs
        .try_iter()?
        .map(|d| read_doc(&d?))
        .collect::<PyResult<_>>()?;
    let opts = read_opts(opts)?;
    py.detach(|| yamluna_core::emit(&docs, &opts))
        .map_err(|e| emit_error(py, &e))
}

/// `parse` then `emit`, without ever building a record: the reference the record path is
/// measured against.
///
/// It exists so a round-trip failure is attributable. When `emit(parse(text))` differs from the
/// source, this says whether the difference survives a trip through Python — in which case it is
/// a boundary bug, something the record model cannot carry — or not, in which case the emitter
/// and the boundary agree and the defect is upstream of both.
#[pyfunction]
fn _roundtrip_in_rust(py: Python<'_>, source: &str, opts: &Bound<'_, PyAny>) -> PyResult<String> {
    let opts = read_opts(opts)?;
    let scanned = strip_bom(source);
    py.detach(|| {
        load(scanned, source)
            .map_err(|e| RoundTrip::Parse(Box::new(e)))
            .and_then(|docs| yamluna_core::emit(&docs, &opts).map_err(RoundTrip::Emit))
    })
    .map_err(|e| match e {
        RoundTrip::Parse(e) => parse_error(py, &e, scanned, "<unicode string>"),
        RoundTrip::Emit(e) => emit_error(py, &e),
    })
}

/// The source with a leading byte-order mark taken off.
///
/// The core strips one of its own and records it on the first document, but it never gets to:
/// stripping here is what keeps [`ParseError::index`] a char offset into the very text `Mark`
/// slices to build a snippet.
fn strip_bom(source: &str) -> &str {
    source.strip_prefix('\u{feff}').unwrap_or(source)
}

/// [`yamluna_core::parse`] over the stripped text, with the BOM put back on the first document.
fn load(scanned: &str, source: &str) -> Result<Vec<Document>, ParseError> {
    let mut docs = yamluna_core::parse(scanned)?;
    if scanned.len() != source.len()
        && let Some(first) = docs.first_mut()
    {
        first.bom = true;
    }
    Ok(docs)
}

/// Which half of [`_roundtrip_in_rust`] failed.
enum RoundTrip {
    Parse(Box<ParseError>),
    Emit(EmitError),
}

/// The extension module: `yamluna._yamluna`.
#[pymodule]
fn _yamluna(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(emit, m)?)?;
    m.add_function(wrap_pyfunction!(_roundtrip_in_rust, m)?)?;
    Ok(())
}
