use yamluna_scanner::{Event, Parser};
fn main() {
    let src = std::env::args().nth(1).unwrap();
    let src = src.replace("\\n", "\n").replace("\\t", "\t");
    for x in Parser::new_from_str(&src).keep_comments(true) {
        match x {
            Ok((ev, span)) => println!(
                "{:?} @ {}:{}..{}:{} idx {}..{}",
                ev,
                span.start.line(), span.start.col(),
                span.end.line(), span.end.col(),
                span.start.index(), span.end.index()
            ),
            Err(e) => { println!("ERR {e}"); break; }
        }
    }
}
