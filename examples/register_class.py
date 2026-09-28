"""Store Python objects in YAML with `register_class`: your own classes and ones you do not own.

Your own class takes a decorator and nothing else: yamluna writes the instance's state
under a tag and restores it on load. A class you cannot change, such as numpy's
`ndarray` or `decimal.Decimal`, is registered by calling `register_class` with a
`to_yaml` and a `from_yaml` function that say how to write it and how to read it back.

Run it (numpy is in the development dependencies):

```bash
.venv/bin/python examples/register_class.py
```
"""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from yamluna import YAML, CommentedSeq

# -- your own class: the decorator is the whole setup ---------------------------------

yaml = YAML()


@yaml.register_class
@dataclass
class Server:
    host: str
    port: int = 80


config = yaml.load("""\
# Production servers
primary: !Server
  host: web-1
  port: 8080
timeout: 30      # seconds
""")

assert config['primary'] == Server('web-1', 8080)
config['primary'].port = 9090
config['canary'] = Server('web-3')
print('# your own class'.ljust(60, '-'))
print(yaml.dump(config))

# -- choose the tag and the namespace -----------------------------------------------
#
# `tag` and `source` pick what goes on the wire. They can also be class attributes,
# `yaml_tag` and `yaml_source`, which is the spelling ruamel.yaml code already uses.

yaml = YAML()
yaml.register_class(Server, tag='Host', source='myapp')
print('# a chosen tag and source'.ljust(60, '-'))
print(yaml.dump({'primary': Server('web-1')}))

# -- classes you do not own: pass to_yaml and from_yaml ----------------------------------
#
# `to_yaml(representer, value)` returns a node from `represent_scalar`,
# `represent_mapping` or `represent_sequence`. `representer.plan.tags[cls]` is the tag
# the document uses for the class, handle included. `from_yaml(constructor, node)`
# reads `node.value` for a scalar, or `constructor.construct_mapping(node)` and
# `constructor.construct_sequence(node)` for a collection.


def decimal_to_yaml(representer, value):
    return representer.represent_scalar(representer.plan.tags[Decimal], str(value))


def decimal_from_yaml(constructor, node):  # noqa: ARG001 - the hook signature
    return Decimal(node.value)


def flow(items):
    """Return `items` as a list written inline, `[1, 2]`."""
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def array_to_yaml(representer, array):
    fields = {
        'dtype': str(array.dtype),
        'shape': flow(array.shape),
        'data': flow(array.ravel().tolist()),
    }
    return representer.represent_mapping(representer.plan.tags[np.ndarray], fields)


def array_from_yaml(constructor, node):
    fields = constructor.construct_mapping(node)
    return np.array(fields['data'], dtype=fields['dtype']).reshape(fields['shape'])


yaml = YAML()
yaml.register_class(Decimal, to_yaml=decimal_to_yaml, from_yaml=decimal_from_yaml)
yaml.register_class(np.ndarray, to_yaml=array_to_yaml, from_yaml=array_from_yaml)

model = {
    'learning_rate': Decimal('0.001'),
    'weights': np.array([[0.5, 1.5], [2.0, 3.0]], dtype=np.float32),
}
text = yaml.dump(model)
assert text is not None  # dump without a stream returns the text
print('# classes you do not own'.ljust(60, '-'))
print(text)

back = yaml.load(text)
assert back['learning_rate'] == Decimal('0.001')
assert back['weights'].dtype == np.float32
assert np.array_equal(back['weights'], model['weights'])
assert yaml.dump(back) == text  # and it round-trips

# --- real output -----------------------------------------------------------------
# $ .venv/bin/python examples/register_class.py
# # your own class--------------------------------------------
# # Production servers
# %TAG ! tag:__main__/
# ---
# primary: !Server
#   host: web-1
#   port: 9090
# timeout: 30      # seconds
# canary: !Server
#   host: web-3
#   port: 80
#
# # a chosen tag and source-----------------------------------
# %TAG ! tag:myapp/
# ---
# primary: !Host
#   host: web-1
#   port: 80
#
# # classes you do not own------------------------------------
# %TAG ! tag:decimal/
# %TAG !numpy! tag:numpy/
# ---
# learning_rate: !Decimal 0.001
# weights: !numpy!ndarray
#   dtype: float32
#   shape: [2, 2]
#   data: [0.5, 1.5, 2.0, 3.0]
#
