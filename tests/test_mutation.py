"""The headline bug fix: comments do not drift when the tree is mutated.

``yamluna`` keys trivia on **node identity** and projects ``.ca.items`` on access
(``DESIGN.md`` §2.1).  ``ruamel.yaml`` keys it on the mapping key / sequence index and
glues an own-line comment into the *previous* sibling's end-of-line ``CommentToken``, so
every structural edit moves prose onto data it was never about
(``docs/DIVERGENCES.md`` A1-A6, ``docs/RUAMEL-BEHAVIOR.md`` §3).

Two kinds of test live here.

1. **The A1-A6 repros**, asserted as exact emitted bytes, because the divergence
   documents give exact expected output.  Each one is paired with a ``ruamel`` assertion
   of the *wrong* output that document records: that pins the divergence as intentional
   and fails loudly the day yamluna starts agreeing with ruamel again.
2. **The whole mutating API**, asserted through :func:`attach` -- a reader that says, of
   the emitted text alone, which comment describes which element.  The invariant is
   mechanical and the same for every mutation:

       a surviving element keeps exactly the comments it had, a deleted element takes
       its comments with it and touches no neighbour's, and a new element arrives bare.

   :func:`surviving` spells that out, so each test says only which elements went and
   which arrived.

Run::

    PYTHONPATH=python .venv/bin/pytest tests/test_mutation.py -q
"""

from __future__ import annotations

import io
from typing import Any

import pytest

yamluna = pytest.importorskip("yamluna", reason="python/yamluna is not importable")
pytest.importorskip(
    "yamluna._yamluna", reason="extension not built yet: maturin develop --uv"
)

from yamluna.comments import C_ELEM_EOL, C_ELEM_PRE, C_VALUE_EOL  # noqa: E402

try:
    import ruamel.yaml as ruamel
except ImportError:  # pragma: no cover - the oracle is optional
    ruamel = None  # type: ignore[assignment]

needs_ruamel = pytest.mark.skipif(ruamel is None, reason="ruamel.yaml is not installed")


# --------------------------------------------------------------------------- harness


def _rt(lib: Any) -> Any:
    """The ordinary round-trip recipe, identical for both libraries."""
    yaml = lib.YAML()
    yaml.preserve_quotes = True
    return yaml


def load(text: str, lib: Any = yamluna) -> Any:
    return _rt(lib).load(text)


def dump(node: Any, lib: Any = yamluna) -> str:
    buf = io.StringIO()
    _rt(lib).dump(node, buf)
    return buf.getvalue()


def mutated(text: str, mutate: Any, lib: Any = yamluna) -> str:
    """``load -> mutate -> dump``, the operation every test here measures."""
    node = load(text, lib)
    mutate(node)
    return dump(node, lib)


def attach(text: str) -> dict[str, tuple[tuple[str, ...], str | None]]:
    """``{element: (own-line comments above it, its end-of-line comment)}``.

    Read out of the emitted text rather than out of ``.ca``, because "which comment
    describes which element" is a property of the document a human opens, not of the
    projection -- a test that read ``.ca`` would pass on a store that is right and an
    emitter that is wrong.

    The element label is the line's content with the ``- `` indicator and the comment
    stripped, and cut at the ``:`` of a mapping key, so ``- one  # eol`` labels as
    ``one`` and ``alpha: 1  # eol`` labels as ``alpha``.  Comments with no element left
    below them land under ``END``.

    A line that is a bare ``-`` is a dash whose value the emitter pushed onto the next
    line; it is skipped so that this reader measures attachment only.  The layout defect
    itself is asserted separately, by
    :func:`test_no_mutation_strands_a_dash_from_its_value`.
    """
    out: dict[str, tuple[tuple[str, ...], str | None]] = {}
    pending: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "-":
            continue
        if stripped.startswith("#"):
            pending.append(stripped)
            continue
        body, hashed, rest = stripped.partition("#")
        body = body.strip()
        if body.startswith("- "):
            body = body[2:].strip()
        label = body.partition(":")[0].strip() if ":" in body else body
        out[label] = (tuple(pending), ("#" + rest).strip() if hashed else None)
        pending = []
    out["END"] = (tuple(pending), None)
    return out


def surviving(
    base: dict[str, tuple[tuple[str, ...], str | None]],
    *,
    gone: set[str] = frozenset(),  # type: ignore[assignment]
    added: set[str] = frozenset(),  # type: ignore[assignment]
) -> dict[str, tuple[tuple[str, ...], str | None]]:
    """The attachment map a mutation must produce: the invariant, spelled out once.

    Everything that is still there keeps exactly what it had, everything in `gone` took
    its comments with it, and everything in `added` arrives with none.
    """
    out = {k: v for k, v in base.items() if k not in gone}
    for k in added:
        out[k] = ((), None)
    return out


# --------------------------------------------------------------------------- documents

#: ``docs/DIVERGENCES.md`` A2/A3 and ``docs/RUAMEL-BEHAVIOR.md`` §3.1-§3.3.
SEQ = "# about one\n- one\n# about two\n- two\n# about three\n- three\n"

#: ``docs/DIVERGENCES.md`` A4/A5 and ``docs/RUAMEL-BEHAVIOR.md`` §3.5-§3.8.
MAP = (
    "# about alpha\n"
    "alpha: 1   # eol alpha\n"
    "# about beta\n"
    "beta: 2    # eol beta\n"
    "# about gamma\n"
    "gamma: 3   # eol gamma\n"
)

#: ``docs/DIVERGENCES.md`` A6 -- end-of-line comments only, which is where ruamel's
#: ``sort`` is right and its ``reverse`` is wrong.
EOL_SEQ = "- a  # ca\n- b  # cb\n- c  # cc\n- d  # cd\n"

#: ``docs/RUAMEL-BEHAVIOR.md`` §1.5, the canonical "a comment in every position".
POSITIONS_SEQ = (
    "# doc comment\n"
    "# before first item\n"
    "- one    # eol on one\n"
    "# between one and two\n"
    "- two\n"
    "# between two and three\n"
    "- three  # eol on three\n"
    "# trailing\n"
)

#: A mapping with a comment in every position the model has a slot for: above an entry,
#: at the end of a value's line, at the end of a key's line (the "between key and value"
#: slot), above the first key of a nested collection, and after the last entry.
EVERY = (
    "# before alpha\n"
    "alpha: 1        # eol alpha\n"
    "# before beta\n"
    "beta:           # eol on beta\n"
    "  # before inner\n"
    "  inner: 2      # eol inner\n"
    "# before gamma\n"
    "gamma: 3        # eol gamma\n"
    "# trailing\n"
)

#: The sequence of the same shape.
EVERY_SEQ = (
    "# before one\n"
    "- one          # eol one\n"
    "# before two\n"
    "- two          # eol two\n"
    "# before three\n"
    "- three        # eol three\n"
    "# trailing\n"
)

EVERY_BASE = attach(EVERY)
EVERY_SEQ_BASE = attach(EVERY_SEQ)


#: The first own-line comment of a collection is filed on the collection
#: (``Trivia4::inner``) instead of on the first child (``before``), against
#: ``DESIGN.md`` §2.2 rule 2 -- ``crates/yamluna-core/src/loader.rs``, ``take_before``.
#: Every mutation that deletes or moves a *first* element therefore still drifts, which
#: is exactly the ruamel defect DIVERGENCES A2/A3/A5/A6 says we do not have.
first_child = pytest.mark.xfail(
    reason="loader.rs take_before() files the first child's own-line comment on the "
    "collection's `inner` slot, not on the child's `before`; DESIGN 2.2 rule 2 and "
    "DIVERGENCES A2/A3/A5 require `before`",
    strict=True,
)

#: A node the user inserted has no recorded position, which desyncs the writer; the next
#: node that is followed by an own-line comment is then emitted as ``-\n  value``.
#: ``crates/yamluna-core/src/emitter/{mod,layout}.rs``.
dash_layout = pytest.mark.xfail(
    reason="after an insertion the emitter strands the dash of the item preceding an "
    "own-line comment: `-\\n  value` instead of `- value`",
    strict=True,
)


# ======================================================================== A1: the model


def test_a1_own_line_comment_is_stored_on_the_node_it_describes() -> None:
    """A1: trivia hangs off the node, in its own slot -- not glued to a sibling."""
    seq = load(POSITIONS_SEQ)
    items = seq.ca.items

    # item 0's end-of-line comment is *only* its own end-of-line comment.
    assert items[0][C_ELEM_EOL].value.strip() == "# eol on one"
    # ... and the comment that describes item 1 is filed on item 1, in the "before" slot.
    assert [t.value for t in items[1][C_ELEM_PRE]] == ["# between one and two\n"]
    assert items[1][C_ELEM_EOL] is None  # item 1 has no end-of-line comment at all
    assert [t.value for t in items[2][C_ELEM_PRE]] == ["# between two and three\n"]
    assert items[2][C_ELEM_EOL].value.strip() == "# eol on three"
    # The trailing comment is the collection's, and .ca.end round-trips it (A9).
    assert [t.value for t in seq.ca.end] == ["# trailing\n"]


@needs_ruamel
def test_a1_ruamel_glues_it_into_the_previous_sibling() -> None:
    """RUAMEL-BEHAVIOR §1.5: one token per item, holding two items' worth of prose."""
    items = load(POSITIONS_SEQ, ruamel).ca.items

    assert items[0][0].value == "# eol on one\n# between one and two\n"
    # item 1 has no comment of its own, yet carries a token -- the bare leading "\n" is
    # how "no end-of-line comment, but something below" is encoded.
    assert items[1][0].value == "\n# between two and three\n"
    assert items[2][0].value == "# eol on three\n# trailing\n"
    assert all(slot is None for item in items.values() for slot in item[1:])


# ============================================================== A2: CommentedSeq.insert


@first_child
def test_a2_insert_at_the_front_does_not_steal_the_first_item_s_comment() -> None:
    assert mutated(SEQ, lambda s: s.insert(0, "zero")) == (
        "- zero\n"
        "# about one\n"
        "- one\n"
        "# about two\n"
        "- two\n"
        "# about three\n"
        "- three\n"
    )


@dash_layout
def test_a2_insert_in_the_middle_does_not_steal_the_next_item_s_comment() -> None:
    assert mutated(SEQ, lambda s: s.insert(1, "x")) == (
        "# about one\n"
        "- one\n"
        "- x\n"
        "# about two\n"
        "- two\n"
        "# about three\n"
        "- three\n"
    )


def test_a2_insert_attaches_nothing_to_the_new_item() -> None:
    """The weaker statement that holds today: the new item is bare, wherever it lands."""
    assert attach(mutated(SEQ, lambda s: s.insert(1, "x")))["x"] == ((), None)


@needs_ruamel
def test_a2_ruamel_labels_the_new_item_with_its_successor_s_comment() -> None:
    """RUAMEL-BEHAVIOR §3.1: the comment renumbers faithfully and lands on the wrong item."""
    front = attach(mutated(SEQ, lambda s: s.insert(0, "zero"), ruamel))
    assert front["zero"][0] == ("# about one",)  # describes `one`
    assert front["one"][0] == ()  # ... which now has nothing

    middle = attach(mutated(SEQ, lambda s: s.insert(1, "x"), ruamel))
    assert middle["x"][0] == ("# about two",)  # describes `two`
    assert middle["two"][0] == ()


# ================================================================== A3: del seq[i]


@first_child
def test_a3_deleting_the_first_item_takes_its_comment_and_leaves_the_rest() -> None:
    assert mutated(SEQ, lambda s: s.__delitem__(0)) == (
        "# about two\n- two\n# about three\n- three\n"
    )


def test_a3_deleting_a_middle_item_takes_its_comment_and_leaves_the_rest() -> None:
    assert mutated(SEQ, lambda s: s.__delitem__(1)) == (
        "# about one\n- one\n# about three\n- three\n"
    )


def test_a3_deleting_the_last_item_leaves_no_comment_dangling() -> None:
    assert mutated(SEQ, lambda s: s.__delitem__(2)) == (
        "# about one\n- one\n# about two\n- two\n"
    )


@needs_ruamel
def test_a3_ruamel_orphans_one_comment_and_destroys_another() -> None:
    """RUAMEL-BEHAVIOR §3.2/§3.3: both failure directions, at once."""
    # del s[0]: `# about one` survives its item and mislabels `two`; `# about two`,
    # whose item survives, is destroyed -- it lived inside the popped token.
    assert mutated(SEQ, lambda s: s.__delitem__(0), ruamel) == (
        "# about one\n- two\n# about three\n- three\n"
    )
    # del s[1]: `# about two` outlives `two` and now labels `three`.
    assert mutated(SEQ, lambda s: s.__delitem__(1), ruamel) == (
        "# about one\n- one\n# about two\n- three\n"
    )
    # del s[2]: `# about three` is left dangling as the final line of the document.
    assert mutated(SEQ, lambda s: s.__delitem__(2), ruamel) == (
        "# about one\n- one\n# about two\n- two\n# about three\n"
    )


# ======================================================== A4: CommentedMap.__delitem__


def test_a4_deleting_a_key_takes_its_comments_and_leaves_the_neighbours_alone() -> None:
    expected = "# about alpha\nalpha: 1   # eol alpha\n# about gamma\ngamma: 3   # eol gamma\n"
    assert mutated(MAP, lambda m: m.__delitem__("beta")) == expected
    assert mutated(MAP, lambda m: m.pop("beta")) == expected


def test_a4_deleting_the_last_key_leaves_no_comment_dangling() -> None:
    assert mutated(MAP, lambda m: m.__delitem__("gamma")) == (
        "# about alpha\nalpha: 1   # eol alpha\n# about beta\nbeta: 2    # eol beta\n"
    )


@first_child
def test_a4_deleting_the_first_key_takes_its_comment() -> None:
    assert mutated(MAP, lambda m: m.__delitem__("alpha")) == (
        "# about beta\nbeta: 2    # eol beta\n# about gamma\ngamma: 3   # eol gamma\n"
    )


def test_a4_a_deleted_comment_cannot_be_resurrected_by_re_adding_the_key() -> None:
    """The store is owned by the node, so a delete leaves nothing behind to come back."""
    src = "alpha: 1\nbeta: 2  # secret comment about beta\ngamma: 3\n"
    node = load(src)
    del node["beta"]
    assert dump(node) == "alpha: 1\ngamma: 3\n"
    assert "beta" not in node.ca.items  # no stale record, so nothing to resurrect

    node["beta"] = "a brand new unrelated value"
    assert dump(node) == "alpha: 1\ngamma: 3\nbeta: a brand new unrelated value\n"


@needs_ruamel
def test_a4_ruamel_drifts_the_comment_and_keeps_a_stale_record() -> None:
    """RUAMEL-BEHAVIOR §3.5(a): `__delitem__` never looks at `self.ca`."""
    node = load(MAP, ruamel)
    node.pop("beta")
    assert dump(node, ruamel) == (
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
        "# about beta\n"  # now labels gamma; `# about gamma` was destroyed with it
        "gamma: 3   # eol gamma\n"
    )
    assert "beta" in node.ca.items  # unbounded state growth on the common delete path


@needs_ruamel
def test_a4_ruamel_resurrects_a_deleted_comment_onto_an_unrelated_value() -> None:
    """RUAMEL-BEHAVIOR §3.5(b): the stale record re-attaches on the next assignment."""
    src = "alpha: 1\nbeta: 2  # secret comment about beta\ngamma: 3\n"
    node = load(src, ruamel)
    del node["beta"]
    assert dump(node, ruamel) == "alpha: 1\ngamma: 3\n"

    node["beta"] = "a brand new unrelated value"
    assert dump(node, ruamel) == (
        "alpha: 1\ngamma: 3\n"
        "beta: a brand new unrelated value # secret comment about beta\n"
    )


# ============================================================ A5: rename / move_to_end


def test_a5_rename_carries_every_slot_to_the_new_key() -> None:
    assert mutated(MAP, lambda m: m.rename("beta", "BETA")) == (
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
        "# about beta\n"
        "BETA: 2    # eol beta\n"
        "# about gamma\n"
        "gamma: 3   # eol gamma\n"
    )


def test_a5_rename_keeps_the_position_and_the_value() -> None:
    node = load(MAP)
    node.rename("beta", "BETA")
    assert list(node) == ["alpha", "BETA", "gamma"]
    assert node["BETA"] == 2
    assert "beta" not in node.ca.items


def test_a5_rename_to_the_same_name_is_a_no_op() -> None:
    assert mutated(MAP, lambda m: m.rename("beta", "beta")) == MAP


@first_child
def test_a5_move_to_end_takes_the_entry_s_comments_with_it() -> None:
    assert mutated(MAP, lambda m: m.move_to_end("alpha")) == (
        "# about beta\n"
        "beta: 2    # eol beta\n"
        "# about gamma\n"
        "gamma: 3   # eol gamma\n"
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
    )


def test_a5_move_to_end_of_a_later_key_takes_its_comments_with_it() -> None:
    """The same operation on a key that is not the first: correct today."""
    assert mutated(MAP, lambda m: m.move_to_end("beta")) == (
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
        "# about gamma\n"
        "gamma: 3   # eol gamma\n"
        "# about beta\n"
        "beta: 2    # eol beta\n"
    )


@first_child
def test_a5_move_to_front_reorders_the_comments_too() -> None:
    assert mutated(MAP, lambda m: m.move_to_end("gamma", last=False)) == (
        "# about gamma\n"
        "gamma: 3   # eol gamma\n"
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
        "# about beta\n"
        "beta: 2    # eol beta\n"
    )


def test_a5_order_preserving_insert_keeps_the_end_of_line_comment() -> None:
    """``CommentedMap.insert(pos, key, value)`` as a rename: ruamel destroys `# eol beta`."""
    assert mutated(MAP, lambda m: m.insert(1, "BETA", m.pop("beta"))) == (
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
        "BETA: 2\n"
        "# about gamma\n"
        "gamma: 3   # eol gamma\n"
    )


@needs_ruamel
def test_a5_ruamel_scatters_comments_across_the_document() -> None:
    """RUAMEL-BEHAVIOR §3.6/§3.7."""
    # move_to_end: `# about alpha` stays at the top (it lives in ca.comment[1]) and
    # `# about beta` -- glued to alpha's end-of-line token -- travels to the very end.
    assert mutated(MAP, lambda m: m.move_to_end("alpha"), ruamel) == (
        "# about alpha\n"
        "beta: 2    # eol beta\n"
        "# about gamma\n"
        "gamma: 3   # eol gamma\n"
        "alpha: 1   # eol alpha\n"
        "# about beta\n"
    )
    # insert(1, 'BETA', ...) keeps the order and destroys `# eol beta`.
    assert mutated(MAP, lambda m: m.insert(1, "BETA", m.pop("beta")), ruamel) == (
        "# about alpha\n"
        "alpha: 1   # eol alpha\n"
        "# about beta\n"
        "BETA: 2\n"
        "gamma: 3   # eol gamma\n"
    )


@needs_ruamel
def test_a5_ruamel_has_no_rename_at_all() -> None:
    assert not hasattr(load(MAP, ruamel), "rename")


# ================================================================ A6: reverse and sort


def test_a6_reverse_moves_the_comments_with_the_items() -> None:
    assert mutated(EOL_SEQ, lambda s: s.reverse()) == (
        "- d  # cd\n- c  # cc\n- b  # cb\n- a  # ca\n"
    )


def test_a6_sort_reverse_agrees_with_reverse() -> None:
    """Half the list API maintaining the table and half not is the ruamel defect."""
    assert mutated(EOL_SEQ, lambda s: s.sort(reverse=True)) == mutated(
        EOL_SEQ, lambda s: s.reverse()
    )


def test_a6_sort_moves_the_comments_with_the_items() -> None:
    assert mutated(
        "- d  # cd\n- c  # cc\n- b  # cb\n- a  # ca\n", lambda s: s.sort()
    ) == ("- a  # ca\n- b  # cb\n- c  # cc\n- d  # cd\n")


@needs_ruamel
def test_a6_ruamel_reverse_moves_nothing() -> None:
    """RUAMEL-BEHAVIOR §3.4: ``sort`` remaps ``ca.items``, ``reverse`` does not."""
    assert mutated(EOL_SEQ, lambda s: s.sort(reverse=True), ruamel) == (
        "- d  # cd\n- c  # cc\n- b  # cb\n- a  # ca\n"  # correct
    )
    assert mutated(EOL_SEQ, lambda s: s.reverse(), ruamel) == (
        "- d  # ca\n- c  # cb\n- b  # cc\n- a  # cd\n"  # every comment wrong
    )


@first_child
def test_a6_reverse_carries_own_line_comments_too() -> None:
    assert mutated(SEQ, lambda s: s.reverse()) == (
        "# about three\n"
        "- three\n"
        "# about two\n"
        "- two\n"
        "# about one\n"
        "- one\n"
    )


# ================================================== the mutating API, on a full document


def test_the_fixtures_round_trip_byte_identically() -> None:
    """Everything below measures a change against this baseline, so pin the baseline."""
    for src in (SEQ, MAP, EOL_SEQ, POSITIONS_SEQ, EVERY, EVERY_SEQ):
        assert dump(load(src)) == src


def test_a_comment_in_every_position_is_read_back_where_it_was_written() -> None:
    assert EVERY_BASE == {
        "alpha": (("# before alpha",), "# eol alpha"),
        "beta": (("# before beta",), "# eol on beta"),
        "inner": (("# before inner",), "# eol inner"),
        "gamma": (("# before gamma",), "# eol gamma"),
        "END": (("# trailing",), None),
    }


# -- CommentedMap ----------------------------------------------------------------------


def test_map_setitem_on_an_existing_key_keeps_the_entry_s_comments() -> None:
    assert attach(mutated(EVERY, lambda m: m.__setitem__("alpha", 11))) == EVERY_BASE


def test_map_setitem_of_a_new_key_adds_it_bare_and_disturbs_nothing() -> None:
    out = mutated(EVERY, lambda m: m.__setitem__("delta", 4))
    assert attach(out) == surviving(EVERY_BASE, added={"delta"})
    assert list(attach(out)) == ["alpha", "beta", "inner", "gamma", "delta", "END"]


def test_map_setdefault_of_a_new_key_adds_it_bare() -> None:
    assert attach(mutated(EVERY, lambda m: m.setdefault("delta", 4))) == surviving(
        EVERY_BASE, added={"delta"}
    )


def test_map_setdefault_of_an_existing_key_changes_nothing() -> None:
    assert attach(mutated(EVERY, lambda m: m.setdefault("alpha", 99))) == EVERY_BASE


def test_map_delitem_of_a_nested_entry_takes_the_whole_subtree_s_comments() -> None:
    assert attach(mutated(EVERY, lambda m: m.__delitem__("beta"))) == surviving(
        EVERY_BASE, gone={"beta", "inner"}
    )


def test_map_pop_of_a_nested_entry_takes_the_whole_subtree_s_comments() -> None:
    assert attach(mutated(EVERY, lambda m: m.pop("beta"))) == surviving(
        EVERY_BASE, gone={"beta", "inner"}
    )


def test_map_pop_with_a_default_on_a_missing_key_changes_nothing() -> None:
    assert attach(mutated(EVERY, lambda m: m.pop("nope", None))) == EVERY_BASE


def test_map_popitem_takes_the_last_entry_s_comments_and_leaves_the_trailing_one() -> None:
    assert attach(mutated(EVERY, lambda m: m.popitem())) == surviving(
        EVERY_BASE, gone={"gamma"}
    )


@first_child
def test_map_clear_takes_every_entry_s_comments() -> None:
    assert attach(mutated(EVERY, lambda m: m.clear())) == {
        "{}": ((), None),
        "END": (("# trailing",), None),  # the collection's own trailing trivia stays
    }


def test_map_update_keeps_existing_comments_and_adds_new_keys_bare() -> None:
    assert attach(
        mutated(EVERY, lambda m: m.update({"alpha": 11, "delta": 4}))
    ) == surviving(EVERY_BASE, added={"delta"})


def test_map_ior_keeps_existing_comments_and_adds_new_keys_bare() -> None:
    assert attach(mutated(EVERY, lambda m: m.__ior__({"delta": 4}))) == surviving(
        EVERY_BASE, added={"delta"}
    )


def test_map_move_to_end_of_a_later_key_reorders_its_comments_with_it() -> None:
    out = mutated(EVERY, lambda m: m.move_to_end("beta"))
    assert attach(out) == EVERY_BASE
    assert list(attach(out)) == ["alpha", "gamma", "beta", "inner", "END"]


def test_map_rename_of_a_nested_entry_keeps_the_child_s_comments() -> None:
    out = mutated(EVERY, lambda m: m.rename("beta", "BETA"))
    assert attach(out) == {
        **{k: v for k, v in EVERY_BASE.items() if k != "beta"},
        "BETA": EVERY_BASE["beta"],
    }
    assert list(attach(out)) == ["alpha", "BETA", "inner", "gamma", "END"]


def test_map_rename_of_a_nested_key_keeps_that_key_s_comments() -> None:
    assert attach(mutated(EVERY, lambda m: m["beta"].rename("inner", "INNER"))) == {
        **{k: v for k, v in EVERY_BASE.items() if k != "inner"},
        "INNER": EVERY_BASE["inner"],
    }


def test_map_nested_container_replacement_drops_only_the_old_child_s_comments() -> None:
    """The entry's own comments belong to the entry; the child's went with the child."""
    out = mutated(EVERY, lambda m: m.__setitem__("beta", {"other": 9}))
    assert attach(out) == surviving(EVERY_BASE, gone={"inner"}, added={"other"})
    assert list(attach(out)) == ["alpha", "beta", "other", "gamma", "END"]


@needs_ruamel
def test_ruamel_nested_container_replacement_keeps_the_dead_child_s_comment() -> None:
    """... and destroys the *next* entry's, which had nothing to do with the edit."""
    out = attach(mutated(EVERY, lambda m: m.__setitem__("beta", {"other": 9}), ruamel))
    assert out["other"][0] == ("# before inner",)  # describes a mapping that is gone
    assert out["gamma"][0] == ()  # `# before gamma` destroyed


@needs_ruamel
def test_ruamel_appends_new_keys_below_the_trailing_comment() -> None:
    out = mutated(EVERY, lambda m: m.__setitem__("delta", 4), ruamel)
    assert out.endswith("# trailing\ndelta: 4\n")
    assert attach(out)["delta"][0] == ("# trailing",)  # the document's, not delta's


@needs_ruamel
def test_ruamel_clear_destroys_the_trailing_comment() -> None:
    assert mutated(EVERY, lambda m: m.clear(), ruamel) == "# before alpha\n{}\n"


# -- CommentedSeq ----------------------------------------------------------------------


def test_seq_setitem_keeps_the_slot_s_comments() -> None:
    out = mutated(EVERY_SEQ, lambda s: s.__setitem__(1, "TWO"))
    assert attach(out) == {
        **{k: v for k, v in EVERY_SEQ_BASE.items() if k != "two"},
        "TWO": EVERY_SEQ_BASE["two"],
    }
    assert list(attach(out)) == ["one", "TWO", "three", "END"]


def test_seq_append_adds_a_bare_item_and_keeps_the_trailing_comment() -> None:
    out = mutated(EVERY_SEQ, lambda s: s.append("four"))
    assert attach(out) == surviving(EVERY_SEQ_BASE, added={"four"})
    assert list(attach(out)) == ["one", "two", "three", "four", "END"]


def test_seq_extend_adds_bare_items() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.extend(["four", "five"]))) == surviving(
        EVERY_SEQ_BASE, added={"four", "five"}
    )


def test_seq_iadd_adds_bare_items() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.__iadd__(["four"]))) == surviving(
        EVERY_SEQ_BASE, added={"four"}
    )


def test_seq_insert_before_a_commented_item_leaves_that_comment_alone() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.insert(1, "onehalf"))) == surviving(
        EVERY_SEQ_BASE, added={"onehalf"}
    )


def test_seq_delitem_takes_the_item_s_comments() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.__delitem__(1))) == surviving(
        EVERY_SEQ_BASE, gone={"two"}
    )


def test_seq_pop_takes_the_last_item_s_comments() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.pop())) == surviving(
        EVERY_SEQ_BASE, gone={"three"}
    )


def test_seq_pop_returns_the_value() -> None:
    node = load(EVERY_SEQ)
    assert node.pop(1) == "two"


def test_seq_remove_takes_the_removed_item_s_comments() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.remove("two"))) == surviving(
        EVERY_SEQ_BASE, gone={"two"}
    )


@first_child
def test_seq_clear_takes_every_item_s_comments() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.clear())) == {
        "[]": ((), None),
        "END": (("# trailing",), None),
    }


def test_seq_slice_deletion_takes_exactly_those_items_comments() -> None:
    assert attach(mutated(EVERY_SEQ, lambda s: s.__delitem__(slice(1, 3)))) == surviving(
        EVERY_SEQ_BASE, gone={"two", "three"}
    )


def test_seq_slice_assignment_replaces_the_slice_and_its_comments() -> None:
    out = mutated(EVERY_SEQ, lambda s: s.__setitem__(slice(1, 3), ["X"]))
    assert attach(out) == surviving(EVERY_SEQ_BASE, gone={"two", "three"}, added={"X"})
    assert list(attach(out)) == ["one", "X", "END"]


def test_seq_sort_carries_own_line_comments_with_their_items() -> None:
    out = mutated(EVERY_SEQ, lambda s: s.sort())
    assert attach(out) == EVERY_SEQ_BASE
    assert list(attach(out)) == ["one", "three", "two", "END"]


def test_seq_nested_container_replacement_drops_only_the_old_child_s_comments() -> None:
    src = (
        "# before one\n"
        "- one          # eol one\n"
        "# before two\n"
        "- # eol on two\n"
        "  # before deep\n"
        "  - deep       # eol deep\n"
        "# trailing\n"
    )
    assert dump(load(src)) == src
    out = mutated(src, lambda s: s.__setitem__(1, ["fresh"]))
    assert "deep" not in out
    assert attach(out)["one"] == (("# before one",), "# eol one")
    assert attach(out)["END"] == (("# trailing",), None)


@needs_ruamel
def test_ruamel_slice_deletion_strands_the_deleted_items_comments() -> None:
    """RUAMEL-BEHAVIOR §3.9: correct for end-of-line comments, wrong for own-line ones."""
    out = mutated(EVERY_SEQ, lambda s: s.__delitem__(slice(1, 3)), ruamel)
    assert out == "# before one\n- one          # eol one\n# before two\n"


@needs_ruamel
def test_ruamel_append_lands_below_the_trailing_comment() -> None:
    out = mutated(EVERY_SEQ, lambda s: s.append("four"), ruamel)
    assert out.endswith("# trailing\n- four\n")


@needs_ruamel
def test_ruamel_pop_shifts_every_comment_up_one_item() -> None:
    out = attach(mutated(EVERY_SEQ, lambda s: s.pop(), ruamel))
    assert out["one"][1] == "# eol two"  # `one` wearing `two`'s end-of-line comment
    assert out["two"][1] == "# eol three"


# ============================================================= cross-cutting invariants


MUTATIONS: dict[str, tuple[str, Any]] = {
    "map-setitem": (EVERY, lambda m: m.__setitem__("delta", 4)),
    "map-setitem-existing": (EVERY, lambda m: m.__setitem__("alpha", 11)),
    "map-delitem": (EVERY, lambda m: m.__delitem__("beta")),
    "map-pop": (EVERY, lambda m: m.pop("gamma")),
    "map-popitem": (EVERY, lambda m: m.popitem()),
    "map-clear": (EVERY, lambda m: m.clear()),
    "map-update": (EVERY, lambda m: m.update({"delta": 4})),
    "map-ior": (EVERY, lambda m: m.__ior__({"delta": 4})),
    "map-move-to-end": (EVERY, lambda m: m.move_to_end("beta")),
    "map-move-to-front": (EVERY, lambda m: m.move_to_end("gamma", last=False)),
    "map-insert": (EVERY, lambda m: m.insert(1, "delta", 4)),
    "map-rename": (EVERY, lambda m: m.rename("beta", "BETA")),
    "map-replace-nested": (EVERY, lambda m: m.__setitem__("beta", {"other": 9})),
    "seq-setitem": (EVERY_SEQ, lambda s: s.__setitem__(1, "TWO")),
    "seq-insert-front": (EVERY_SEQ, lambda s: s.insert(0, "zero")),
    "seq-insert-middle": (EVERY_SEQ, lambda s: s.insert(1, "onehalf")),
    "seq-append": (EVERY_SEQ, lambda s: s.append("four")),
    "seq-delitem": (EVERY_SEQ, lambda s: s.__delitem__(1)),
    "seq-pop": (EVERY_SEQ, lambda s: s.pop()),
    "seq-remove": (EVERY_SEQ, lambda s: s.remove("two")),
    "seq-clear": (EVERY_SEQ, lambda s: s.clear()),
    "seq-reverse": (EVERY_SEQ, lambda s: s.reverse()),
    "seq-sort": (EVERY_SEQ, lambda s: s.sort()),
    "seq-slice-del": (EVERY_SEQ, lambda s: s.__delitem__(slice(1, 3))),
    "seq-slice-set": (EVERY_SEQ, lambda s: s.__setitem__(slice(1, 3), ["X"])),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_mutation_emits_each_comment_exactly_once(name: str) -> None:
    """No mutation may duplicate a comment, which is how a drift usually starts."""
    src, mutate = MUTATIONS[name]
    lines = [line.strip() for line in mutated(src, mutate).splitlines()]
    comments = [line for line in lines if line.startswith("#")]
    assert len(comments) == len(set(comments)), comments


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_every_mutation_emits_a_document_that_reloads_unchanged(name: str) -> None:
    """The output is well-formed and stable: a second round trip is a no-op."""
    src, mutate = MUTATIONS[name]
    out = mutated(src, mutate)
    assert dump(load(out)) == out


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_no_mutation_invents_a_comment(name: str) -> None:
    """Every comment in the output was in the input; nothing is fabricated."""
    src, mutate = MUTATIONS[name]
    before = {line.strip() for line in src.splitlines() if line.strip().startswith("#")}
    after = {
        line.strip()
        for line in mutated(src, mutate).splitlines()
        if line.strip().startswith("#")
    }
    assert after <= before


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            n,
            marks=dash_layout
            if n in {"seq-insert-front", "seq-insert-middle", "seq-slice-set"}
            else (),
        )
        for n in sorted(MUTATIONS)
    ],
)
def test_no_mutation_strands_a_dash_from_its_value(name: str) -> None:
    """A sequence item is ``- value``, not ``-`` followed by an indented value.

    Purely a layout property, but it is the one the :func:`attach` reader has to look
    past, so it gets asserted here rather than nowhere.
    """
    src, mutate = MUTATIONS[name]
    assert [line for line in mutated(src, mutate).splitlines() if line.strip() == "-"] == []


def test_dumping_does_not_mutate_the_object_graph() -> None:
    """DIVERGENCES A8: ruamel's representer appends to ``ca.comment`` on every dump."""
    node = load(EVERY)
    before = repr(node.ca)
    for _ in range(3):
        dump(node)
    assert repr(node.ca) == before
    assert len(node.ca.comment or []) <= 2  # still `[post, [pre]]`


@needs_ruamel
def test_ruamel_dumping_grows_ca_comment_without_bound() -> None:
    node = load("# lead\na: 1\n", ruamel)
    lengths = []
    for _ in range(3):
        dump(node, ruamel)
        lengths.append(len(node.ca.comment))
    assert lengths == [3, 4, 5]  # started at 2


def test_the_key_eol_slot_survives_a_mutation_of_a_sibling() -> None:
    """The "between key and value" comment is the entry's, not the neighbour's."""
    node = load(EVERY)
    assert node.ca.items["beta"][C_VALUE_EOL].value.strip() == "# eol on beta"
    del node["alpha"]
    assert node.ca.items["beta"][C_VALUE_EOL].value.strip() == "# eol on beta"
    assert "# eol on beta" in dump(node)
