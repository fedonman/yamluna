# Porting from ruamel.yaml

For round-trip code, the port is one line:

```diff
-from ruamel.yaml import YAML
+from yamluna import YAML
```

`YAML()`, `load`, `dump`, `CommentedMap`, `CommentedSeq`, `.ca`, `.lc`, `.fa`, `.anchor`, the
scalar types and the error hierarchy all keep ruamel's names and signatures. This page is
everything that is *not* a straight swap: three deliberate differences, a handful of smaller
behaviour changes, and the list of things that are absent because
[the scope](../index.md#what-it-is-not) excludes them.

Everything here was run against `ruamel.yaml` 0.19.1 and yamluna 0.1.0.

---

## What ports unchanged

### The `YAML` object

| ruamel | yamluna | |
|---|---|---|
| `YAML()`, `YAML(typ='rt')` | same | `typ` accepts `'rt'` and nothing else |
| `YAML(output=...)` and a `with` block | same | collects each `dump` and writes one stream |
| `.load(stream)` / `.load_all(stream)` | same | a `str` or `bytes` is the document *text*; pass a `Path` for a file |
| `.dump(data, stream)` / `.dump_all(docs, stream)` | same | `stream=None` returns the text |
| `.indent(mapping=, sequence=, offset=)` | same | but see [layout](#layout-is-reproduced-not-re-decided): it is for nodes *you* made |
| `.map_indent` / `.sequence_indent` / `.sequence_dash_offset` | same | |
| `.preserve_quotes` | same | plus [quoting](#preserve_quotes-gates-quoted-strings-you-construct) |
| `.default_flow_style`, `.width`, `.line_break` | same | |
| `.explicit_start`, `.explicit_end` | same | the default `None` now means "keep what the source had" |
| `.allow_duplicate_keys` | same | plus [duplicate keys](#duplicate-keys) |
| `.version` | same | `(1, 2)` or `'1.2'`; forces the directive and a `---` |
| `.register_class(cls)` / `@yaml.register` | same signature | different semantics, see [the registry](#the-registry-is-per-instance-and-tags-carry-a-namespace) |

### The containers

| ruamel | yamluna |
|---|---|
| `CommentedMap`, `CommentedSeq`, `CommentedSet`, `CommentedKeyMap`, `CommentedKeySeq`, `TaggedScalar` | same |
| `.ca`, `.lc`, `.fa`, `.anchor`, `.tag`, `.merge` | same |
| `.ca.items[key]` as `[key_eol, key_pre, value_eol, value_post]` | same layout, projected from the real store |
| `.ca.comment` as `[eol, [pre]]`, and `.ca.end` | same |
| `yaml_set_start_comment`, `yaml_add_eol_comment`, `yaml_set_comment_before_after_key` | same |
| `yaml_end_comment_extend`, `yaml_key_comment_extend`, `yaml_value_comment_extend` | same |
| `yaml_set_anchor`, `yaml_anchor`, `copy_attributes` | same, except that `copy_attributes` [copies rather than aliases](#copy-no-longer-shares-ca) |
| `CommentedMap.insert(pos, key, value, comment=)`, `move_to_end`, `mlget`, `non_merged_items`, `add_yaml_merge` | same |
| `.fa.set_flow_style()` / `.set_block_style()` / `.flow_style()` | same |
| `.lc.line`, `.lc.col`, `.lc.key(k)`, `.lc.value(k)`, `.lc.item(i)` | same, 0-based; `None` instead of `KeyError` for an unrecorded node |

`CommentedMap.rename(old, new)` is new. It renames a key in place and carries its comments, which
`pop` plus `insert` cannot do.

### The scalar types

`LiteralScalarString`, `FoldedScalarString`, `SingleQuotedScalarString`,
`DoubleQuotedScalarString`, `PlainScalarString`, `PreservedScalarString`, `ScalarString`,
`preserve_literal`, `walk_tree`, `ScalarInt`, `HexInt`, `OctalInt`, `BinaryInt`, `ScalarFloat`,
`ScalarBoolean`, `TimeStamp`: all importable from `yamluna` directly, all with ruamel's behaviour.
`HexCapsInt` and `DecimalInt` are the two that are [absent](#absent-and-what-to-use-instead).

### The errors

`YAMLError` to `MarkedYAMLError` to `ScannerError`, `ParserError`, `ComposerError`,
`ConstructorError`, `RepresenterError`, `EmitterError`, `DuplicateKeyError`, plus
`YAMLStreamError` and the warning classes. `Mark` keeps `.name`, `.line`, `.column`, `.index`,
`.buffer`, `.pointer` and `.get_snippet()`, and `.line` and `.column` stay 0-based as in ruamel.

---

## Deliberately different

### Comments belong to nodes, not to positions

`.ca` reads the same, so code that *inspects* comments needs no change. Code that *mutates*
structure gets different, and correct, results.

ruamel stores an own-line comment glued into the previous sibling's end-of-line
`CommentToken.value`, so it describes one node and lives on another. yamluna stores trivia on the
node it describes. For a mapping or sequence with a comment above each entry:

| operation | ruamel | yamluna |
|---|---|---|
| `seq.insert(0, x)` | the old first element's comment now labels `x` | `x` has no comment; every other one is untouched |
| `del seq[0]` | the orphaned comment survives and mislabels the new first element, and the *neighbour's* comment is destroyed | the deleted element's comment goes, nothing else moves |
| `del map[k]` / `map.pop(k)` | `.ca.items[k]` survives, so re-adding `k` resurrects the old comment on the new value | the record goes with the entry |
| `map.move_to_end(k)` | comments scatter across the document | comments travel with the entry |
| `seq.reverse()` | every comment stays on its old index | comments follow the elements |
| renaming a key | `pop` plus `insert` loses the end-of-line comment | `map.rename(old, new)` keeps all four slots |

**What to check when porting.** If you have workaround code that repairs `.ca` after a mutation,
re-assigning `ca.items`, deleting stale keys, moving `CommentToken`s by hand, delete it. It will
now move comments that are already in the right place.
[Behaviour differences](differences.md#a-the-comment-model) A1 to A7 has the before and after for
every row of that table, measured.

One thing that is *not* different, because ruamel gets it right: an own-line comment above a
container's **first** entry belongs to the container (`.ca.comment[1]`), not to that entry. It is
the block's heading, and it stays at the top of the block when you reorder what is under it. The
[same position](differences.md#a2-seqinsert-puts-the-following-items-comment-above-the-new-item)
is also where yamluna's own remaining ownership gap sits, pinned by xfails.

### The registry is per instance, and tags carry a namespace

ruamel's `register_class` is a classmethod on the representer and constructor. It mutates
process-global tables keyed on `'!' + cls.__name__`, so two libraries with a `Circuit` each
silently overwrite one another and which one you get back depends on import order
([C1](differences.md#c1-register_class-keys-the-constructor-registry-on-the-class-name),
[C2](differences.md#c2-register_class-is-process-global-not-per-yaml)).

The call is the same:

```python
yaml = YAML()
yaml.register_class(Circuit)
```

Three consequences to plan for.

1. **Registration does not leak.** `YAML()` starts empty. If your code registered on one `YAML()`
   and loaded on another, register on both, or share one explicitly with
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
   The tag itself is unchanged, `!Circuit`, as long as only one source is in play. With two
   sources the second gets a named handle, `!liby!Circuit`. If a downstream consumer parses your
   YAML with a regex rather than a YAML parser, that is the line that will surprise it.
3. **An ambiguous bare `!Circuit` raises instead of guessing.** Loading a hand-written file with
   no `%TAG` line works whenever exactly one registered class has that name. With two you get a
   `ConstructorError` listing both fully qualified candidates and telling you to add a `%TAG`
   directive or `register_class(cls, source='...')`.

`cls.yaml_tag` and the `to_yaml` and `from_yaml` classmethod hooks keep ruamel's signatures, so
existing classes port unchanged. `cls.yaml_source` and `register_class(source=)` are new: they pin
the namespace. [Custom classes and tags](../guide/custom-classes.md) has the wire format and a
runnable example.

### Layout is reproduced, not re-decided

ruamel's `indent()`, `explicit_start` and `width` are global emitter settings applied to every
node. yamluna records what each node looked like and reproduces it, and the settings apply to
nodes **you created or restyled**.

So the ubiquitous incantation

```python
yaml.indent(mapping=2, sequence=4, offset=2)   # ruamel: needed to stop re-indentation
```

is no longer what preserves a file's sequence indentation. That happens anyway, per node, even for
a file that mixes two styles: `tests/corpus/struct-seq-indent.yaml` mixes offset-0, offset-2 and
offset-6 sequences in one document, and a default `YAML()` returns it byte for byte. Keep the call
only if you want *new* nodes emitted that way.

!!! warning "Keeping the call is not free"

    `indent()` reaches further into a loaded document than it should. A sequence whose `-` the
    source wrote at the parent key's own column is re-laid-out to match the setting, and for a
    compact block mapping under a dash the result does not re-parse:

    ```pycon
    >>> yaml = YAML()
    >>> yaml.indent(mapping=2, sequence=4, offset=2)
    >>> yaml.dump(yaml.load('a:\n- 1\n- 2\n'))
    'a:\n  - 1\n  - 2\n'
    >>> yaml.dump(yaml.load('outer:\n- inner: 1\n  also: 2\n- inner: 3\n'))
    'outer:\n  - inner: 1\n  also: 2\n  - inner: 3\n'
    >>> yaml.load(_)
    ScannerError: while parsing a block collection, did not find expected '-' indicator
    ```

    A sequence the source already indented is left alone, at 2 columns or at 6. The safe port is
    to delete the `indent()` call, which is what preserves the file either way; keep it only when
    you are creating sequence nodes and want them laid out that way, and check the output.

`explicit_start = None`, the default, now means "keep the `---` the source had" rather than "never
write one". Set it to `True` or `False` to force the issue.

**The practical effect on a port.** Your dumps will produce different bytes than they did under
ruamel. That is the point of the library, but it means a golden-file test comparing against
ruamel's output will fail. Re-record those fixtures against the *input* file instead: for an
unmodified document, `dump(load(text)) == text` now holds.

---

## Smaller differences

### Anchors are always emitted

ruamel drops `&name` for an anchor referenced fewer than twice unless `always_dump` is set
([B1](differences.md#b1-anchors-referenced-fewer-than-twice-are-dropped)). yamluna emits every
anchor that is in the document, because it is source text. `Anchor.always_dump` still exists and
still matters for anchors you set on new nodes.

### Aliases stay aliases

ruamel resolves an alias by cloning the target, so `*base` is unrecoverable after a load and a
recursive anchor fails outright. yamluna keeps an alias as a node kind and re-emits `*base`.
`d['use'] is d['base']` is `True` in both, so identity checks port unchanged.

### `preserve_quotes` gates quoted strings you construct

A quoted scalar that came from a file keeps its quotes either way, because the lexeme is reproduced
verbatim. A `DoubleQuotedScalarString` you construct yourself is only emitted quoted when
`preserve_quotes = True`:

```text
yaml.dump({'a': DoubleQuotedScalarString('hi')})

ruamel                     'a: "hi"\n'
yamluna, default           'a: hi\n'
yamluna, preserve_quotes   'a: "hi"\n'
```

If you construct quoted scalars, set `yaml.preserve_quotes = True`. Most round-trip code already
does.

### Duplicate keys

`allow_duplicate_keys = False`, the default, raises `DuplicateKeyError` as in ruamel, naming both
source positions. `allow_duplicate_keys = True` warns, also naming both positions, and the **last**
value wins; ruamel keeps the first and says nothing
([D5](differences.md#d5-allow_duplicate_keystrue-keeps-the-first-value-and-warns-about-nothing)).
`CommentedMap` is a `dict` in both libraries, so the duplicate entry itself cannot survive a round
trip either way.

### `copy()` no longer shares `.ca`

`m.copy().ca is m.ca` was `True` in ruamel, so adding a comment to the copy edited the original
([D6](differences.md#d6-copy_attributes-and-commentedmapcopy-share-the-comment-object)). It is
`False` here, and `copy_attributes(t)` copies rather than aliases. If you relied on the aliasing to
propagate a comment, assign `.ca` explicitly.

### `.lc` returns `None` instead of raising

`.lc` is still a load-time snapshot, 0-based, not maintained across edits. The one change is that
`.lc.key(k)` on a key with no recorded position, one you just inserted, returns `None` rather than
raising `KeyError`.

### Dumping does not mutate the object

ruamel's representer appends to `ca.comment` on every dump, so `post, pre = obj.ca.comment` raises
after the first one ([A8](differences.md#a8-ca-is-mutated-by-dumping)). Here the object graph is
untouched by a dump, and `.ca.comment` keeps its documented two-element shape however many times
you serialise.

---

## Absent, and what to use instead

| ruamel | why it is not here | instead |
|---|---|---|
| `YAML(typ='safe' / 'base' / 'unsafe' / 'rtsc')` | this library is the round-trip mode; the others are `json.load` with more spelling, and shipping them means shipping four object models. `YAML(typ='safe')` raises `ValueError` with a message pointing here | `import yaml` (PyYAML) or `json` for a plain load; keep `yamluna` for the files you edit |
| `!!python/object:`, `!!python/name:`, `!!python/module:` | arbitrary-object construction from a document is a remote-code-execution primitive, and `typ='rt'` never supported it either | `register_class` with `to_yaml` and `from_yaml` |
| `yaml.Constructor = MyConstructor`, and `.Representer`, `.Parser`, `.Emitter`, `.Resolver`, `.Scanner`, `.Serializer`, `.Composer` | component substitution needs a stable Python-level pipeline; here the pipeline is Rust and the seam is a flat record list, not a class | `to_yaml` and `from_yaml` hooks for per-class control, the `YAML` settings for global layout. `YAML` uses `__slots__`, so assigning these raises `AttributeError: 'YAML' object has no attribute 'Constructor' and no __dict__ for setting new attributes` rather than being silently ignored |
| `official_plug_ins()`, `yaml.plug_ins`, `pure=` | no C-versus-Python duality to switch between: there is one implementation | |
| `scan()`, `parse()`, `compose()`, `serialize()`, `emit()` and their `_all` forms | the low-level pipeline exposes ruamel's token, event and node classes as public API; the equivalent here is the flat record list at the FFI boundary, and freezing that as public API would freeze the internals | walk the loaded `CommentedMap` and `CommentedSeq`: they carry the same information plus the trivia. For token-level work `yamluna._yamluna.parse(text)` returns the records, unsupported and unstable |
| module-level `load()`, `dump()`, `safe_load()`, `round_trip_load()`, `round_trip_dump()` | deprecated in ruamel since 0.15 and gone in 0.19, where calling `ruamel.yaml.safe_load` raises `AttributeError`. Keeping them would re-import the global mutable state the registry exists to eliminate | `YAML().load(...)` and `YAML().dump(...)` |
| `add_constructor`, `add_representer`, `add_multi_constructor`, `add_implicit_resolver`, `add_path_resolver` | process-global classmethod registries ([C2](differences.md#c2-register_class-is-process-global-not-per-yaml)) | `yaml.register_class(cls)`, per instance |
| `YAMLObject` / `YAMLObjectMetaclass` | a metaclass that registers on import, into the global table | `@yaml.register` on the class, or `register_class` at your package's entry point |
| `.tags`, `.doc_infos`, `DocInfo`, `docinfo` | ruamel 0.19's directive-reporting API; yamluna keeps directives on the document and re-emits them, and does not expose a parallel reporting surface | `yaml.version` for `%YAML`; `%TAG` handling is automatic |
| `canonical`, `default_style`, `allow_unicode`, `sort_base_mapping_type_on_output`, `compact_seq_seq`, `compact_seq_map`, `block_seq_indent`, `prefix_colon`, `top_level_colon_align`, `scalar_after_indicator`, `brace_single_entry_mapping_in_flow_sequence` | emitter knobs that exist to re-decide the layout of nodes ruamel could not reproduce. Reproduction makes most of them unnecessary | per-node style: the scalar-string subclasses, `.fa.set_flow_style()`, `.fa.set_block_style()`. Non-ASCII is written as-is, never `\uXXXX`-escaped, unless the source escaped it. Keys are never reordered on dump |
| `HexCapsInt`, `DecimalInt` | `DecimalInt` cannot be dumped in ruamel at all, it raises `RepresenterError` ([D3](differences.md#d3-decimalint-has-no-representer)); capitalisation is a property of the lexeme, which is preserved | `HexInt` for hex; a loaded `0xFF` or `0X1F` keeps its own capitalisation ([D2](differences.md#d2-0x1f-capital-x-does-not-resolve-as-an-integer)) |
| `CommentedMap._ok`, `._ref`, `.update_key_value`, `._yaml_set_kv_line_col`, `CommentedSeq._lst` | private internals of ruamel's implementation | none needed: if you were poking these to fix comment drift, [the comment model](#comments-belong-to-nodes-not-to-positions) is the fix |
| `comment_handling` and the `C_*` slot constants | the `C_*` constants describe the `typ='rtsc'` layout, not the `typ='rt'` one they are usually used with ([A10](differences.md#a10-the-c_-slot-constants-do-not-describe-caitems)) | the four rt slots are listed [above](#the-containers); `yamluna.comments` exports `C_KEY_EOL`, `C_KEY_PRE`, `C_VALUE_EOL`, `C_VALUE_POST` matching the *actual* rt layout |

---

## The porting checklist

```bash
grep -rn "ruamel"                      # the import, and any typ= other than 'rt'
grep -rn "yaml.indent("                # usually deletable
grep -rn "add_representer\|add_constructor\|YAMLObject"   # -> register_class
grep -rn "\.ca\.items\[" | grep "="    # hand-repaired comment tables: delete them
grep -rn "round_trip_load\|safe_load\|\.compose\|\.serialize\|\.scan("
```

Then run your test suite. The failures you should *expect* are golden-file comparisons against
ruamel's output, and any test asserting that a comment ends up where ruamel's index-keyed model put
it. Both are covered above; [Behaviour differences](differences.md) has the measured repro for each
one, so a surprise can be checked against a known entry before you file it as a bug.
