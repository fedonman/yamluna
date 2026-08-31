"""Tag registry — DESIGN.md §5.

Run: PYTHONPATH=python .venv/bin/pytest tests/test_registry.py
"""

import re

import pytest

from yamluna.registry import ConstructorError, TagDirective, TagRegistry


def cls_in(module: str, name: str, qualname: str | None = None, **attrs: object) -> type:
    """A class that claims to live in `module` — no fixture packages on disk."""
    made = type(name, (), dict(attrs))
    made.__module__ = module
    made.__qualname__ = qualname or name
    return made


HANDLE = re.compile(r"^!([A-Za-z0-9-]+!)?$")  # what YAML actually allows


# -- §5.3 wire format ------------------------------------------------------------


def test_single_source_gets_the_bare_primary_handle():
    circuit = cls_in("libx.circuits", "Circuit")
    reg = TagRegistry()
    reg.register_class(circuit)

    plan = reg.plan([circuit])

    assert plan.directives == (TagDirective("!", "tag:libx/"),)
    assert str(plan.directives[0]) == "%TAG ! tag:libx/"
    assert plan.tags == {circuit: "!Circuit"}


def test_no_registered_classes_means_no_directives():
    reg = TagRegistry()
    reg.register_class(cls_in("libx", "Circuit"))

    assert reg.plan([]).directives == ()
    assert reg.plan([dict, cls_in("elsewhere", "Thing")]).directives == ()


def test_two_libraries_same_class_name_both_survive():
    x = cls_in("libx.circuits", "Circuit")
    y = cls_in("liby.core", "Circuit")
    reg = TagRegistry()
    reg.register_class(x)
    reg.register_class(y)

    plan = reg.plan([x, y])

    assert plan.directives == (
        TagDirective("!", "tag:libx/"),
        TagDirective("!liby!", "tag:liby/"),
    )
    assert plan.tags == {x: "!Circuit", y: "!liby!Circuit"}

    # plan and resolve are inverses: every tag written comes back as its own class.
    for cls, tag in plan.tags.items():
        assert reg.resolve(tag, plan.directives).cls is cls


def test_most_used_source_wins_the_primary_handle():
    x = cls_in("libx", "Circuit")
    y = cls_in("liby", "Circuit")
    reg = TagRegistry()
    reg.register_class(x)
    reg.register_class(y)

    # one node each: tie broken on the source name.
    assert reg.plan([x, y]).directives[0] == TagDirective("!", "tag:libx/")
    # three liby nodes to one libx: liby takes the bare handle.
    plan = reg.plan([x, y, y, y])
    assert plan.directives[0] == TagDirective("!", "tag:liby/")
    assert plan.tags == {x: "!libx!Circuit", y: "!Circuit"}


# -- §5.2 promotion --------------------------------------------------------------


def test_two_modules_of_one_library_promote_to_full_module_paths():
    a = cls_in("libx.circuits", "Circuit")
    b = cls_in("libx.gates", "Circuit")
    reg = TagRegistry()
    reg.register_class(a)
    reg.register_class(b)

    assert reg.registration_for(a).source == "libx.circuits"
    assert reg.registration_for(b).source == "libx.gates"

    plan = reg.plan([a, b])
    assert plan.directives == (
        TagDirective("!", "tag:libx.circuits/"),
        TagDirective("!libx-gates!", "tag:libx.gates/"),
    )
    assert reg.resolve("!Circuit", plan.directives).cls is a
    assert reg.resolve("!libx-gates!Circuit", plan.directives).cls is b


def test_promotion_only_fires_on_collision():
    a = cls_in("libx.circuits", "Circuit")
    b = cls_in("libx.gates", "Gate")
    reg = TagRegistry()
    reg.register_class(a)
    reg.register_class(b)

    assert {r.source for r in reg.registrations()} == {"libx"}
    assert reg.plan([a, b]).directives == (TagDirective("!", "tag:libx/"),)


def test_explicit_source_is_pinned_and_never_promoted():
    pinned = cls_in("libx.circuits", "Circuit")
    other = cls_in("libx.gates", "Circuit")
    reg = TagRegistry()
    reg.register_class(pinned, source="libx")
    reg.register_class(other)

    assert reg.registration_for(pinned).source == "libx"  # stayed put
    assert reg.registration_for(other).source == "libx.gates"  # moved out of the way
    assert reg.resolve("!Circuit", [("!", "tag:libx/")]).cls is pinned


def test_yaml_source_and_yaml_tag_class_attributes_are_honoured():
    circuit = cls_in("libx.circuits", "Circuit", yaml_tag="!Circ", yaml_source="qilisdk")
    reg = TagRegistry()
    reg.register_class(circuit)

    record = reg.registration_for(circuit)
    assert (record.tag_name, record.source, record.pinned) == ("Circ", "qilisdk", True)
    assert record.uri == "tag:qilisdk/Circ"
    assert reg.plan([circuit]).tags == {circuit: "!Circ"}


def test_explicit_tag_overrides_the_class_name():
    circuit = cls_in("libx", "Circuit")
    reg = TagRegistry()
    reg.register_class(circuit, tag="Circ")

    assert reg.plan([circuit]).tags == {circuit: "!Circ"}
    assert reg.resolve("!Circ").cls is circuit
    assert reg.resolve("!Circuit") is None


# -- §5.4 loading ----------------------------------------------------------------


def test_bare_tag_with_one_candidate_resolves():
    circuit = cls_in("libx.circuits", "Circuit")
    reg = TagRegistry()
    reg.register_class(circuit)

    assert reg.resolve("!Circuit").cls is circuit  # hand-written file, no directive


def test_bare_tag_with_two_candidates_never_guesses():
    x = cls_in("libx.circuits", "Circuit")
    y = cls_in("liby.core", "Circuit")
    reg = TagRegistry()
    reg.register_class(x)
    reg.register_class(y)

    with pytest.raises(ConstructorError) as excinfo:
        reg.resolve("!Circuit")

    message = str(excinfo.value)
    assert "libx.circuits.Circuit" in message  # both fully qualified candidates
    assert "liby.core.Circuit" in message
    assert "tag:libx/Circuit" in message  # and both wire identities
    assert "tag:liby/Circuit" in message
    assert "%TAG" in message  # how to disambiguate
    assert "source=" in message
    assert "!Circuit" in message


def test_ambiguity_survives_pinning_two_classes_to_one_source():
    x = cls_in("libx.circuits", "Circuit")
    y = cls_in("liby.core", "Circuit")
    reg = TagRegistry()
    reg.register_class(x, source="shared")
    reg.register_class(y, source="shared")

    with pytest.raises(ConstructorError, match="ambiguous"):
        reg.resolve("!Circuit", [("!", "tag:shared/")])


def test_tag_in_our_namespace_with_no_registration_is_an_error():
    reg = TagRegistry()
    reg.register_class(cls_in("libx.circuits", "Circuit"))

    with pytest.raises(ConstructorError) as excinfo:
        reg.resolve("!Ghost", [("!", "tag:libx/")])

    assert "tag:libx/Ghost" in str(excinfo.value)


@pytest.mark.parametrize(
    ("tag", "directives"),
    [
        ("!Unknown", ()),  # bare, nothing registered under that name
        ("!!str", ()),  # the secondary handle is never ours
        ("!!str", [("!", "tag:libx/")]),  # ... even with our primary in scope
        ("!<tag:example.com,2002:thing>", ()),  # verbatim
        ("tag:example.com,2002:thing", ()),  # already resolved
        ("!local", [("!e!", "tag:example.com,2002:")]),  # handle not in scope
        ("!e!thing", [("!e!", "tag:example.com,2002:")]),  # somebody else's namespace
        ("!Circuit", [("!", "!private-")]),  # local prefix, not our shape
    ],
)
def test_unregistered_tags_round_trip_untouched(tag, directives):
    reg = TagRegistry()
    reg.register_class(cls_in("libx.circuits", "Circuit"))

    assert reg.resolve(tag, directives) is None


def test_directives_accept_a_mapping_too():
    circuit = cls_in("libx", "Circuit")
    reg = TagRegistry()
    reg.register_class(circuit)

    assert reg.resolve("!c!Circuit", {"!c!": "tag:libx/"}).cls is circuit


# -- order independence: the ruamel bug ------------------------------------------


def test_registration_order_does_not_change_the_wire_output():
    a = cls_in("libx.circuits", "Circuit")
    b = cls_in("liby.core", "Circuit")
    c = cls_in("libz.things", "Gate")

    forward, backward = TagRegistry(), TagRegistry()
    for cls in (a, b, c):
        forward.register_class(cls)
    for cls in (c, b, a):
        backward.register_class(cls)

    assert forward.plan([a, b, c]) == backward.plan([a, b, c])
    assert forward.registrations() == backward.registrations()
    for cls in (a, b, c):
        assert forward.registration_for(cls).uri == backward.registration_for(cls).uri


def test_promotion_order_does_not_change_the_wire_output():
    a = cls_in("libx.circuits", "Circuit")
    b = cls_in("libx.gates", "Circuit")

    forward, backward = TagRegistry(), TagRegistry()
    forward.register_class(a)
    forward.register_class(b)
    backward.register_class(b)
    backward.register_class(a)

    assert forward.plan([a, b]) == backward.plan([a, b])
    assert forward.plan([a, b]).tags[a] == "!Circuit"


# -- idempotence and replacement -------------------------------------------------


def test_re_registering_the_same_class_is_idempotent():
    circuit = cls_in("libx.circuits", "Circuit")
    reg = TagRegistry()
    reg.register_class(circuit)
    reg.register_class(circuit)
    reg.register_class(circuit)

    assert len(reg.registrations()) == 1
    assert reg.registration_for(circuit).source == "libx"  # not self-collided
    assert reg.resolve("!Circuit").cls is circuit


def test_a_reloaded_module_replaces_rather_than_duplicates():
    old = cls_in("libx.circuits", "Circuit")
    new = cls_in("libx.circuits", "Circuit")  # same path, different object
    assert old is not new

    reg = TagRegistry()
    reg.register_class(old)
    reg.register_class(new)

    assert len(reg.registrations()) == 1
    assert reg.resolve("!Circuit").cls is new
    assert reg.registration_for(old).cls is new


def test_decorator_form_returns_the_class():
    reg = TagRegistry()

    @reg.register
    class Circuit:
        pass

    assert reg.registration_for(Circuit) is not None
    assert reg.resolve("!Circuit").cls is Circuit


# -- §5.3 handle sanitisation ----------------------------------------------------


def test_handles_are_sanitised_to_legal_yaml():
    primary = cls_in("aaa", "Anchor")
    awkward = cls_in("whatever", "Circuit", yaml_source="my_lib.sub_mod")
    reg = TagRegistry()
    reg.register_class(primary)
    reg.register_class(awkward)

    plan = reg.plan([primary, awkward])

    assert plan.directives[1] == TagDirective("!my-lib-sub-mod!", "tag:my_lib.sub_mod/")
    assert plan.tags[awkward] == "!my-lib-sub-mod!Circuit"
    assert all(HANDLE.match(d.handle) for d in plan.directives)
    assert reg.resolve(plan.tags[awkward], plan.directives).cls is awkward


def test_sources_that_sanitise_alike_still_get_distinct_handles():
    primary = cls_in("aaa", "Anchor")  # most-used: takes the bare "!" out of the way
    underscore = cls_in("m1", "Circuit", yaml_source="my_lib")
    dotted = cls_in("m2", "Gate", yaml_source="my.lib")
    reg = TagRegistry()
    for cls in (primary, underscore, dotted):
        reg.register_class(cls)

    plan = reg.plan([primary, primary, underscore, dotted])
    handles = [d.handle for d in plan.directives]

    assert handles[0] == "!"
    assert len(set(handles)) == len(handles)
    assert all(HANDLE.match(h) for h in handles)
    assert set(handles[1:]) == {"!my-lib!", "!my-lib2!"}
    for cls in (underscore, dotted):
        assert reg.resolve(plan.tags[cls], plan.directives).cls is cls


def test_a_source_of_pure_punctuation_still_yields_a_usable_handle():
    primary = cls_in("aaa", "Anchor")
    weird = cls_in("m1", "Circuit", yaml_source="___")
    reg = TagRegistry()
    reg.register_class(primary)
    reg.register_class(weird)

    plan = reg.plan([primary, weird])

    assert all(HANDLE.match(d.handle) for d in plan.directives)
    assert reg.resolve(plan.tags[weird], plan.directives).cls is weird
