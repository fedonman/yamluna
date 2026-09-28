# Switch to yamluna

Most code needs three changes: import `YAML` from `yamluna`, create one `YAML()` instance, and call its `load()` and `dump()` methods. What else changes depends on the library you use now:

| You use | Main differences | Guide |
| --- | --- | --- |
| PyYAML or oyaml | YAML 1.2 instead of 1.1, so `yes` and `on` are strings; `add_constructor` and `add_representer` become `register_class` | [From PyYAML](pyyaml.md) |
| ruamel.yaml | No `typ=`; registrations are per instance and namespaced; comments move with edited entries | [From ruamel.yaml](ruamel.md) |
| py-yaml12 | Loaded values keep their formatting; `handlers=` becomes `register_class` | [From py-yaml12](others.md#from-py-yaml12) |
| StrictYAML | All of YAML is accepted and values are typed, but there is no schema validation | [From StrictYAML](others.md#from-strictyaml) |

Whichever you come from, register your classes with [`register_class`](../guide/custom-classes.md), pass a `Path` rather than a string to read a file by name, and check the [known limitations](../guide/limitations.md). [Compare libraries](../comparison.md) has the features, round-trip results, and speed of each side by side.
