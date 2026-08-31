"""Load, edit, dump — everything you did not touch comes back byte for byte.

    .venv/bin/python examples/round_trip.py
"""

from yamluna import YAML

SRC = """\
# service configuration
name: demo            # shown in the UI
replicas: 3
legacy_mode: true     # remove me before 2.0

ports:
  - 80                # http
  - 443               # https

database:
  host: 'localhost'   # single quotes are kept
  port: 5432
  motd: |
    welcome
    to demo

# everything below is optional
features: []
"""

yaml = YAML()
yaml.preserve_quotes = True

# 1. An untouched document is reproduced exactly.  No `indent(...)` incantation
#    needed: every node reproduces the layout it was loaded with.
config = yaml.load(SRC)
assert yaml.dump(config) == SRC, 'round trip must be byte-identical'

# 2. The containers are a dict and a list, so ordinary Python works on them.
assert isinstance(config, dict) and isinstance(config['ports'], list)
assert config['replicas'] == 3
assert config['database']['motd'] == 'welcome\nto demo\n'

# 3. Edit.  Comments hang off the node they describe, so they stay with it —
#    and deleting an entry deletes that entry's comment and nothing else.
config['replicas'] = 5
config['database']['port'] = 6543
config['ports'].append(8080)
config['features'].append('beta')
del config['legacy_mode']

out = yaml.dump(config)
assert '# remove me before 2.0' not in out  # went with the key it described
assert '# shown in the UI' in out  # the neighbour's did not move
print(out)

# --- real output -----------------------------------------------------------------
# $ .venv/bin/python examples/round_trip.py
# # service configuration
# name: demo            # shown in the UI
# replicas: 5
#
# ports:
#   - 80                # http
#   - 443               # https
#   - 8080
#
# database:
#   host: 'localhost'   # single quotes are kept
#   port: 6543
#   motd: |
#     welcome
#     to demo
#
# # everything below is optional
# features: [beta]
