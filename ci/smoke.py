"""Checks that an installed yamluna wheel loads, edits and dumps a document.

Run it against a fresh interpreter that has nothing but the wheel installed:

```bash
uv venv /tmp/fresh --python 3.13
uv pip install --python /tmp/fresh/bin/python dist/*.whl
/tmp/fresh/bin/python ci/smoke.py
```

Installing into an empty environment is what makes the wheel verified rather than
merely built, and running it on a Python other than the one the extension was compiled
against is what verifies the abi3 tag.

It checks three things, which between them cross the Rust and Python boundary in both
directions:

1. A commented document with an anchor loads and dumps byte-identically, covering the
   emitter, the trivia slots and the anchor and alias path.
2. The same document, edited, keeps each comment with the entry it described, covering
   the trivia store across a mutation.
3. A custom class registered on the instance registry dumps and reads back, covering
   the representer, the constructor and the `%TAG` wire format.

Every check is an assertion, so the script exits non-zero on the first failure and
prints the versions it ran against on success.
"""

import sys

import yamluna

SRC = """\
# header
defaults: &defaults   # reused below
  retries: 3
name: demo        # eol comment
items:
  - 1   # one
  - 2
prod: *defaults

# tail
"""

yaml = yamluna.YAML()
yaml.preserve_quotes = True

doc = yaml.load(SRC)
out = yaml.dump(doc)
assert out == SRC, f'round trip is not byte-identical:\n{out!r}'
assert doc['prod'] is doc['defaults'], 'the alias did not resolve to the anchored object'

doc['items'].append(3)
del doc['name']
edited = yaml.dump(doc)
assert '# eol comment' not in edited, edited  # went with the key it described
assert '# one' in edited and '# tail' in edited, edited  # the others did not move
assert '&defaults' in edited and '*defaults' in edited, edited  # still an alias, not a clone


class Circuit:
    """A custom class, registered on one `YAML` instance and not on any other."""

    def __init__(self, qubits: int = 0) -> None:
        self.qubits = qubits


yaml.register_class(Circuit)
text = yaml.dump(yamluna.CommentedMap({'main': Circuit(qubits=2)}))
assert '%TAG !' in text, text  # the namespace went out as a directive
assert '!Circuit' in text, text
back = yaml.load(text)
assert isinstance(back['main'], Circuit), type(back['main'])
assert back['main'].qubits == 2, back['main'].qubits

print(f'wheel smoke test OK: yamluna {yamluna.__version__} on {sys.version.split()[0]}')
