# Sound Vault Desktop

Local-first desktop app for a private TikTok Sound Vault.

V1 goals:

- index an existing file-native vault folder
- search/preview/tag sounds locally
- keep SQLite as a cache, with `metadata.json` and catalog JSONL as durable truth
- accept iOS Shortcut share links through a pairing-code relay without accounts

This repo intentionally keeps cloud infrastructure thin: the relay moves URLs, not media.

## Current maturity

Private alpha. The local catalog/index/inbox path is regression-tested. The relay now supports SQLite persistence for private hosted tests. The PySide GUI still needs real Mac/Windows click-through testing, and the hosted relay needs rate limiting before public alpha.

## Requirements

- Python 3.11+
- macOS, Windows, or Linux
- desktop GUI extra: `PySide6`
- relay extra: `fastapi`, `uvicorn`, `pydantic`

Keep virtualenvs off CIFS/NAS mounts. Use a local disk venv and point `--vault` at the NAS vault.

## Install for local desktop development

```bash
python3 -m venv /opt/data/venvs/sound-vault-desktop
/opt/data/venvs/sound-vault-desktop/bin/python -m pip install -U pip
/opt/data/venvs/sound-vault-desktop/bin/python -m pip install -e ".[gui,relay,dev]"
```

Mac example:

```bash
python3 -m venv ~/venvs/sound-vault
source ~/venvs/sound-vault/bin/activate
python -m pip install -e ".[gui,relay,dev]"
sound-vault --vault "$HOME/Documents/Sound Vault"
```

Windows PowerShell example:

```powershell
py -3.11 -m venv $env:USERPROFILE\venvs\sound-vault
& $env:USERPROFILE\venvs\sound-vault\Scripts\Activate.ps1
python -m pip install -e ".[gui,relay,dev]"
sound-vault --vault "$env:USERPROFILE\Documents\Sound Vault"
```

## Run

CLI smoke test:

```bash
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault "/nas/TikTok Sound Vault"
```

GUI:

```bash
sound-vault --vault "/path/to/Sound Vault"
```

Default vault resolution:

1. `SOUND_VAULT_DEFAULT_VAULT`, if set
2. saved app setting from the GUI vault picker
3. `/nas/TikTok Sound Vault`, if present
4. `~/Documents/Sound Vault`

App config/data paths can be overridden with:

```text
SOUND_VAULT_CONFIG_DIR=/path/to/config
SOUND_VAULT_DATA_DIR=/path/to/data
```

## Test

```bash
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation --sdist --wheel
```

## Relay

Local relay:

```bash
PYTHONPATH=src SOUND_VAULT_RELAY_HOST=127.0.0.1 SOUND_VAULT_RELAY_PORT=43117 \
  /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.relay.server
```

Docker relay:

```bash
docker build -f Dockerfile.relay -t sound-vault-relay .
docker run --rm -p 43117:43117 -v sound-vault-relay-data:/data \
  -e SOUND_VAULT_RELAY_STORAGE_PATH=/data/relay.sqlite3 sound-vault-relay
curl http://127.0.0.1:43117/v1/health
```

Relay security posture:

- URLs only, no media files
- no TikTok credentials
- device secret stays desktop-side
- unknown pair codes are rejected
- SQLite relay persistence is available with `SOUND_VAULT_RELAY_STORAGE_PATH`
- basic per-IP rate limiting is available with `SOUND_VAULT_RELAY_RATE_LIMIT` and `SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS`
- relay logs mask pair codes and redact device secrets/tokens
- hosted public alpha still needs hosted restart/load testing and abuse cleanup tuning
