# Migrating from ruamel.yaml

For round-trip code, the port is one line:

```diff
-from ruamel.yaml import YAML
+from yamluna import YAML
```

`YAML()`, `load`, `dump`, `CommentedMap`, `CommentedSeq`, `.ca`, `.lc`, `.fa`, `.anchor`, the
scalar types and the error hierarchy all keep ruamel's names and signatures. What follows is
everything that is *not* a straight swap: three deliberate differences, a handful of smaller
behaviour changes, and the list of things that are absent because
[the scope](../README.md#what-it-is-not) excludes them.

Everything on this page was executed against `ruamel.yaml==0.19.1` and yamluna 0.1.0.

---

## 1. What ports unchanged

### The `YAML` object

| ruamel | yamluna | |
|---|---|---|
| `YAML()`, `YAML(typ='rt')` | same | `typ` accepts `'rt'` and nothing else |
| `YAML(output=...)` + `with` block | same | collects each `dump` and writes one stream |
| `.load(stream)` / `.load_all(stream)` | same | `str`/`bytes` are the document *text*; use a `Path` for a file |
| `.dump(data, stream)` / `.dump_all(docs, stream)` | same | `stream=None` returns the text |
| `.indent(mapping=, sequence=, offset=)` | same | but see §2.3 — it only applies to nodes *you* made |
| `.map_indent` / `.sequence_indent` / `.sequence_dash_offset` | same | |
| `.preserve_quotes` | same | plus §3.3 |
| `.default_flow_style`, `.width`, `.line_break` | same | |
| `.explicit_start`, `.explicit_end` | same | default `None` now means "keep what the source had" (§2.3) |
| `.allow_duplicate_keys` | same | plus §3.4 |
| `.version` | same | `(1, 2)` or `'1.2'`; forces the directive and `---` |
| `.register_class(cls)` / `@yaml.register` | same signature | different semantics — §2.2 |

### The containers

| ruamel | yamluna |
|---|---|
| `CommentedMap`, `CommentedSeq`, `CommentedSet`, `CommentedKeyMap`, `CommentedKeySeq`, `TaggedScalar` | same |
| `.ca`, `.lc`, `.fa`, `.anchor`, `.tag`, `.merge` | same |
| `.ca.items[key]` → `[key_eol, key_pre, value_eol, value_post]` | same layout, projected from the real store (§2.1) |
| `.ca.comment` → `[eol, [pre]]`, `.ca.end` | same |
| `yaml_set_start_comment`, `yaml_add_eol_comment`, `yaml_set_comment_before_after_key` | same |
| `yaml_end_comment_extend`, `yaml_key_comment_extend`, `yaml_value_comment_extend` | same |
| `yaml_set_anchor`, `yaml_anchor`, `copy_attributes` | same (`copy_attributes` copies rather than aliases — §3.5) |
| `CommentedMap.insert(pos, key, value, comment=)`, `move_to_end`, `mlget`, `non_merged_items`, `add_yaml_merge` | same |
| `.fa.set_flow_style()` / `.set_block_style()` / `.flow_style()` | same |
| `.lc.line`, `.lc.col`, `.lc.key(k)`, `.lc.value(k)`, `.lc.item(i)` | same, 0-based; `None` instead of `KeyError` for an unrecorded node |

`CommentedMap.rename(old, new)` is new: it renames a key in place and carries its comments, which
`pop` + `insert` cannot do.

### The scalar types

`LiteralScalarString`, `FoldedScalarString`, `SingleQuotedScalarString`,
`DoubleQuotedScalarString`, `PlainScalarString`, `PreservedScalarString`, `ScalarString`,
`preserve_literal`, `walk_tree`, `ScalarInt`, `HexInt`, `OctalInt`, `BinaryInt`, `ScalarFloat`,
`ScalarBoolean`, `TimeStamp` — all importable from `yamluna` directly, all with ruamel's
behaviour. `HexCapsInt` and `DecimalInt` are the two that are absent (§4).

### The errors

`YAMLError` → `MarkedYAMLError` → `ScannerError`, `ParserError`, `ComposerError`,
`ConstructorError`, `RepresenterError`, `EmitterError`, `DuplicateKeyError`; plus
`YAMLStreamError` and the warning classes. `Mark` keeps `.name`, `.line`, `.column`, `.index`,
`.buffer`, `.pointer` and `.get_snippet()`, and `.line`/`.column` stay 0-based as in ruamel.

---

## 2. Deliberately different

### 2.1 Comments are keyed by node identity, not by index

`.ca` reads the same, so code that *inspects* comments needs no change. Code that *mutates*
structure gets different — correct — results.

ruamel stores an own-line comment glued into the previous sibling's end-of-line
`CommentToken.value`, so it describes one node and lives on another. yamluna stores trivia on the
node it describes. Concretely, for `services: {web, worker, cron}` with a comment above each:

| operation | ruamel | yamluna |
|---|---|---|
| `seq.insert(0, x)` | the old first element's comment now labels `x` | `x` has no comment; every other one is untouched |
| `del seq[0]` | the orphaned comment survives and mislabels the new first element; the *neighbour's* comment is destroyed | the deleted element's comment goes, nothing else moves |
| `del map[k]` / `map.pop(k)` | `.ca.items[k]` survives, so re-adding `k` resurrects the old comment on the new value | the record goes with the entry |
| `map.move_to_end(k)` | comments scatter across the document | comments travel with the entry |
| `seq.reverse()` | every comment stays on its old index | comments follow the elements |
| rename a key | `pop` + `insert` loses the end-of-line comment | `map.rename(old, new)` keeps all four slots |

**What to check when porting:** if you have workaround code that repairs `.ca` after a mutation —
re-assigning `ca.items`, deleting stale keys, moving `CommentToken`s by hand — delete it. It will
now move comments that are already in the right place. `docs/DIVERGENCES.md` A1–A7 has the measured
before/after for each row.

One thing that is *not* different, because ruamel gets it right: an own-line comment above a
container's **first** entry belongs to the container (`.ca.comment[1]`), not to that entry. It is
the block's heading, and it stays at the top of the block when you reorder what is under it.

### 2.2 The registry is per-instance, and tags carry a namespace

ruamel's `register_class` is a classmethod on the representer and constructor: it mutates
process-global tables, keyed on `'!' + cls.__name__`. Two libraries with a `Circuit` each silently
overwrite one another, and which one you get back depends on import order.

```python
yaml = YAML()
yaml.register_class(Circuit)     # same call
```

Three consequences to plan for:

1. **Registration does not leak.** `YAML()` starts empty. If your code registered on one `YAML()`
   and loaded on another, register on both — or share one explicitly:
   `YAML(registry=my_registry)`, or use the module-level `yamluna.register_class` with
   `YAML(registry=yamluna.default_registry)`.
2. **The output gains a `%TAG` line and a `---`.** A document that uses registered classes always
   declares its sources:
   ```yaml
   %TAG ! tag:libx/
   ---
   main: !Circuit
     qubits: 2
   ```
   The tag itself is unchanged (`!Circuit`) as long as only one source is in play. Two sources,
   and the second gets a named handle (`!liby!Circuit`). If a downstream consumer parses your
   YAML with a regex rather than a YAML parser, that is the line that will surprise it.
3. **An ambiguous bare `!Circuit` raises instead of guessing.** Loading a hand-written file with
   no `%TAG` line works whenever exactly one registered class has that name; with two, you get a
   `ConstructorError` listing both fully qualified candidates and telling you to add a `%TAG`
   directive or `register_class(cls, source='...')`.

`cls.yaml_tag` and the `to_yaml` / `from_yaml` classmethod hooks keep ruamel's signatures, so
existing classes port unchanged. `cls.yaml_source` and `register_class(source=)` are new: they pin
the namespace. README "The tag registry" and DESIGN §5 have the full wire format;
`examples/custom_classes.py` runs it.

### 2.3 Layout is reproduced, not re-decided

ruamel's `indent()` / `explicit_start` / `width` are global emitter settings applied to every node.
yamluna records what each node looked like and reproduces it; the settings apply to nodes **you
created or restyled**.

So the ubiquitous incantation

```python
yaml.indent(mapping=2, sequence=4, offset=2)   # ruamel: needed to stop re-indentation
```

is no longer needed to preserve a file's sequence indentation — that happens anyway, per node,
even for a file that mixes two styles. Keep the call only if you want *new* nodes emitted that
way.

Similarly `explicit_start=None` (the default) now means "keep the `---` the source had" rather than
"never write one". Set it to `True` or `False` to force the issue.

**The practical effect on a port:** your dumps will produce different bytes than they did under
ruamel — that is the point of the library, but it means a golden-file test comparing against
ruamel's output will fail. Re-record those fixtures against the *input* file instead; for an
unmodified document, `dump(load(text)) == text` now holds.

---

## 3. Smaller behaviour differences

### 3.1 Anchors are always emitted

ruamel drops `&name` for an anchor referenced fewer than twice unless `always_dump` is set.
yamluna emits every anchor that is in the document, because it is source text.
`Anchor.always_dump` still exists and still matters for anchors you set on new nodes.

### 3.2 Aliases stay aliases

ruamel resolves an alias by cloning the target, so `*base` is unrecoverable after a load and a
recursive anchor fails outright. yamluna keeps `Alias` as a node kind and re-emits `*base`.
`d['use'] is d['base']` remains true at the Python surface, as in ruamel, so identity checks port
unchanged.

### 3.3 `preserve_quotes` gates *constructed* quoted strings

A quoted scalar that came from a file keeps its quotes either way, because the lexeme is
reproduced verbatim. But a `DoubleQuotedScalarString` you construct yourself is only emitted
quoted when `preserve_quotes = True`:

```python
yaml.dump({'a': DoubleQuotedScalarString('hi')})    # ruamel: a: "hi"
                                                    # yamluna, default:  a: hi
                                                    # yamluna, preserve_quotes=True:  a: "hi"
```

If you construct quoted scalars, set `yaml.preserve_quotes = True`. (Most round-trip code already
does.)

### 3.4 Duplicate keys

`allow_duplicate_keys = False` (the default) raises `DuplicateKeyError`, as in ruamel.
`allow_duplicate_keys = True` warns — naming both source positions — and the **last** value wins;
ruamel keeps the first and says nothing. Note that `CommentedMap` is a `dict`, so the duplicate
entry itself cannot survive a round trip in either library.

### 3.5 `copy()` no longer shares `.ca`

`m.copy().ca is m.ca` was `True` in ruamel, so adding a comment to the copy edited the original.
It is `False` here, and `copy_attributes(t)` copies rather than aliases. If you relied on the
aliasing to propagate a comment, assign `.ca` explicitly.

### 3.6 `.lc` returns `None` instead of raising

`.lc` is still a load-time snapshot that is not maintained across edits. The one change:
`.lc.key(k)` on a key with no recorded position (one you just inserted) returns `None` rather than
raising `KeyError`.

### 3.7 Dumping does not mutate the object

ruamel's representer appends to `ca.comment` on every dump, so `post, pre = obj.ca.comment` raises
after the first one. Here the object graph is untouched by a dump, and `.ca.comment` keeps its
documented two-element shape however many times you serialise.

---

## 4. Absent, and what to do instead

| ruamel | why it is not here | instead |
|---|---|---|
| `YAML(typ='safe' / 'base' / 'unsafe' / 'rtsc')` | this library is the round-trip mode; the others are `json.load` with more spelling, and shipping them means shipping four object models | `import yaml` (PyYAML) or `json` for a plain load; keep `yamluna` for the files you edit |
| `!!python/object:`, `!!python/name:`, `!!python/module:` | arbitrary-object construction from a document is a remote-code-execution primitive, and `typ='rt'` never supported it either | `register_class` with `to_yaml`/`from_yaml` |
| `yaml.Constructor = MyConstructor`, `.Representer`, `.Parser`, `.Emitter`, `.Resolver`, `.Scanner`, `.Serializer`, `.Composer` | component substitution requires a stable Python-level pipeline; here the pipeline is Rust, and the seam is the flat record list, not a class | `to_yaml` / `from_yaml` hooks for per-class control; `EmitOptions` (via the `YAML` settings) for global layout. `YAML` uses `__slots__`, so assigning these raises `AttributeError` rather than being silently ignored |
| `official_plug_ins()`, `yaml.plug_ins`, `pure=` | no C-vs-Python duality to switch between: there is one implementation | — |
| `scan()`, `parse()`, `compose()`, `serialize()`, `emit()` and their `_all` forms | the low-level pipeline exposes ruamel's token/event/node classes as public API; yamluna's equivalent is the flat `Doc`/`Node` record list at the FFI boundary, and freezing it as a public API would freeze the internals | walk the loaded `CommentedMap`/`CommentedSeq` — it carries the same information plus the trivia. For token-level work, `yamluna._yamluna.parse(text)` returns the records, unsupported and unstable |
| module-level `load()`, `dump()`, `safe_load()`, `round_trip_load()`, `round_trip_dump()` | deprecated in ruamel since 0.15 and removed in 0.19; keeping them would re-import the global mutable state the registry exists to eliminate | `YAML().load(...)` / `YAML().dump(...)` |
| `add_constructor`, `add_representer`, `add_multi_constructor`, `add_implicit_resolver`, `add_path_resolver` | process-global classmethod registries — DIVERGENCES C2 | `yaml.register_class(cls)`, per instance |
| `YAMLObject` / `YAMLObjectMetaclass` | a metaclass that registers on import, into the global table | `@yaml.register` on the class, or `register_class` at your package's entry point |
| `.tags`, `.doc_infos`, `DocInfo`, `docinfo` | ruamel 0.19's directive-reporting API; yamluna keeps directives on the document and re-emits them, and does not expose a parallel reporting surface | `yaml.version` for `%YAML`; `%TAG` handling is automatic |
| `canonical`, `default_style`, `allow_unicode`, `sort_base_mapping_type_on_output`, `compact_seq_seq`, `compact_seq_map`, `block_seq_indent`, `prefix_colon`, `top_level_colon_align`, `scalar_after_indicator`, `brace_single_entry_mapping_in_flow_sequence` | emitter knobs that exist to re-decide the layout of nodes ruamel could not reproduce. Reproduction makes most of them unnecessary | per-node style: the scalar-string subclasses, `.fa.set_flow_style()`, `.fa.set_block_style()`. Non-ASCII is written as-is (never `\uXXXX`-escaped) unless the source escaped it. Keys are never reordered on dump |
| `HexCapsInt`, `DecimalInt` | `DecimalInt` cannot be dumped in ruamel at all (`RepresenterError`); capitalisation is a property of the lexeme, which is preserved | `HexInt` for hex; a loaded `0xFF` or `0X1F` keeps its own capitalisation |
| `CommentedMap._ok`, `._ref`, `.update_key_value`, `._yaml_set_kv_line_col`, `CommentedSeq._lst` | private internals of ruamel's implementation | none needed — if you were poking these to fix comment drift, §2.1 is the fix |
| `comment_handling` / `C_*` slot constants | the `C_*` constants describe the `typ='rtsc'` layout, not the `typ='rt'` one they are usually used with (DIVERGENCES A10) | the four rt slots are documented by position in §1; `yamluna.comments` exports `C_KEY_EOL`, `C_KEY_PRE`, `C_VALUE_EOL`, `C_VALUE_POST` matching the *actual* rt layout |

---

## 5. Porting checklist

```bash
grep -rn "ruamel"                      # the import, and any typ= other than 'rt'
grep -rn "yaml.indent("                # usually deletable (§2.3)
grep -rn "add_representer\|add_constructor\|YAMLObject"   # -> register_class (§2.2)
grep -rn "\.ca\.items\[" | grep "="    # hand-repaired comment tables (§2.1) — delete
grep -rn "round_trip_load\|safe_load\|\.compose\|\.serialize\|\.scan("  # §4
```

Then run your test suite. The failures you should *expect* are golden-file comparisons against
ruamel's output (§2.3) and any test asserting that a comment ends up where ruamel's index-keyed
model put it (§2.1).
