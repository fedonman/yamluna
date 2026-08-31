"""Namespace-aware tag registry — DESIGN.md §5.

Pure Python, no parser and no emitter. The module answers exactly two questions:

*  :meth:`TagRegistry.plan` — given the classes used in a document, which ``%TAG``
   directives does it need and what tag string does each node get?
*  :meth:`TagRegistry.resolve` — given a tag as written plus the ``%TAG`` directives
   in scope, which registered class is it (if any)?

Both are pure functions of the registry contents, so registration order can never
change the output. That is the ruamel bug this module exists to not have: ruamel keys
its constructor table on ``'!' + cls.__name__``, so two ``Circuit`` classes overwrite
each other and import order decides which one silently wins.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Final, NamedTuple

try:
    from .errors import ConstructorError
except ImportError:  # ponytail: errors.py lands with the loader; delete this fallback then.

    class ConstructorError(Exception):  # type: ignore[no-redef]
        """Stand-in until ``yamluna.errors`` exists."""


__all__ = ["ConstructorError", "Registration", "TagDirective", "TagRegistry", "WirePlan"]

#: Everything outside this class is folded to ``-`` when deriving a ``%TAG`` handle.
_ILLEGAL_IN_HANDLE: Final = re.compile(r"[^A-Za-z0-9-]+")

#: The wire identity of a registered class: ``tag:{source}/{tag_name}`` where the source
#: is a dotted module-ish path. Anything else is somebody else's tag and round-trips
#: untouched (§5.4.3).
_NAMESPACE: Final = re.compile(
    r"tag:([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)/([^/]+)"
)


class TagDirective(NamedTuple):
    """One ``%TAG {handle} {prefix}`` line. Mirrors ``yamluna_core::TagDirective``."""

    handle: str  # "!" or "!name!"
    prefix: str  # "tag:libx/"

    def __str__(self) -> str:
        return f"%TAG {self.handle} {self.prefix}"


@dataclass(frozen=True, slots=True)
class Registration:
    """One registered class. ``path`` is the registry key, so nothing can overwrite."""

    cls: type
    path: str  # "libx.circuits.Circuit"
    tag_name: str  # "Circuit"
    source: str  # effective source, after promotion (§5.2)
    declared_source: str  # root package, or the pinned value
    pinned: bool  # explicit source= / yaml_source: never promoted

    @property
    def uri(self) -> str:
        """The global tag this class is written as: ``tag:libx/Circuit``."""
        return f"tag:{self.source}/{self.tag_name}"


@dataclass(frozen=True, slots=True)
class WirePlan:
    """What a document needs on the wire for the classes it uses."""

    directives: tuple[TagDirective, ...]  # in emission order, primary "!" first
    tags: dict[type, str]  # class -> tag exactly as written, e.g. "!liby!Circuit"


def _path_of(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _sanitise(source: str) -> str:
    """A source folded into a legal YAML handle body (``[A-Za-z0-9-]``)."""
    return _ILLEGAL_IN_HANDLE.sub("-", source).strip("-") or "tag"


def _alloc(base: str, taken: set[str]) -> str:
    """`base`, or `base` with the first free digit suffix (§5.3 deduplication)."""
    handle, n = base, 1
    while handle in taken:
        n += 1
        handle = f"{base}{n}"
    taken.add(handle)
    return handle


def _promote(regs: Iterable[Registration]) -> list[Registration]:
    """§5.2: a ``(source, tag_name)`` collision promotes every *unpinned* member of the
    colliding group to its full module path. Pure function of the records, recomputed on
    every registration, so the result never depends on registration order.
    """
    regs = list(regs)
    clashes = Counter((r.declared_source, r.tag_name) for r in regs)
    return [
        replace(r, source=r.cls.__module__)
        if not r.pinned and clashes[r.declared_source, r.tag_name] > 1
        else replace(r, source=r.declared_source)
        for r in regs
    ]


def _split(tag: str) -> tuple[str | None, str]:
    """Split a tag as written into ``(handle, suffix)``.

    The handle is ``None`` when no directive can apply — a verbatim ``!<uri>`` tag or an
    already-resolved absolute one; the suffix is then the whole URI.
    """
    if tag.startswith("!<") and tag.endswith(">"):
        return None, tag[2:-1]
    if not tag.startswith("!"):
        return None, tag
    rest = tag[1:]
    if rest.startswith("!"):
        return "!!", rest[1:]
    head, sep, suffix = rest.partition("!")
    return (f"!{head}!", suffix) if sep else ("!", rest)


class TagRegistry:
    """Registration keyed on the fully qualified class path (§5.2).

    One registry per :class:`~yamluna.YAML` instance.
    """

    def __init__(self) -> None:
        self._by_path: dict[str, Registration] = {}

    # -- registration (§5.2, §5.5) ------------------------------------------------

    def register_class(
        self, cls: type, *, tag: str | None = None, source: str | None = None
    ) -> type:
        """Register `cls`. Returns `cls`, so it also works as a decorator.

        `tag` overrides the tag name (default ``cls.yaml_tag`` or ``cls.__name__``);
        `source` pins the namespace (default ``cls.yaml_source`` or the root package)
        and a pinned source is never promoted.
        """
        declared = source or getattr(cls, "yaml_source", None)
        name = tag or getattr(cls, "yaml_tag", None) or cls.__name__
        record = Registration(
            cls=cls,
            path=_path_of(cls),
            tag_name=name.removeprefix("!"),  # ruamel writes yaml_tag = '!Circuit'
            source=declared or cls.__module__.partition(".")[0],
            declared_source=declared or cls.__module__.partition(".")[0],
            pinned=declared is not None,
        )
        self._by_path[record.path] = record  # re-registration replaces, never duplicates
        self._by_path = {r.path: r for r in _promote(self._by_path.values())}
        return cls

    #: Bare decorator form: ``@yaml.register``.
    register = register_class

    def registrations(self) -> list[Registration]:
        """Every registration, sorted by qualified path."""
        return sorted(self._by_path.values(), key=lambda r: r.path)

    def registration_for(self, cls: type) -> Registration | None:
        return self._by_path.get(_path_of(cls))

    # -- (a) emitting: classes -> %TAG directives + tag strings (§5.3) -------------

    def plan(self, classes: Iterable[type]) -> WirePlan:
        """The wire format for a document using `classes`.

        Repeats count: pass one entry per node and the most-used source wins the primary
        ``!`` handle; pass a set and the source with the most distinct classes wins.
        Ties break on the source name, so the output is order-independent either way.
        Unregistered classes are ignored — they get no directive and no tag.
        """
        used: dict[type, Registration] = {}
        counts: Counter[str] = Counter()
        for cls in classes:
            record = self._by_path.get(_path_of(cls))
            if record is None:
                continue
            used[cls] = record
            counts[record.source] += 1
        if not counts:
            return WirePlan((), {})  # no registered classes -> no %TAG line (§5.3)

        primary, *rest = sorted(counts, key=lambda s: (-counts[s], s))
        handles = {primary: "!"}
        taken: set[str] = set()
        for src in sorted(rest):
            handles[src] = f"!{_alloc(_sanitise(src), taken)}!"
        return WirePlan(
            directives=tuple(
                TagDirective(handles[s], f"tag:{s}/") for s in [primary, *sorted(rest)]
            ),
            tags={cls: handles[r.source] + r.tag_name for cls, r in used.items()},
        )

    # -- (b) loading: tag as written + directives -> class (§5.4) ------------------

    def resolve(
        self,
        tag: str,
        directives: Mapping[str, str] | Iterable[tuple[str, str]] = (),
    ) -> Registration | None:
        """The class `tag` names, or ``None`` when it is not ours and round-trips as-is.

        Raises :class:`ConstructorError` for a tag that claims our namespace but matches
        no registration, and for an ambiguous bare tag — never guesses (§5.4).
        """
        handle, suffix = _split(tag)
        in_scope = dict(directives)
        if handle is not None and handle in in_scope:
            return self._by_uri(in_scope[handle] + suffix, tag)
        if handle == "!" and suffix:
            return self._by_name(suffix, tag)
        return None  # verbatim, absolute, !!secondary, or an undeclared handle

    def _by_uri(self, uri: str, written: str) -> Registration | None:
        match = _NAMESPACE.fullmatch(uri)
        if match is None:
            return None  # not our namespace (§5.4.3)
        source, name = match.groups()
        found = self._matching(lambda r: r.source == source and r.tag_name == name)
        if len(found) == 1:
            return found[0]
        if not found:
            raise ConstructorError(
                f"unresolved tag {written!r} (= {uri!r}): no class is registered as "
                f"{name!r} in source {source!r}"
            )
        raise self._ambiguous(written, found)

    def _by_name(self, name: str, written: str) -> Registration | None:
        found = self._matching(lambda r: r.tag_name == name)
        if len(found) == 1:
            return found[0]
        if not found:
            return None  # unregistered: round-trips untouched (§5.4.3)
        raise self._ambiguous(written, found)

    def _matching(self, pred: Callable[[Registration], bool]) -> list[Registration]:
        return sorted((r for r in self._by_path.values() if pred(r)), key=lambda r: r.path)

    @staticmethod
    def _ambiguous(written: str, found: list[Registration]) -> ConstructorError:
        candidates = ", ".join(f"{r.path} (= {r.uri})" for r in found)
        return ConstructorError(
            f"ambiguous tag {written!r}: {len(found)} registered candidates: "
            f"{candidates}; yamluna will not guess. Add a %TAG directive naming the "
            f"source (e.g. '%TAG ! tag:{found[0].source}/') or re-register with an "
            f"explicit source= to disambiguate."
        )
