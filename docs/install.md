# Install

Install yamluna from PyPI with **Python 3.11+**. Python wheels include the compiled extension.

## Install in a virtual environment

```bash
python -m venv .venv
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Then install:

```bash
python -m pip install yamluna
```

## Check it works

```python
from yamluna import YAML

yaml = YAML()
print(yaml.dump({'ready': True}), end='')
```

Output:

```yaml
ready: true
```

## Rust

Add the round-trip core from crates.io:

```bash
cargo add yamluna-core
```

For the YAML parser on its own:

```bash
cargo add yamluna-scanner
```

Next: [read and write your first file](guide/load-and-dump.md).
