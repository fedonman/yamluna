# Why yamluna?

yamluna is for Python code that reads and writes YAML, including files that people also edit, and that wants its own objects back rather than nested dictionaries. The defaults do that work: there is no loader type to pick and no formatting option to set before the output matches the input.

## The file comes back as written

An unchanged `+12` stays `+12`. A list indented by four spaces stays indented by four spaces. An unused anchor keeps its name, and `---` and `...` markers stay where they were. You don't configure a formatter to recreate the file's style, because nothing you did not edit is re-formatted.

This read-edit-save workflow is called a **round trip**. In the project's 40-file round-trip corpus, yamluna reproduces all 40 files byte for byte. ruamel.yaml reproduces 3 and StrictYAML 2; PyYAML and py-yaml12 reproduce none, because they drop comments and reformat what they write. [Results and method](comparison.md).

## Your objects, not just dictionaries

`@yaml.register_class` on a class is enough to save its objects under a tag and load them back as that class. For a class you cannot change, such as numpy's `ndarray`, `Decimal`, or `timedelta`, pass a `to_yaml` and a `from_yaml` function to `register_class()` instead. [Custom classes](guide/custom-classes.md) shows both.

Registrations are kept per `YAML()` instance, and tags are namespaced by the package a class came from, using YAML's own `%TAG` directive. Your application's `Config` and a library's `Config` can be registered side by side and each loads back as itself. A hand-written `!Config` is accepted when only one registered class could be meant, and rejected with both candidates named when two could.

## Comments follow your edits

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

Deleting a setting also removes its attached comments, and renaming a key with `rename()` keeps them in place. [More comment examples](guide/comments.md). There are still edge cases around comments above a collection's first item and inserting into commented lists; [known limitations](guide/limitations.md) explains them.

## Fast

Parsing and writing are done by a Rust core. A complete load and save runs 1.7 to 6.0 times faster than ruamel.yaml, depending on the file, and faster than StrictYAML. PyYAML with libyaml and py-yaml12 are faster than yamluna because they keep nothing of the original text, which is the trade to weigh if you never save the file back. [Compare speed and features](comparison.md), or [start with a file](guide/load-and-dump.md).
