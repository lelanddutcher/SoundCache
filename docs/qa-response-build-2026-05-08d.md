# Sound Vault QA response build — 2026-05-08d

User reported:

- right preview metadata must be selectable for copy/paste;
- associated videos show in filter counts but not in the right panel;
- transcripts/lyrics are missing;
- columns cannot be resized;
- search/filter UI is slow;
- selected runtime shows stale/one-minute player duration until play;
- likely duplicate sounds need a review/dedupe tool;
- artwork scrape is incomplete.

## Fixed in app build 0.3.0

- Bumped app/package version to `0.3.0`.
- Right-side metadata labels are now selectable by mouse/keyboard:
  - title
  - metadata block
  - tags
  - time label
  - playback status
  - evidence list
- Table columns are now fully interactive/resizable; removed the stretch-locked sound column.
- Search/filter changes are debounced with a short single-shot timer instead of rebuilding the table on every keystroke.
- Search results now prefer hydrated in-memory records after indexing, so associated-video rows survive filtering and populate the right panel.
- Selection now updates the duration display from indexed metadata immediately instead of showing stale `QMediaPlayer` duration until playback starts.
- Preview metadata now explicitly shows duration and transcript status/text when available.

## Verified with real data

- Vault folders: `2,036`.
- Associated-video manifests exist broadly:
  - `associated_videos_manifest.json`: `2,036` folders
  - parsed records with associated video tuple: `737` sounds
- True artwork progress while scraper is still running:
  - `267 / 2,036` folders have true artwork
  - `1,769` missing
- Transcripts:
  - `0` transcript sidecars currently exist
  - transcript script exists, but Whisper/faster-whisper is not installed in the venv yet
- Duplicate audit:
  - generated candidate report with `352` candidate rows across `139` groups
  - reports:
    - `/nas/TikTok Sound Vault/reports/duplicate-candidates.json`
    - `/nas/TikTok Sound Vault/reports/duplicate-candidates.csv`

## Added dedupe tooling

- New non-destructive script:
  - `scripts/audit_duplicates.py`
- New test:
  - `tests/test_duplicate_audit.py`
- Groups likely duplicates by normalized title + artist/source and includes available duration/folder info for human review.

## Tests

```bash
/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_library_view_model.py::test_library_view_model_search_prefers_hydrated_records_with_associated_videos tests/test_desktop_ui_source.py::test_desktop_debounces_search_and_preview_metadata_is_selectable tests/test_desktop_ui_source.py::test_desktop_selection_updates_duration_from_metadata_before_playback_loads tests/test_desktop_ui_source.py::test_desktop_table_columns_include_dates_and_local_audio -q
# 4 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_duplicate_audit.py -q
# 1 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
# All checks passed

/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
# 92 passed
```

## Artifact

- `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260508d.zip`
  - size: `39133`
  - sha256: `03e05cc10c1874d113c8cf4fbb58bdc9e7184b3eb21ca6e0b0bc50d8045eba86`
- wheel:
  - `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl`
  - size: `32528`
  - sha256: `c39e3fd926b50b04b4c9328fabf3095cc93ceb23787f6066261d64869139b24c`

Archive verification confirmed:

```json
{
  "plist_version": "0.3.0",
  "cache_version": "0.3.0+20260508d",
  "wheel_has_debounce": true,
  "wheel_has_selectable_labels": true,
  "wheel_has_interactive_no_stretch": true,
  "wheel_has_duration_metadata": true,
  "wheel_has_hydrated_search": true
}
```

## Still not done

- Transcript/lyrics backfill is not done because no transcription engine is installed in the project venv.
- Artwork scrape is still running and incomplete.
- Search may still feel heavier than ideal because the table still creates per-row play buttons for full-catalog views; debounce reduces churn, but a proper model/delegate table would be the next performance fix.
- Dedupe tool is audit-only; no destructive merge/delete workflow yet.
