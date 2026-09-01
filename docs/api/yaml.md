# The YAML object

`YAML()` is the entry point and, for most programs, the only name you import. One instance
carries the emitter settings, the tag registry, and the records of the stream it loaded last.
`typ='rt'` is the only mode there is, so `YAML()` with no arguments is what you want; any
other `typ` raises `ValueError`.

The settings are plain attributes you assign after construction rather than constructor
arguments. Each one is documented below with what it does to the output;
[Settings](../guide/settings.md) shows them working on a real document.

::: yamluna.YAML

## One registry for a whole application

Each `YAML()` builds its own empty [`TagRegistry`](registry.md#yamluna.TagRegistry), so two
instances in one process never see each other's registrations. When you want the opposite,
one registry an application registers everything with, use these two names and construct the
instance as `YAML(registry=default_registry)`. A plain `YAML()` never consults it.

[Custom classes and tags](../guide/custom-classes.md) covers which of the two you want.

::: yamluna.register_class

::: yamluna.default_registry
