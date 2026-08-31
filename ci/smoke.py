"""Smoke-test an installed yamluna wheel.

Run against a *fresh* interpreter that only has the wheel installed -- that is what makes the
wheel verified rather than merely built, and running it on a Python other than the one the
extension was compiled against is what verifies the abi3 tag.

Three things, because they exercise three different halves of the boundary:

1. a commented document with an **anchor**, loaded and dumped byte-identically -- the emitter,
   the trivia slots and the anchor/alias path;
2. the same document edited -- the trivia store surviving a mutation;
3. a **custom class** registered with the instance registry, dumped and read back -- the
   representer, the constructor and the ``%TAG`` wire format.
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
    """A custom class, registered on this YAML() only."""

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
