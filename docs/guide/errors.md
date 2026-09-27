# Handle errors

Catch `YAMLError` when you want to report a YAML problem to the user. Errors with a source position provide `problem_mark`.

## Report the line and column

```python
from yamluna import YAML, YAMLError

yaml = YAML()
try:
    yaml.load('replicas: 2\nreplicas: 5\n')
except YAMLError as error:
    print(type(error).__name__)
    mark = getattr(error, 'problem_mark', None)
    if mark is not None:
        print(f'Line {mark.line + 1}, column {mark.column + 1}')
```

Output:

```text
DuplicateKeyError
Line 2, column 1
```

`print(error)` includes the explanation and available location details. The stored line and column numbers start at zero; add one for display.

## Common problems

| Error | What to do |
| --- | --- |
| `ScannerError` | Fix the YAML syntax or undefined alias at the reported location |
| `DuplicateKeyError` | Remove or rename the repeated key |
| `ComposerError` | Use `load_all()` for a stream containing multiple documents |
| `ConstructorError` | Check the tag registration or your `from_yaml` hook |
| `RepresenterError` | Convert the value to a supported type or register its class |
| `YAMLStreamError` | Pass YAML text, a `Path`, or an appropriate file object |

File errors such as `FileNotFoundError` and `PermissionError` are ordinary Python exceptions. Catch `OSError` separately when reporting file-access problems.

## Duplicate keys

By default, repeated keys are errors. If you deliberately accept them, set `yaml.allow_duplicate_keys = True`: yamluna warns and **keeps the last value**. Saving writes one entry for that key. A repeated `<<` merge key always raises.

See the [error reference](../api/errors.md) for the exception classes.
