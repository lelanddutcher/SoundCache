# QA Response Build 2026-05-17a

This build adds the first deterministic back-catalog import spine from the 2026-05-18 handoff materials.

## Scope

- Ported TikTok favorite-sound export normalization into `sound_vault.importers.tiktok_archive`.
- Added public oEmbed enrichment with checkpoint/resume support in `sound_vault.workers.oembed`.
- Added metadata-only package writing in `sound_vault.vault.package_writer`.
- Added CLI commands that avoid Qt import:
  - `--import-favorite-sounds`
  - `--enrich-favorite-sounds-oembed`
  - `--package-imported-sounds`
- Added desktop UI controls:
  - Ingest inbox: `Import TikTok export`
  - Worker status: `Run oEmbed enrichment`
  - Worker status: `Package imported metadata`
- Worker status now reports latest import artifacts and package audit counts.

## Behavior

- Original TikTok export files are read-only inputs.
- TikTok JSON fragments are repaired in memory and normalized to `catalog/imports/`.
- oEmbed enrichment writes JSON/CSV, checkpoints progress, resumes completed IDs, and records per-row errors.
- Package writing creates durable `sounds/{music_id} - {title} - {author}/metadata.json` folders.
- Metadata-only packages are valid records with explicit missing-asset audit flags.
- Catalog JSONL/CSV are rewritten atomically with upsert semantics.
- Existing package metadata keeps useful paths/assets when an imported metadata row touches the same music ID.

## Artifacts

Preferred tarball:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260517a.tar.gz
sha256: 669960dfea183cd30d7ecd78b3a52294bae0aebd97b133eda28504ea3f2ff903
```

Fallback zip:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260517a.zip
sha256: a8d314bfe10ff540b094a77639092dc17bd053b9d418f9e03fe26e9d8b33d8e7
```

Wheel:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 246b7afbec99f681f53ec12327ca2bb16abe1c586c36e2eaacf43dccf811fa85
```

## Verification

- `pytest -q`: 165 passed.
- `git diff --check`: passed.
- `py_compile`: importer, oEmbed worker, package writer, app, view model, desktop UI, and targeted tests passed.
- Source real-vault diagnostics passed.
- Source real-vault CLI smoke: 2,036 records.
- Source temp import/package/index smoke: 1 normalized, 1 metadata package, 1 indexed.
- Installed-wheel real-vault diagnostics passed.
- Installed-wheel real-vault CLI smoke: 2,036 records.
- Installed-wheel temp import/package/index smoke passed.
- Installed-wheel offscreen Qt smoke opened the window with auto-index/audio disabled and saw 5 views.
- Launcher archive verification passed for tar/zip contents, Info.plist, executable mode, and bundled wheel.

## Remaining Work

- The oEmbed lane is public metadata only; authenticated audio/artwork/video capture remains intentionally separate and not enabled in this build.
- Worker jobs are single-lane and simple; richer retry queues, progress bars, and cancellation should come next.
- Metadata-only import can populate search and audit state, but full editor-grade packages still require audio/artwork/transcript/video enrichment workers.
