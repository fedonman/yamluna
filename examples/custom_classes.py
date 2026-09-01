"""Registering a class, and the %TAG wire format that keeps two `Circuit`s apart.

ruamel keys its registry on the class *name*, so the second library to register a
`Circuit` silently overwrites the first and you get the wrong class back. yamluna keys
on the fully qualified path (`libx.circuits.Circuit`) and writes the namespace into the
document with YAML's own `%TAG` mechanism, so both classes survive a round trip and
come back as themselves.

Run it:

```bash
.venv/bin/python examples/custom_classes.py
```
"""

from yamluna import YAML
from yamluna.error import ConstructorError


def a_class(module: str, name: str) -> type:
    """Builds a class that reports `module` as the module it was defined in.

    Args:
        module: The dotted module path the class claims to come from.
        name: The name of the class.

    Returns:
        A new class whose constructor copies its keyword arguments onto the instance.
    """
    cls = type(name, (), {'__init__': lambda self, **kw: self.__dict__.update(kw)})
    # In real code the class already lives in a real module and `cls.__module__` is
    # right on its own. Faking it keeps this example to a single file.
    cls.__module__ = module
    return cls


LibxCircuit = a_class('libx.circuits', 'Circuit')  # libx.circuits.Circuit
LibyCircuit = a_class('liby.core', 'Circuit')  # liby.core.Circuit
LibxGate = a_class('libx.gates', 'Circuit')  # libx.gates.Circuit, the same name again


# -- one library --------------------------------------------------------------------
#
# A single registered source takes YAML's primary `!` handle, so its tags go out bare.

yaml = YAML()
yaml.register_class(LibxCircuit)
print('# one library'.ljust(60, '-'))
print(yaml.dump({'main': LibxCircuit(qubits=2)}))

# -- two libraries ------------------------------------------------------------------
#
# The most-used source keeps `!`; every other source gets a named handle.

yaml = YAML()
yaml.register_class(LibxCircuit)
yaml.register_class(LibyCircuit)
two_libs = yaml.dump({'a': LibxCircuit(qubits=2), 'b': LibyCircuit(n=3)})
assert two_libs is not None  # dump without a stream returns the text
print('# two libraries'.ljust(60, '-'))
print(two_libs)

back = yaml.load(two_libs)
assert type(back['a']) is LibxCircuit
assert back['a'].qubits == 2
assert type(back['b']) is LibyCircuit
assert back['b'].n == 3
assert yaml.dump(back) == two_libs  # and it round-trips

# -- two modules of one library -----------------------------------------------------
#
# Two classes called `Circuit` under the same top-level package collide, so both
# sources promote from the package name to the full module path.

yaml = YAML()
yaml.register_class(LibxCircuit)
yaml.register_class(LibxGate)
circuit_reg = yaml.registry.registration_for(LibxCircuit)
gate_reg = yaml.registry.registration_for(LibxGate)
assert circuit_reg is not None
assert gate_reg is not None
assert circuit_reg.source == 'libx.circuits'
assert gate_reg.source == 'libx.gates'
two_mods = yaml.dump({'a': LibxCircuit(qubits=2), 'b': LibxGate(width=1)})
assert two_mods is not None  # dump without a stream returns the text
print('# two modules of one library'.ljust(60, '-'))
print(two_mods)

back = yaml.load(two_mods)
assert type(back['a']) is LibxCircuit
assert type(back['b']) is LibxGate

# -- a hand-written bare `!Circuit` with two candidates -----------------------------
#
# With more than one candidate the loader raises instead of picking one, and the
# message lists the candidates and the two ways to disambiguate.

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
