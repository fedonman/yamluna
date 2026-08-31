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

use pyo3::exceptions::{PyNotImplementedError, PyValueError};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyList, PyTuple, PyType};
use pyo3::{IntoPyObjectExt, intern};

use yamluna_core::{
    Document, Entry, ErrorKind, Node, NodeId, NodeKind, NodeTag, ParseError, Position, ScalarStyle,
    Style, TagDirective, Trivia, Trivia4,
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
    // `merge` holds positions in `children`, so an entry's key sits at twice its entry index.
    let merge: Vec<usize> = match &n.kind {
        NodeKind::Mapping { entries } => entries
            .iter()
            .enumerate()
            .filter(|(_, e)| e.merge)
            .map(|(i, _)| i * 2)
            .collect(),
        _ => Vec::new(),
    };
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
        build_trivia_list(py, &n.trivia.before)?.into_any(),
        match &n.trivia.eol {
            Some(t) => build_trivia(py, t)?,
            None => py.None().into_bound(py),
        },
        build_trivia_list(py, &n.trivia.inner)?.into_any(),
        build_trivia_list(py, &n.trivia.after)?.into_any(),
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
                        // ponytail: the `Doc` record has no slot for the explicit `? key` form,
                        // so a hand-built entry is always implicit. See the note in lib.rs.
                        explicit: false,
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
        style: style_from_code(o.getattr(intern!(py, "style"))?.extract()?)?,
        value: o.getattr(intern!(py, "value"))?.extract()?,
        raw: o.getattr(intern!(py, "raw"))?.extract()?,
        pos: Position {
            line: o.getattr(intern!(py, "line"))?.extract()?,
            col: o.getattr(intern!(py, "col"))?.extract()?,
        },
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
        explicit_start: o.getattr(intern!(py, "explicit_start"))?.extract()?,
        explicit_end: o.getattr(intern!(py, "explicit_end"))?.extract()?,
        // ponytail: neither `bom` nor `final_line_break` has a slot on the `Doc` record, so a
        // round trip through Python loses them. Defaults chosen to match the common file.
        bom: false,
        final_line_break: true,
        root,
        nodes,
        leading: read_trivia_list(&o.getattr(intern!(py, "leading"))?)?,
        trailing: read_trivia_list(&o.getattr(intern!(py, "trailing"))?)?,
        duplicate_keys: Vec::new(),
    })
}

/// The emitter knobs, read off an `EmitOptions` record.
#[allow(clippy::struct_excessive_bools)] // ten independent knobs; grouping them buys nothing
#[derive(Clone, Debug)]
struct EmitOpts {
    map_indent: usize,
    seq_indent: usize,
    seq_offset: usize,
    width: usize,
    line_break: String,
    explicit_start: bool,
    explicit_end: bool,
    default_flow_style: bool,
    canonical: bool,
    preserve_quotes: bool,
}

fn read_opts(o: &Bound<'_, PyAny>) -> PyResult<EmitOpts> {
    let py = o.py();
    Ok(EmitOpts {
        map_indent: o.getattr(intern!(py, "map_indent"))?.extract()?,
        seq_indent: o.getattr(intern!(py, "seq_indent"))?.extract()?,
        seq_offset: o.getattr(intern!(py, "seq_offset"))?.extract()?,
        width: o.getattr(intern!(py, "width"))?.extract()?,
        line_break: o.getattr(intern!(py, "line_break"))?.extract()?,
        explicit_start: o.getattr(intern!(py, "explicit_start"))?.extract()?,
        explicit_end: o.getattr(intern!(py, "explicit_end"))?.extract()?,
        default_flow_style: o.getattr(intern!(py, "default_flow_style"))?.extract()?,
        canonical: o.getattr(intern!(py, "canonical"))?.extract()?,
        preserve_quotes: o.getattr(intern!(py, "preserve_quotes"))?.extract()?,
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
    let scanned = source.strip_prefix('\u{feff}').unwrap_or(source);
    let docs = py
        .detach(|| yamluna_core::parse(scanned))
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
    py.detach(|| {
        // TODO(emitter): replace with `yamluna_core::emit(&docs, &opts)` once it lands.
        let _ = (&docs, &opts);
        Err::<String, _>("yamluna_core::emit is not implemented yet")
    })
    .map_err(PyNotImplementedError::new_err)
}

/// The extension module: `yamluna._yamluna`.
#[pymodule]
fn _yamluna(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(emit, m)?)?;
    Ok(())
}
