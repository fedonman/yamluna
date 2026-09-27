# Why yamluna?

A config change should be easy to review. yamluna keeps the comments and layout around your edit, so a reader can see what changed and why.

## Keep the explanation with the value

Reverse this list and each comment follows its step:

```python
from yamluna import YAML

yaml = YAML()
config = yaml.load("""steps:
  - build    # compile
  - test     # check
  - publish  # upload
""")

config['steps'].reverse()
print(yaml.dump(config), end='')
```

Output:

```yaml
steps:
  - publish  # upload
  - test     # check
  - build    # compile
```

Deleting a setting also removes its attached comments. Renaming a mapping key with `rename()` keeps its comments in place. [More comment examples](guide/comments.md).

There are still edge cases around comments above a collection's first item and inserting into commented lists. [Known limitations](guide/limitations.md) explains them.

## Keep the author's formatting

An unchanged `+12` stays `+12`. A list indented by four spaces stays indented by four spaces. An unused anchor keeps its name. You don't need to configure a formatter to recreate the file's style.

This read-edit-save workflow is called a **round trip**. In the project's 40-file round-trip corpus, yamluna reproduces all 40 files exactly; ruamel.yaml 0.19.1 reproduces 3 with the tested settings. [Results and method](comparison.md).

## Use it in a larger application

Each `YAML()` keeps its own class registrations. Your application's types can coexist with another library's types, even if both define a class called `Config`. [Custom classes](guide/custom-classes.md) shows how.

The project's recorded release-build benchmarks also show faster read-and-write cycles than ruamel.yaml. [Compare speed and features](comparison.md), or [start with a file](guide/load-and-dump.md).
