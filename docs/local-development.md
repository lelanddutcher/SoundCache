# Local Development Notes

## NAS/CIFS environment

Do not create Python virtualenvs directly inside this repo when it lives on the NAS. CIFS symlinks can fail.

Use an off-NAS venv:

```bash
uv venv /opt/data/venvs/sound-vault-desktop
uv pip install --python /opt/data/venvs/sound-vault-desktop/bin/python -e '.[dev]'
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
```

## Current Hermes Linux GUI limitation

The current Slack/Hermes Linux runtime cannot import PySide6 widgets because the container is missing `libEGL.so.1`, and this user lacks apt permissions to install it.

Observed:

```text
ImportError: libEGL.so.1: cannot open shared object file: No such file or directory
apt-get ... Permission denied
```

The non-GUI code path is intentionally lazy-loaded so CLI/index/relay tests do not require Qt system libraries.

On a normal Mac/Windows dev machine, PySide6 wheels should provide the app runtime. For Linux GUI dev, install system Qt/OpenGL deps, e.g. `libegl1`, `libgl1`, `libxkbcommon-x11-0`, `libxcb-cursor0`.
