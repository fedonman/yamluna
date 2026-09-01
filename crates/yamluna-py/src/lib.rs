//! The `PyO3` boundary: [`yamluna_core::Document`] on one side, the Python record classes on
//! the other.
//!
//! `parse` takes YAML source and returns a list of `Doc` records, one per document in the
//! stream; `emit` takes those records back and returns one YAML stream. The record types
//! themselves are defined in Python, in the `yamluna._record` module, and nowhere else. This
//! crate imports that module once, caches a [`Py<PyType>`] per class in a [`PyOnceLock`], calls
//! the class to build an instance on load, and reads attributes off it on dump.
//!
//! Both directions run the core inside [`Python::detach`], so loads and emits on several
//! threads overlap and only the record building is serialised by the GIL.
//!
//! Errors cross as data. A [`ParseError`] carries an [`ErrorKind`], and `kind_name` is the one
//! place that discriminant becomes a Python exception class, by name, through
//! `yamluna.error.make_error`. Neither side of the boundary ever string-matches a message.
//!
//! Positions stay char offsets with 0-based line and column all the way into Python. A byte
//! offset in `Mark.pointer` slices mid-character on any document holding an accent or an emoji,
//! and the scanner's `Marker::index` is already a char offset, so nothing here converts it.

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

// There is no `#[pyclass]` in this crate on purpose: a second definition of `Node` here would
// be a second contract to keep in step with the one in `yamluna._record`. Each class is looked
// up once and held, so building a node costs a call, not an import.
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

// -- the kind and style codes of `yamluna._record` -----------------------------------------

const KIND_SCALAR: u8 = 0;
const KIND_SEQUENCE: u8 = 1;
const KIND_MAPPING: u8 = 2;
const KIND_ALIAS: u8 = 3;

/// The `STYLE_*` constant `yamluna._record` gives a [`Style`].
fn style_code(style: Style) -> u8 {
    // 0..=4 are the `ScalarStyle` variants in declaration order, 5 and 6 the collection
    // styles. One field carries both ranges because a node is never both.
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

/// The inverse of [`style_code`], for a record built in Python. Above 6 is a `ValueError`.
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

/// A [`Position`] as the record spells it: `(line, col)`, or `None` for one that was never
/// recorded. The Python layer hands these back unread.
fn place(p: Option<Position>) -> Option<(u32, u32)> {
    p.map(|p| (p.line, p.col))
}

/// The inverse of [`place`], for a record built or carried by Python.
fn read_place(o: &Bound<'_, PyAny>, name: &str) -> PyResult<Option<Position>> {
    Ok(o.getattr(name)?
        .extract::<Option<(u32, u32)>>()?
        .map(|(line, col)| Position { line, col }))
}

/// One [`Trivia`] as a `Trivia` record: a comment, or a run of blank lines.
fn build_trivia<'py>(py: Python<'py>, t: &Trivia) -> PyResult<Bound<'py, PyAny>> {
    let cls = trivia_class(py)?;
    match t {
        Trivia::Comment {
            text,
            own_line,
            col,
        } => cls.call1((text, *own_line, *col, 0u32)),
        // A blank run has no text and no column; `yamluna._record` prints neither.
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

/// One [`Node`] as a `Node` record, with its trivia and its recorded positions.
fn build_node<'py>(py: Python<'py>, n: &Node) -> PyResult<Bound<'py, PyAny>> {
    // `Node.anchor` is the anchor the node *defines*, except on an alias where it is the one
    // the node *references* (`NodeKind::Alias { anchor }`). A node never does both.
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
    // One slot per entry, in entry order. All-`None` is no record at all, and goes as the empty
    // list the record contract reads as "not recorded".
    let colon: Vec<Option<(u32, u32)>> = match &n.kind {
        NodeKind::Mapping { entries } => entries.iter().map(|e| place(e.colon)).collect(),
        _ => Vec::new(),
    };
    let colon = if colon.iter().all(Option::is_none) {
        Vec::new()
    } else {
        colon
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
        explicit.into_bound_py_any(py)?,
        build_trivia_list(py, &n.trivia.before)?.into_any(),
        match &n.trivia.eol {
            Some(t) => build_trivia(py, t)?,
            None => py.None().into_bound(py),
        },
        build_trivia_list(py, &n.trivia.inner)?.into_any(),
        build_trivia_list(py, &n.trivia.after)?.into_any(),
        n.tag_first.into_bound_py_any(py)?,
        n.flow_seps.clone().into_bound_py_any(py)?,
        place(n.anchor_at).into_bound_py_any(py)?,
        place(n.tag_at).into_bound_py_any(py)?,
        place(n.header_at).into_bound_py_any(py)?,
        colon.into_bound_py_any(py)?,
    ];
    node_class(py)?.call1(PyTuple::new(py, args)?)
}

/// One [`Document`] as a `Doc` record, holding every node of the tree in one flat arena.
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
    // Past twelve fields a tuple is no longer a `call1` argument, so the tuple is built by
    // hand, the same way `build_node` has always had to.
    let args = [
        d.version.into_bound_py_any(py)?,
        directives.into_bound_py_any(py)?,
        d.explicit_start.into_bound_py_any(py)?,
        d.explicit_end.into_bound_py_any(py)?,
        d.root.into_bound_py_any(py)?,
        nodes.into_any(),
        build_trivia_list(py, &d.leading)?.into_any(),
        build_trivia_list(py, &d.trailing)?.into_any(),
        d.bom.into_bound_py_any(py)?,
        d.final_line_break.into_bound_py_any(py)?,
        d.tags_before_version.into_bound_py_any(py)?,
        d.directives_raw.clone().into_bound_py_any(py)?,
        d.stream_tail.as_str().into_bound_py_any(py)?,
        d.line_space.clone().into_bound_py_any(py)?,
    ];
    doc_class(py)?.call1(PyTuple::new(py, args)?)
}

// -- record -> core -----------------------------------------------------------------------

/// The [`Trivia`] a `Trivia` record stands for.
fn read_trivia(o: &Bound<'_, PyAny>) -> PyResult<Trivia> {
    let py = o.py();
    // A non-zero `blank_lines` is what tells a blank run from a comment; the two share a class,
    // and a comment record leaves the count at zero.
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

/// The [`Node`] a `Node` record stands for.
///
/// A mapping record needs an even number of children, an alias record needs `anchor` set, and
/// `kind` and `style` must be codes the record module defines; anything else is a `ValueError`.
fn read_node(o: &Bound<'_, PyAny>) -> PyResult<Node> {
    let py = o.py();
    let kind: u8 = o.getattr(intern!(py, "kind"))?.extract()?;
    let anchor: Option<String> = o.getattr(intern!(py, "anchor"))?.extract()?;
    let children: Vec<NodeId> = o.getattr(intern!(py, "children"))?.extract()?;
    let merge: Vec<usize> = o.getattr(intern!(py, "merge"))?.extract()?;
    let explicit: Vec<usize> = o.getattr(intern!(py, "explicit"))?.extract()?;
    let colon: Vec<Option<(u32, u32)>> = o.getattr(intern!(py, "colon"))?.extract()?;

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
                        colon: colon
                            .get(i)
                            .copied()
                            .flatten()
                            .map(|(line, col)| Position { line, col }),
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
        anchor_at: read_place(o, "anchor_at")?,
        tag_at: read_place(o, "tag_at")?,
        header_at: read_place(o, "header_at")?,
        flow_seps: o.getattr(intern!(py, "flow_seps"))?.extract()?,
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

/// The [`Document`] a `Doc` record stands for, with every node index checked against the arena.
///
/// The returned document has no `duplicate_keys`: the record carries no such field, and the
/// emitter writes whatever entries the tree holds.
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
        directives_raw: o.getattr(intern!(py, "directives_raw"))?.extract()?,
        stream_tail: o.getattr(intern!(py, "stream_tail"))?.extract()?,
        explicit_start: o.getattr(intern!(py, "explicit_start"))?.extract()?,
        explicit_end: o.getattr(intern!(py, "explicit_end"))?.extract()?,
        bom: o.getattr(intern!(py, "bom"))?.extract()?,
        final_line_break: o.getattr(intern!(py, "final_line_break"))?.extract()?,
        root,
        nodes,
        leading: read_trivia_list(&o.getattr(intern!(py, "leading"))?)?,
        trailing: read_trivia_list(&o.getattr(intern!(py, "trailing"))?)?,
        duplicate_keys: Vec::new(),
        line_space: o.getattr(intern!(py, "line_space"))?.extract()?,
    })
}

/// The core [`EmitOptions`] an `EmitOptions` record asks for.
///
/// Three of the record's fields do not cross as they stand:
///
/// * `line_break` of `'\n'` becomes [`LineBreak::Auto`], so the emitter takes the break from
///   the lexemes and a CRLF file stays byte-identical through a default `YAML()`.
/// * `explicit_start`, `explicit_end` and `default_flow_style` are `bool` on the record and
///   `Option<bool>` in the core. `False` becomes `None`, which leaves each document with the
///   markers and the flow style it already had; only `True` overrides them.
/// * `canonical` is ignored, having no counterpart in the core emitter, and `allow_unicode` has
///   no counterpart on the record, so it keeps the core default.
///
/// A `line_break` other than `'\n'`, `'\r\n'` or `'\r'` is a `ValueError`.
fn read_opts(o: &Bound<'_, PyAny>) -> PyResult<EmitOptions> {
    let py = o.py();
    // `False` is what an unset `YAML.explicit_start` collapses to, so it has to read as "leave
    // the documents alone": under any other reading an unmodified round trip gains markers.
    let force = |name: &str| -> PyResult<Option<bool>> {
        Ok(if o.getattr(name)?.extract::<bool>()? {
            Some(true)
        } else {
            None
        })
    };
    // The record types `line_break` as a plain `str` and `main.py` writes `self.line_break or
    // '\n'`, so unset and LF are the same value by the time they arrive here.
    // ponytail: an `Optional[str]` slot on the record would tell the two apart.
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

/// The name `yamluna.error.make_error` classifies an [`ErrorKind`] by.
fn kind_name(kind: ErrorKind) -> &'static str {
    // This match is the whole of error classification. Adding a variant to `ErrorKind` fails
    // this build, which is how a new kind is made to pick its Python class here rather than by
    // string-matching a message somewhere downstream.
    match kind {
        ErrorKind::Scanner => "scanner",
    }
}

/// Turns an [`EmitError`] into `yamluna.error.EmitterError`, carrying its message and no
/// position.
fn emit_error(py: Python<'_>, e: &EmitError) -> PyErr {
    // Straight to the class rather than through `make_error`: an emit failure has no position
    // in any source text, and a `Mark` pointing at line 1 column 1 would be a lie. The Rust
    // type still picks the class.
    let built = emitter_error(py)
        .and_then(|cls| cls.call1((py.None(), py.None(), e.to_string(), py.None())));
    match built {
        Ok(exc) => PyErr::from_value(exc),
        Err(err) => err,
    }
}

/// Turns a core [`ParseError`] into the exception `yamluna.error` says its kind is.
///
/// # Arguments
///
/// * `source`: the text the core actually scanned, with any byte-order mark already off.
///   `index` is a char offset into that text and `Mark` slices it to build the snippet, so a
///   source that differs by one character puts the caret in the wrong place.
/// * `name`: the stream name the error prints, such as `<unicode string>` or a file path.
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

/// Loads every document of `source` into a list of `Doc` records.
///
/// # Arguments
///
/// * `source`: the YAML stream. A leading byte-order mark is taken off before scanning and
///   recorded on the first document, so it comes back on emit.
/// * `allow_duplicate_keys`: accepted for signature compatibility and not acted on here.
///   Duplicate keys are reported by `yamluna.constructor`, whatever this is set to.
/// * `name`: the stream name that appears in the position of any error raised.
///
/// # Errors
///
/// Raises the exception `yamluna.error` maps the core's [`ErrorKind`] to when `source` is not
/// well-formed YAML, with a `Mark` pointing into `source`.
#[pyfunction]
#[pyo3(signature = (source, *, allow_duplicate_keys = true, name = "<unicode string>"))]
fn parse<'py>(
    py: Python<'py>,
    source: &str,
    allow_duplicate_keys: bool,
    name: &str,
) -> PyResult<Bound<'py, PyList>> {
    // Duplicate detection stays in `yamluna.constructor`, which compares constructed keys and
    // owns the message ruamel users match on. A second detector here, keyed on the canonical
    // rendering of the key, would disagree with it on exactly the interesting cases.
    let _ = allow_duplicate_keys;
    // The core reports positions relative to the text it scanned, which is BOM-stripped.
    let scanned = strip_bom(source);
    // The core touches nothing Python, so detaching around it is safe, and it lets a load on
    // another thread run at the same time.
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

/// Emits `Doc` records as one YAML stream and returns it.
///
/// # Arguments
///
/// * `docs`: any iterable of `Doc` records, emitted in order.
/// * `opts`: an `EmitOptions` record.
///
/// # Errors
///
/// Raises `ValueError` when a record cannot be read as a document: a mapping with an odd number
/// of children, a node index past the end of the arena, an alias with no name, an unknown kind
/// or style code, or a `line_break` the emitter does not write. Raises
/// `yamluna.error.EmitterError` when the tree itself cannot be emitted. A missing or wrongly
/// typed attribute raises whatever the attribute access does.
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

/// Parses and emits `source` inside the core, without ever building a record.
///
/// This is the reference the record path is measured against, so that a round-trip failure is
/// attributable. If `emit(parse(text))` differs from the source and this does not, the record
/// model dropped something and the bug is at the boundary. If this differs the same way, the
/// emitter and the boundary agree and the defect is upstream of both.
///
/// # Errors
///
/// Raises the same exceptions as `parse` and `emit`: a `yamluna.error` class for a source that
/// is not well-formed, `yamluna.error.EmitterError` for a tree that cannot be emitted, and
/// `ValueError` for an `opts` record the core cannot take.
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
fn strip_bom(source: &str) -> &str {
    // The core strips a mark of its own and records it on the first document, but it never gets
    // the chance: stripping here is what keeps `ParseError::index` a char offset into the very
    // text `Mark` slices to build its snippet.
    source.strip_prefix('\u{feff}').unwrap_or(source)
}

/// [`yamluna_core::parse`] over the stripped text; the first document records the mark.
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

/// The extension module `yamluna._yamluna`: `parse`, `emit` and `_roundtrip_in_rust`.
#[pymodule]
fn _yamluna(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(emit, m)?)?;
    m.add_function(wrap_pyfunction!(_roundtrip_in_rust, m)?)?;
    Ok(())
}
