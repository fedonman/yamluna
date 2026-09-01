# Tag registry

The registry answers two questions and nothing else. `TagRegistry.plan` takes the classes a
document uses and returns the `%TAG` directives that document needs plus the tag string for
each class; `TagRegistry.resolve` takes a tag as written plus the directives in scope and
returns the class it names, or `None` when the tag belongs to somebody else. Both are pure
functions of what is registered, so registration order never changes the output.

It is pure Python with no parser and no emitter in it, which means you can build one,
register classes with it and inspect what a document would look like without the Rust
extension built.

Registrations are keyed on the fully qualified class path rather than on the class name, so
two libraries that both define a `Circuit` both survive. This is the one place yamluna is
deliberately not ruamel-compatible; [Custom classes and tags](../guide/custom-classes.md) has
the rules and the runnable example, and [Behaviour differences](../migrating/differences.md)
has the measurement of what ruamel does instead.

::: yamluna.TagRegistry

::: yamluna.Registration

::: yamluna.TagDirective

::: yamluna.WirePlan
