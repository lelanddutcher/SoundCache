# Sound Vault revision progress — 2026-05-08

## done

- Converted Leland QA into implementation changes rather than another plan pass.
- Added media/workflow filters to the library model + GUI:
  - has/missing audio
  - has/missing true artwork
  - has/missing transcript
  - has/missing associated videos
  - duration under/over 30s still works
- Kept full-catalog behavior: empty search returns the full indexed library, not a 200/500-row slice.
- Review queues / worker status now surface missing artwork, missing transcripts, and missing associated videos instead of hiding those gaps.
- Tightened artwork backfill detection so `thumbnail.*` / evidence-ish images do **not** count as true sound artwork.
- Verified a live artwork proof batch: 3/3 captures succeeded, writing `artwork.jpg/png`, `artwork_manifest.json`, and `metadata.paths.artwork`.
- Started the full true-artwork backfill as a resumable background process:
  - session: `proc_28fc582f7c5e`
  - command: `/opt/data/venvs/sound-vault-desktop/bin/python scripts/backfill_artwork.py --vault '/nas/TikTok Sound Vault' --limit 5000 --delay 6`
  - last observed: running, 80/2036 folders had true artwork, 1956 remaining.

## verification

```bash
/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_library_view_model.py::test_library_view_model_media_filters_cover_editor_workflow tests/test_library_view_model.py::test_backfill_artwork_missing_detection_requires_true_artwork tests/test_desktop_ui_source.py::test_desktop_library_has_duration_filters_visible_counts_and_real_row_play_buttons -q
# 3 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
# All checks passed

/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
# 87 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation
# built sound_vault_desktop-0.1.0.tar.gz and sound_vault_desktop-0.1.0-py3-none-any.whl

/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py --stamp 20260508
# launcher executable mode 0o755
```

## real vault smoke

Observed while the artwork backfill was running:

```json
{
  "catalog_rows": 2052,
  "unique_catalog_ids": 2036,
  "duplicate_catalog_rows": 16,
  "packaged_folders": 2036,
  "indexed_records": 2036,
  "db_total": 2036,
  "has_audio": 2036,
  "has_artwork": 61,
  "missing_artwork": 1975,
  "has_videos": 737,
  "has_transcript": 0
}
```

A later direct folder count showed `80` true-artwork folders after the background process continued.

## artifacts

- wheel: `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.1.0-py3-none-any.whl`
  - size: `32123`
  - sha256: `bc21f392e7ad5e493625f7f3d688713c3fd1273376d1b8c8e9caa874631bf481`
- Mac launcher zip: `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.1.0-20260508.zip`
  - size: `36556`
  - sha256: `d537f66a912f0f53df808722393e788e5b17fc8ede932ed9688bf18c28943f10`
- Mac launcher tar: `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.1.0-20260508.tar.gz`
  - size: `32333`
  - sha256: `9216d0ba1037ea0912d091848c391c444876bbac40646391df5492bc91b6e0bb`

## caveats

- Linux refreshed the unsigned Mac launcher bundle; this is not a signed/notarized standalone macOS app.
- GUI click-through on actual macOS remains target-machine QA.
- Transcript count is still 0 because the ASR worker exists but no local Whisper-family engine has been run/installed for the corpus.
- Full artwork backfill is intentionally slow/rate-limited and resumable by disk state. Do not kill it unless TikTok session health becomes a concern.
