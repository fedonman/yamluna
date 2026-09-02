"""The tag registry: which class a tag names, and what a document writes it as.

Pure Python, with no parser and no emitter. It answers two questions:

* `TagRegistry.plan` takes the classes a document uses and returns the `%TAG` directives
  the document needs, plus the tag string for each class.
* `TagRegistry.resolve` takes a tag as written plus the `%TAG` directives in scope and
  returns the class it names, or `None` when the tag is somebody else's.

Both are pure functions of what is registered, so registration order never changes the
output. Two classes of the same name from different libraries both survive, and a tag that
matches more than one of them raises with every candidate named rather than picking one.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, NamedTuple

from .error import ConstructorError

__all__ = ['ConstructorError', 'Registration', 'TagDirective', 'TagRegistry', 'WirePlan']

# Everything outside this character class is folded to `-` when deriving a `%TAG` handle.
_ILLEGAL_IN_HANDLE: Final = re.compile(r'[^A-Za-z0-9-]+')

# The wire identity of a registered class: `tag:{source}/{tag_name}`, where the source is a
# dotted module-ish path. A tag of any other shape belongs to somebody else and round-trips
# untouched.
_NAMESPACE: Final = re.compile(
    r'tag:([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)/([^/]+)'
)


class TagDirective(NamedTuple):
    """One `%TAG {handle} {prefix}` line, the same pair `yamluna_core::TagDirective` holds."""

    handle: str
    """The handle as written: `!` for the primary handle, `!name!` for a named one."""

    prefix: str
    """The prefix the handle stands for, such as `tag:libx/`."""

    def __str__(self) -> str:
        """Return the directive as a document writes it: `%TAG ! tag:libx/`."""
        return f'%TAG {self.handle} {self.prefix}'


@dataclass(frozen=True, slots=True)
class Registration:
    """One registered class and the wire identity the registry gives it."""

    cls: type
    """The class itself. Re-registering the same path replaces this with the new object."""

    path: str
    """The fully qualified class path, `libx.circuits.Circuit`, and the registry key."""

    tag_name: str
    """The name written after the handle, `Circuit`. A leading `!` is already stripped."""

    source: str
    """The namespace in effect, after any promotion to the full module path."""

    declared_source: str
    """The source as declared: the pinned value, or the root package of the module."""

    pinned: bool
    """The source came from `source=` or `yaml_source`, so it is never promoted."""

    to_yaml: Callable[[Any, Any], int] | None = None
    """The `to_yaml=` hook this class was registered with, ahead of any the class carries."""

    from_yaml: Callable[[Any, Any], Any] | None = None
    """The `from_yaml=` hook this class was registered with, ahead of any the class carries."""

    @property
    def uri(self) -> str:
        """The global tag this class is written as, `tag:libx/Circuit`."""
        return f'tag:{self.source}/{self.tag_name}'


@dataclass(frozen=True, slots=True)
class WirePlan:
    """What a document needs on the wire for the classes it uses."""

    directives: tuple[TagDirective, ...]
    """The `%TAG` lines to write, in emission order, the primary `!` handle first."""

    tags: dict[type, str]
    """The tag string per class, exactly as a node writes it, such as `!liby!Circuit`."""


def _path_of(cls: type) -> str:
    """Return the registry key for `cls`: its module path joined to its qualified name."""
    return f'{cls.__module__}.{cls.__qualname__}'


def _sanitise(source: str) -> str:
    """`source` folded into a legal YAML handle body, `[A-Za-z0-9-]`.

    A source of pure punctuation folds to nothing and comes back as `tag`, so the handle
    is always usable.
    """
    return _ILLEGAL_IN_HANDLE.sub('-', source).strip('-') or 'tag'


def _alloc(base: str, taken: set[str]) -> str:
    """Return `base`, or `base` with the first free digit suffix, and add it to `taken`."""
    handle, n = base, 1
    while handle in taken:
        n += 1
        handle = f'{base}{n}'
    taken.add(handle)
    return handle


def _promote(regs: Iterable[Registration]) -> list[Registration]:
    """Every registration with `source` set to its effective value.

    Two unpinned registrations that declare the same source and the same tag name both move
    to their full module path, so neither can hide the other. A pinned source stays put.
    """
    # Recomputed over the whole record set on every registration, so the result is a
    # function of what is registered and not of the order it arrived in. Re-registering a
    # class under a different source undoes the promotion its old source caused.
    regs = list(regs)
    clashes = Counter((r.declared_source, r.tag_name) for r in regs)
    return [
        replace(r, source=r.cls.__module__)
        if not r.pinned and clashes[r.declared_source, r.tag_name] > 1
        else replace(r, source=r.declared_source)
        for r in regs
    ]


def _split(tag: str) -> tuple[str | None, str]:
    """Split a tag as written into `(handle, suffix)`.

    The handle is `None` when no directive can apply, as for a verbatim `!<uri>` tag or an
    already-resolved absolute one; the suffix is then the whole URI.
    """
    if tag.startswith('!<') and tag.endswith('>'):
        return None, tag[2:-1]
    if not tag.startswith('!'):
        return None, tag
    rest = tag[1:]
    if rest.startswith('!'):
        return '!!', rest[1:]
    head, sep, suffix = rest.partition('!')
    return (f'!{head}!', suffix) if sep else ('!', rest)


class TagRegistry:
    """Which class a tag names, keyed on the fully qualified class path.

    Every `YAML` instance owns one. Because the key is `module.QualName`, registering a
    class replaces only its own entry: two classes called `Circuit` from two libraries stay
    registered side by side, and each is written in the namespace it came from.

    Example:
        ```python
        registry = TagRegistry()
        registry.register_class(Circuit)  # libx.circuits.Circuit
        plan = registry.plan([Circuit])
        plan.directives  # (TagDirective('!', 'tag:libx/'),)
        plan.tags[Circuit]  # '!Circuit'
        registry.resolve('!Circuit', plan.directives).cls is Circuit
        ```

    """

    def __init__(self) -> None:
        """Start a registry with nothing registered in it."""
        # Keyed on the qualified path, never on the tag name. ruamel keys its constructor
        # table on `'!' + cls.__name__`, so two `Circuit` classes overwrite each other and
        # import order decides which one wins. A path key cannot collide.
        self._by_path: dict[str, Registration] = {}

    # -- registration -------------------------------------------------------------

    def register_class(
        self,
        cls: type,
        *,
        tag: str | None = None,
        source: str | None = None,
        to_yaml: Callable[[Any, Any], int] | None = None,
        from_yaml: Callable[[Any, Any], Any] | None = None,
    ) -> type:
        """Register `cls` so it is written with a tag and loaded back as itself.

        Registering a class path twice replaces the earlier entry, so re-running a module
        or reloading it leaves one registration rather than two.

        Args:
            cls: The class to register.
            tag: The name written after the handle. Defaults to `cls.yaml_tag` when the
                class sets one, otherwise `cls.__name__`. A leading `!` is stripped, since
                ruamel spells the attribute `yaml_tag = '!Circuit'`.
            source: The namespace to write the class in. Defaults to `cls.yaml_source` when
                the class sets one, otherwise the root package of `cls.__module__`. A
                source given here or through `yaml_source` is pinned: it keeps its spelling
                even when another class collides with it.
            to_yaml: How to write an instance, as `(representer, obj) -> int`. A plain
                function gets no `cls`, so it takes what the classmethod takes after it.
                It wins over a `to_yaml` the class itself carries. Pass it for a class you
                cannot add one to, such as a type from a C extension.
            from_yaml: How to read one back, as `(constructor, node) -> object`. Wins over
                a `from_yaml` on the class in the same way.

        Returns:
            `cls` itself, so the method also works as a decorator.

        Example:
            ```python
            @registry.register
            class Circuit: ...


            registry.register_class(Decimal, to_yaml=write_decimal, from_yaml=read_decimal)
            ```

        """
        declared = source or getattr(cls, 'yaml_source', None)
        name = tag or getattr(cls, 'yaml_tag', None) or cls.__name__
        record = Registration(
            cls=cls,
            path=_path_of(cls),
            tag_name=name.removeprefix('!'),  # ruamel writes yaml_tag = '!Circuit'
            source=declared or cls.__module__.partition('.')[0],
            declared_source=declared or cls.__module__.partition('.')[0],
            pinned=declared is not None,
            to_yaml=to_yaml,
            from_yaml=from_yaml,
        )
        self._by_path[record.path] = record  # re-registration replaces, never duplicates
        self._by_path = {r.path: r for r in _promote(self._by_path.values())}
        return cls

    register = register_class
    """Bare decorator form of `register_class`: `@yaml.register` above a class."""

    def registrations(self) -> list[Registration]:
        """Every registration, sorted by qualified class path.

        Returns:
            One `Registration` per registered class, each with `source` already promoted.

        """
        return sorted(self._by_path.values(), key=lambda r: r.path)

    def registration_for(self, cls: type) -> Registration | None:
        """Return the registration for `cls`.

        Args:
            cls: The class to look up. Matched on its qualified path, so a class object
                replaced by a module reload still finds the current registration.

        Returns:
            The `Registration`, or `None` when nothing is registered under that path.

        """
        return self._by_path.get(_path_of(cls))

    # -- emitting: classes to %TAG directives and tag strings ----------------------

    def plan(self, classes: Iterable[type]) -> WirePlan:
        """Return the `%TAG` directives and tag strings a document using `classes` needs.

        Repeats count. Pass one entry per node and the most-used source wins the primary
        `!` handle; pass a set and the source with the most distinct classes wins. Ties
        break on the source name, so the output is the same whatever order `classes`
        arrives in.

        Args:
            classes: The classes the document uses, one entry per node or one per class.
                Classes that are not registered are ignored: they get no directive and
                no tag.

        Returns:
            A `WirePlan`. Both of its fields are empty when none of `classes` is
            registered, so a document of plain data gets no `%TAG` line at all.

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
            return WirePlan((), {})  # nothing registered, so no %TAG line

        primary, *rest = sorted(counts, key=lambda s: (-counts[s], s))
        handles = {primary: '!'}
        taken: set[str] = set()
        for src in sorted(rest):
            handles[src] = f'!{_alloc(_sanitise(src), taken)}!'
        return WirePlan(
            directives=tuple(
                TagDirective(handles[s], f'tag:{s}/') for s in [primary, *sorted(rest)]
            ),
            tags={cls: handles[r.source] + r.tag_name for cls, r in used.items()},
        )

    # -- loading: tag as written plus directives, to a class -----------------------

    def resolve(
        self,
        tag: str,
        directives: Mapping[str, str] | Iterable[tuple[str, str]] = (),
    ) -> Registration | None:
        """Return the class `tag` names, given the `%TAG` directives in scope.

        Args:
            tag: The tag exactly as the source wrote it: `!Circuit`, `!liby!Circuit`,
                `!<tag:libx/Circuit>`, or an already-resolved absolute tag.
            directives: The `%TAG` directives in scope, as a mapping of handle to prefix or
                as `(handle, prefix)` pairs. Defaults to none in scope.

        Returns:
            The `Registration` the tag names, or `None` when the tag is not this
            registry's and should round-trip exactly as written. A verbatim `!<uri>` tag,
            an already-resolved tag, the secondary `!!` handle, a handle no directive
            declares, and a source no registered class uses all give `None`.

        Raises:
            ConstructorError: The tag resolves into a source this registry has classes in
                but names no class there, or it matches more than one registered class. The
                message names every candidate by qualified path and wire identity and says
                how to disambiguate. The registry never guesses.

        """
        handle, suffix = _split(tag)
        in_scope = dict(directives)
        if handle is not None and handle in in_scope:
            return self._by_uri(in_scope[handle] + suffix, tag)
        if handle == '!' and suffix:
            return self._by_name(suffix, tag)
        return None  # verbatim, absolute, !!secondary, or an undeclared handle

    def _by_uri(self, uri: str, written: str) -> Registration | None:
        """Return the registration an absolute `uri` names.

        `written` is the tag as the source wrote it, and is what any error quotes back.
        """
        match = _NAMESPACE.fullmatch(uri)
        if match is None:
            return None  # not the shape this registry writes
        source, name = match.groups()
        found = self._matching(lambda r: r.source == source and r.tag_name == name)
        if len(found) == 1:
            return found[0]
        if not found:
            # A source this registry has classes in is a source whose spelling mistakes it
            # can see: `!Ghost` beside a registered `tag:libx/Circuit` is a typo, so it is
            # reported rather than guessed at. A source it has never heard of belongs to
            # somebody else's document and round-trips untouched, which is what lets a
            # `YAML()` with an empty registry load a file full of `!Circuit`.
            if any(r.source == source for r in self._by_path.values()):
                msg = (
                    f'unresolved tag {written!r} (= {uri!r}): no class is registered as '
                    f'{name!r} in source {source!r}'
                )
                raise ConstructorError(msg)
            return None
        raise self._ambiguous(written, found)

    def _by_name(self, name: str, written: str) -> Registration | None:
        """Return the one registration whose tag name is `name`, across every source."""
        found = self._matching(lambda r: r.tag_name == name)
        if len(found) == 1:
            return found[0]
        if not found:
            return None  # nothing registered under that name, so it round-trips untouched
        raise self._ambiguous(written, found)

    def _matching(self, pred: Callable[[Registration], bool]) -> list[Registration]:
        """Registrations satisfying `pred`, sorted by path so the order is stable."""
        return sorted((r for r in self._by_path.values() if pred(r)), key=lambda r: r.path)

    @staticmethod
    def _ambiguous(written: str, found: list[Registration]) -> ConstructorError:
        """Return the error for a tag matching several registrations, naming every candidate."""
        candidates = ', '.join(f'{r.path} (= {r.uri})' for r in found)
        return ConstructorError(
            f'ambiguous tag {written!r}: {len(found)} registered candidates: '
            f'{candidates}; yamluna will not guess. Add a %TAG directive naming the '
            f"source (e.g. '%TAG ! tag:{found[0].source}/') or re-register with an "
            f'explicit source= to disambiguate.'
        )
