# User guide

Loaded mappings and sequences work like Python dictionaries and lists, with the file's formatting kept alongside them, and registered classes load as their own objects. Start with [reading and writing YAML](load-and-dump.md) if you have not used yamluna before.

## Common tasks

| I want to… | Guide |
| --- | --- |
| Save and load my own classes | [Custom classes](custom-classes.md#register-your-own-class) |
| Store types I do not own, such as `ndarray` or `Decimal` | [Custom classes](custom-classes.md#register-a-class-you-do-not-own) |
| Update a string, file, or multi-document stream | [Read and write YAML](load-and-dump.md) |
| Add comments or move entries with their comments | [Comments](comments.md) |
| Keep quotes or write multiline text | [Strings and numbers](scalars.md) |
| Use shared values and defaults | [Anchors and merges](anchors.md) |
| Choose indentation or document markers | [Settings](settings.md) |
| Report invalid YAML | [Handle errors](errors.md) |
| Copy a practical recipe | [Examples](examples.md) |
| Check current edge cases | [Known limitations](limitations.md) |

## Choosing and switching

- [Why yamluna](../why.md): what it does differently, with examples.
- [Compare libraries](../comparison.md): yamluna, ruamel.yaml, PyYAML, and others, with round-trip and speed measurements.
- [Switch from ruamel.yaml](../migrating/index.md): the code changes to make, and the [behavior differences](../migrating/differences.md) to expect.

For method signatures, use the [API reference](../api/index.md).
