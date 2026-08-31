"""register_class, and the %TAG wire format that keeps two `Circuit`s apart.

ruamel keys its registry on the class *name*, so the second library to register a
`Circuit` silently overwrites the first and you get the wrong class back
(docs/DIVERGENCES.md C1).  yamluna keys on the fully qualified path and writes the
namespace into the document with YAML's own `%TAG` mechanism.

    .venv/bin/python examples/custom_classes.py
"""

from yamluna import YAML
from yamluna.error import ConstructorError


def a_class(module: str, name: str) -> type:
    """A class pretending to live in `module`.

    In real code these live in real modules and `cls.__module__` is already right;
    faking it keeps this example to one file.
    """
    cls = type(name, (), {'__init__': lambda self, **kw: self.__dict__.update(kw)})
    cls.__module__ = module
    return cls


LibxCircuit = a_class('libx.circuits', 'Circuit')  # libx.circuits.Circuit
LibyCircuit = a_class('liby.core', 'Circuit')  # liby.core.Circuit
LibxGate = a_class('libx.gates', 'Circuit')  # libx.gates.Circuit — same name again


# -- one library: the source takes the primary `!` handle, tags are bare ------------

yaml = YAML()
yaml.register_class(LibxCircuit)
print('# one library'.ljust(60, '-'))
print(yaml.dump({'main': LibxCircuit(qubits=2)}))

# -- two libraries: the most-used source keeps `!`, the rest get named handles ------

yaml = YAML()
yaml.register_class(LibxCircuit)
yaml.register_class(LibyCircuit)
two_libs = yaml.dump({'a': LibxCircuit(qubits=2), 'b': LibyCircuit(n=3)})
print('# two libraries'.ljust(60, '-'))
print(two_libs)

back = yaml.load(two_libs)
assert type(back['a']) is LibxCircuit and back['a'].qubits == 2
assert type(back['b']) is LibyCircuit and back['b'].n == 3
assert yaml.dump(back) == two_libs  # and it round-trips

# -- two modules of one library: the colliding sources promote to module paths ------

yaml = YAML()
yaml.register_class(LibxCircuit)
yaml.register_class(LibxGate)
assert yaml.registry.registration_for(LibxCircuit).source == 'libx.circuits'
assert yaml.registry.registration_for(LibxGate).source == 'libx.gates'
two_mods = yaml.dump({'a': LibxCircuit(qubits=2), 'b': LibxGate(width=1)})
print('# two modules of one library'.ljust(60, '-'))
print(two_mods)

back = yaml.load(two_mods)
assert type(back['a']) is LibxCircuit
assert type(back['b']) is LibxGate

# -- a hand-written bare `!Circuit` with two candidates: never guesses --------------

yaml = YAML()
yaml.register_class(LibxCircuit)
yaml.register_class(LibyCircuit)
try:
    yaml.load('main: !Circuit\n  qubits: 2\n')
except ConstructorError as exc:
    print('# bare !Circuit, two candidates'.ljust(60, '-'))
    print(exc)

# One candidate is unambiguous, so hand-written files stay pleasant to write.
solo = YAML()
solo.register_class(LibxCircuit)
assert type(solo.load('main: !Circuit\n  qubits: 2\n')['main']) is LibxCircuit

# The registry is per-instance: registering here never leaks into another YAML().
assert YAML().registry.registration_for(LibxCircuit) is None

# --- real output -----------------------------------------------------------------
# $ .venv/bin/python examples/custom_classes.py
# # one library-----------------------------------------------
# %TAG ! tag:libx/
# ---
# main: !Circuit
#   qubits: 2
#
# # two libraries---------------------------------------------
# %TAG ! tag:libx/
# %TAG !liby! tag:liby/
# ---
# a: !Circuit
#   qubits: 2
# b: !liby!Circuit
#   n: 3
#
# # two modules of one library--------------------------------
# %TAG ! tag:libx.circuits/
# %TAG !libx-gates! tag:libx.gates/
# ---
# a: !Circuit
#   qubits: 2
# b: !libx-gates!Circuit
#   width: 1
#
# # bare !Circuit, two candidates-----------------------------
# ambiguous tag '!Circuit': 2 registered candidates: libx.circuits.Circuit (= tag:libx/Circuit), liby.core.Circuit (= tag:liby/Circuit); yamluna will not guess. Add a %TAG directive naming the source (e.g. '%TAG ! tag:libx/') or re-register with an explicit source= to disambiguate.
#   in "<unicode string>", line 2, column 3
