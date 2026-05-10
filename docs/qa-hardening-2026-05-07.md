# TikTok Sound Vault QA + Production Hardening Report

Timestamp: 2026-05-07T02:25:11Z
Repo: `/nas/TikTok Sound Vault/product/sound-vault-desktop`

## Verification status

Passed:

```text
ruff: All checks passed
pytest: 41 passed in 0.55s
build: sound_vault_desktop-0.1.0.tar.gz and sound_vault_desktop-0.1.0-py3-none-any.whl
CLI: Sound Vault loaded 1188 records from /nas/TikTok Sound Vault
relay import smoke: Sound Vault Pairing Relay
```

Latest verification: 2026-05-07T04:22:42Z

Could not run:

```text
Docker image build: Docker daemon unavailable at unix:///var/run/docker.sock
GUI visual click-through: still requires Mac/Windows or Linux Qt/OpenGL deps; current container lacks GUI stack
```

Final soak rerun:

```text
process: proc_b72cfc055bbd
log: docs/qa-soak-2026-05-07-final.log
iteration 1: ruff passed, 41 tests passed, CLI loaded 1188 records
plan: 24 iterations, every 5 minutes
```

## Changes made

### Data/catalog hardening

- catalog loader now skips malformed JSONL rows instead of crashing
- skips non-object catalog rows
- normalizes tags when tags arrive as list/tuple/string/invalid value
- handles malformed `associated_video_count` values safely
- includes `music_id` in search text
- added regression tests for malformed catalog rows and music-id search

### SQLite/index hardening

- dedupes records by `music_id` before rebuild to avoid duplicate-primary-key failure
- enables WAL mode, busy timeout, and `synchronous=NORMAL`
- clamps search limit to a safe range
- added regression tests for duplicate records and hostile limits

### Shortcut inbox hardening

- JSONL inbox reader now skips corrupt/non-object rows
- dedupes repeated item IDs while reading
- writes inbox updates atomically via temp file + `os.replace`
- relay client now writes through `ShortcutInboxStore` instead of raw append
- relay client ignores malformed relay payload items
- added tests for corrupt inbox recovery and relay-client dedupe

### Final blocker closeout pass

- added persisted relay settings in `AppSettings` (`relay_base_url`, `pair_code`, `device_id`, `device_secret`)
- Settings UI now saves relay configuration and can request a new pairing code from `/v1/pairing/create`
- pairing card reflects saved relay state without exposing device secrets
- Play button now opens local audio paths or preview/audio/media URLs through the OS default handler
- index rebuild now runs on a one-worker background executor and reports status back through a Qt timer
- relay now enforces configurable per-IP rate limits with `SOUND_VAULT_RELAY_RATE_LIMIT` and `SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS`
- relay event logging masks pair codes and redacts device secrets/tokens
- added regression tests for settings persistence, play-target resolution, async indexing, rate limiting, log masking, and desktop UI wiring

### Relay hardening

- `/v1/inbox/submit` now rejects unknown pair codes
- pair-code route acceptance is registered explicitly in `InboxStore`
- pairing setup TTL no longer accidentally bricks Shortcut submissions after 10 minutes
- pair-code route TTL is tracked separately from queued-link TTL
- SQLite relay persistence added via `InboxStore(db_path=...)`
- `SOUND_VAULT_RELAY_STORAGE_PATH` wires persistent relay state into the server
- device registrations, accepted pair-code routes, and queued links survive restart with SQLite
- expired persistent inbox rows are deleted during poll/restart cleanup
- added FastAPI server regression tests for unknown pair code rejection and valid submit/poll round-trip
- added inbox/persistence tests for pair-code acceptance expiry and restart delivery

### Desktop/UI hardening

- added `sound_vault.settings` for cross-platform app config/data paths
- default vault path is no longer hard-coded only to `/nas/TikTok Sound Vault`
- GUI vault picker persists selected vault path
- index path moved to platform-ish app data dir with env overrides
- rebuild errors now show non-fatal UI status instead of hard-crashing the window
- empty searches clear stale preview state
- preview now calls `view_model.preview_for()` to recover richer record metadata where available
- fake `RIVER-7421` pairing card removed
- library/inbox tables now use single-row selection and sorting
- sorted-table actions now store stable IDs in row item data to avoid wrong-record actions
- Open Folder is wired for records with `raw["paths"]["folder"]`

### Packaging/deployment hardening

- split package extras into `gui`, `relay`, and `dev`
- fixed Hatch wheel package selection with `packages = ["src/sound_vault"]`
- Docker relay now installs the package with `[relay]`
- Docker relay binds `0.0.0.0` by default
- Docker relay runs as non-root user
- Docker relay has a healthcheck using `SOUND_VAULT_RELAY_PORT`
- Fly example sets host/port/storage env and a `/data` mount
- README now includes install/run/test/relay maturity notes
- relay deployment docs now call out private-test vs public-alpha blockers
- `.gitignore` now excludes `dist/`, `build/`, and egg-info artifacts

## Remaining blockers before calling it production-ready

P0 before public relay alpha:

- relay restart/load test against the real hosted target
- real iOS Shortcut → hosted relay → desktop inbox end-to-end test

P1 before broad desktop beta:

- real GUI click-through on macOS and Windows
- installer/signing/notarization path

## Re-run commands

```bash
cd "/nas/TikTok Sound Vault/product/sound-vault-desktop"
/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation --sdist --wheel
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault "/nas/TikTok Sound Vault"
```

## Files touched

See `git diff --stat` from repo root. Main touched areas:

- `src/sound_vault/vault/indexer.py`
- `src/sound_vault/db/index_db.py`
- `src/sound_vault/ingest/shortcut_inbox.py`
- `src/sound_vault/relay/*`
- `src/sound_vault/ui/desktop.py`
- `src/sound_vault/settings.py`
- `tests/test_*hardening.py`
- `README.md`
- `docs/relay-deployment.md`
- `Dockerfile.relay`
- `pyproject.toml`
