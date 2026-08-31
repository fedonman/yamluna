use yamluna_core::{Document, NodeId, NodeKind, Trivia};

fn tv(t: &Trivia) -> String {
    match t {
        Trivia::Comment {
            text,
            own_line,
            col,
        } => format!("{}@{col}{text:?}", if *own_line { "own" } else { "eol" }),
        Trivia::BlankLines(n) => format!("blank({n})"),
    }
}
fn slot(name: &str, v: &[Trivia]) -> String {
    if v.is_empty() {
        String::new()
    } else {
        format!(
            " {name}=[{}]",
            v.iter().map(tv).collect::<Vec<_>>().join(", ")
        )
    }
}

fn show(d: &Document, id: NodeId, ind: usize) {
    let n = d.node(id);
    let pad = " ".repeat(ind);
    let head = match &n.kind {
        NodeKind::Scalar => format!(
            "Scalar {:?} raw={:?}",
            n.value.as_deref().unwrap_or(""),
            n.raw.as_deref().unwrap_or("")
        ),
        NodeKind::Alias { anchor } => format!("Alias *{anchor}"),
        NodeKind::Sequence { .. } => "Seq".into(),
        NodeKind::Mapping { .. } => "Map".into(),
    };
    let anchor = n
        .anchor
        .as_ref()
        .map(|a| format!(" &{a}"))
        .unwrap_or_default();
    let tag = n
        .tag
        .as_ref()
        .map(|t| format!(" tag({}|{}|{})", t.handle, t.suffix, t.resolved))
        .unwrap_or_default();
    println!(
        "{pad}#{id} {head}{anchor}{tag} {:?} @{}:{}{}{}{}{}",
        n.style,
        n.pos.line,
        n.pos.col,
        slot("before", &n.trivia.before),
        n.trivia
            .eol
            .as_ref()
            .map(|t| format!(" eol={}", tv(t)))
            .unwrap_or_default(),
        slot("inner", &n.trivia.inner),
        slot("after", &n.trivia.after),
    );
    match &n.kind {
        NodeKind::Sequence { items } => {
            for i in items {
                show(d, *i, ind + 2);
            }
        }
        NodeKind::Mapping { entries } => {
            for e in entries {
                println!("{pad}  entry merge={} explicit={}", e.merge, e.explicit);
                show(d, e.key, ind + 4);
                show(d, e.value, ind + 4);
            }
        }
        _ => {}
    }
}

fn main() {
    let path = std::env::args().nth(1).unwrap();
    let src = std::fs::read_to_string(&path).unwrap();
    for (i, d) in yamluna_core::parse(&src).unwrap().iter().enumerate() {
        println!(
            "== doc {i} start={} end={} bom={} nl={} version={:?} tags={:?} dups={:?}",
            d.explicit_start,
            d.explicit_end,
            d.bom,
            d.final_line_break,
            d.version,
            d.tag_directives,
            d.duplicate_keys
        );
        println!(
            "  leading=[{}]",
            d.leading.iter().map(tv).collect::<Vec<_>>().join(", ")
        );
        if let Some(r) = d.root {
            show(d, r, 2);
        }
        println!(
            "  trailing=[{}]",
            d.trailing.iter().map(tv).collect::<Vec<_>>().join(", ")
        );
    }
}
