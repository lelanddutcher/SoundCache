# QA — app-native TikTok Sound Vault ingestion

Timestamp: 2026-05-26T03:41:57Z

## Scope

Built and verified a local-first, cross-platform import workflow for TikTok `favorite sounds list.json` data exports:

- import wizard: select export → repair/preview → normalize → enrich/skip enrichment → package → rebuild index → verify archive/search health
- worker result abstraction with durable JSON/JSONL run outputs and artifact verification
- settings/secrets schema for cloud/local transcription and capture aggressiveness without plaintext API keys
- dependency diagnostics for PATH tools, Python packages, model cache, ffmpeg/ffprobe, and Demucs acceleration/fallback
- cloud/local transcription worker scaffolding, artwork worker, explicit TikTok storage-state validation, resumable verification outputs
- CLI hooks that do not rely on Hermes/agent behavior

## Commands run

```bash
PYTHONPATH=src pytest -q tests/test_app_native_workflow.py tests/test_media_auth_workers.py tests/test_app_cli_import.py
# 12 passed in 0.19s

PYTHONPATH=src python -m sound_vault.app --diagnose-dependencies --vault /tmp/sound-vault-qa-empty
# emitted JSON diagnostics for ffmpeg/ffprobe, ASR packages/models, Demucs acceleration/fallback

PYTHONPATH=src python -m sound_vault.app \
  --vault /tmp/sound-vault-app-native-qa \
  --run-import-workflow /tmp/sound-vault-favorite-sounds-qa.json \
  --skip-oembed \
  --verify-search 1234567890
# Preview: 2 rows / 2 unique IDs / 2 new
# Packaged imported sounds: 2 created, 0 updated
# Verification: ok {'metadata_records': 2, 'sound_folders': 2, 'catalog_files': 2, 'verified_outputs': 4, 'missing_outputs': 0, 'search_hits': 1}

PYTHONPATH=src python -m sound_vault.app --vault /tmp/sound-vault-app-native-qa --verify-vault --verify-search 2222222222
# Verification: ok {'metadata_records': 2, 'sound_folders': 2, 'verified_outputs': 4, 'missing_outputs': 0, 'search_hits': 1}

uvx ruff check .
# All checks passed!

PYTHONPATH=src pytest -q
# 177 passed, 1 skipped in 1.71s

uvx --from build --with hatchling pyproject-build --no-isolation
# Successfully built sound_vault_desktop-0.3.0.tar.gz and sound_vault_desktop-0.3.0-py3-none-any.whl

uv venv /tmp/sv-wheel-smoke --python 3.11
uv pip install --python /tmp/sv-wheel-smoke/bin/python dist/sound_vault_desktop-0.3.0-py3-none-any.whl
/tmp/sv-wheel-smoke/bin/sound-vault --diagnose --vault /tmp/sound-vault-app-native-qa
/tmp/sv-wheel-smoke/bin/sound-vault --verify-vault --vault /tmp/sound-vault-app-native-qa --verify-search 1234567890
# installed-wheel smoke passed; verification ok
```

## Durable output verification

The CLI workflow created browsable vault files under `/tmp/sound-vault-app-native-qa`:

```text
catalog/imports/.previews/favorite_sounds_import_normalized_preview.csv
catalog/imports/.previews/favorite_sounds_import_normalized_preview.json
catalog/imports/.previews/favorite_sounds_import_summary_preview.json
catalog/imports/favorite_sounds_import_normalized_2026-05-26.csv
catalog/imports/favorite_sounds_import_normalized_2026-05-26.json
catalog/imports/favorite_sounds_import_summary_2026-05-26.json
catalog/sounds.csv
catalog/sounds.jsonl
sounds/1234567890 - Unknown - Unknown/metadata.json
sounds/2222222222 - Unknown - Unknown/metadata.json
workers/verification/latest_import_verification.json
workers/verification/latest_manual_verification.json
workers/verification/runs/*.json
workers/verification/runs/*.jsonl
```

Search/index verification found both imported music IDs via the app CLI. Worker completion was gated on artifact existence and searchability, not thread/process return alone.

## Build artifacts

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 28c03c006490bb4df3b6bb5c42ef480fccc3e5384a3c74453bc53d8e8622a418
size: 100417 bytes

dist/sound_vault_desktop-0.3.0.tar.gz
sha256: 06968ccf016844fda682ca0b4abb957b820247d2af34b045ea7b51ef09a40f14
size: 997091 bytes
```

Wheel contents were checked for the new app-native ingestion modules:

- `sound_vault/workflows/import_wizard.py`
- `sound_vault/workers/result.py`
- `sound_vault/workers/transcription.py`
- `sound_vault/workers/artwork.py`
- `sound_vault/auth/tiktok_session.py`
- `sound_vault/dependency_diagnostics.py`

A packaging bug was caught and fixed: root private-data ignore patterns like `auth/` were hiding `src/sound_vault/auth/` from the wheel. `.gitignore` now scopes private vault folders to repo root paths (`/auth/`, `/catalog/`, `/sounds/`, etc.).

## Secret scan

Tracked diff secret scan: clean. No credentials or API keys were found in the diff. Settings store only secret references such as keyring/env handles.

## Notes / caveats

- Cloud ASR is configured as the recommended default path; local ASR diagnostics are explicit about executable/package/model-cache distinctions.
- Demucs is treated as an optional recovery/variant path with acceleration detection and CPU warning, not as a mandatory import dependency.
- Authenticated TikTok capture remains explicit/manual: storage-state validation is a conservative probe path and must stop on CAPTCHA/checkpoint. No credentials are logged or written to the vault.
- This QA was run on Linux. The wheel and CLI were installed and smoked in a clean uv-managed Python 3.11 venv; target OS GUI click-through/package notarization was not performed here.
