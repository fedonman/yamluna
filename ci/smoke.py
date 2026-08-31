"""Smoke-test an installed yamluna wheel: load a commented document, dump it, edit it.

Run against a *fresh* interpreter that only has the wheel installed -- this is what makes
the wheel verified rather than merely built.
"""

import sys

import yamluna

SRC = """\
# header
name: demo        # eol comment
items:
  - 1   # one
  - 2

# tail
"""

yaml = yamluna.YAML()
yaml.preserve_quotes = True

doc = yaml.load(SRC)
out = yaml.dump(doc)
assert out == SRC, f'round trip is not byte-identical:\n{out!r}'

doc['items'].append(3)
del doc['name']
edited = yaml.dump(doc)
assert '# eol comment' not in edited, edited  # went with the key it described
assert '# one' in edited and '# tail' in edited, edited  # the others did not move

print(f'wheel smoke test OK: yamluna {yamluna.__version__} on {sys.version.split()[0]}')
