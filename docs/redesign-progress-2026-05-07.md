# Redesign progress — 2026-05-07

## completed across redesign loop

- Inspected the live vault shape under `/nas/TikTok Sound Vault`:
  - `catalog/sounds.jsonl` is the durable catalog.
  - packaged sounds live under `sounds/<music_id> - .../` with `metadata.json`, local packaged `.m4a`, `associated_videos_manifest.json`, `videos.jsonl`, and `videos/` containing screenshots plus `.mp4` evidence clips.
  - latest real-data smoke observed 1,480 indexed records; 1,480 had local audio and evidence images; 751 had parsed associated-video manifests.
- Expanded the archive model:
  - `SoundRecord` carries `added_at`, `packaged_at`, `folder_path`, `local_audio_path`, `evidence_images`, and parsed `associated_videos`.
  - Indexer reads associated video manifests and screenshot/music-page images from actual vault folders instead of assuming catalog-only metadata.
  - SQLite index stores dates/local path columns and defaults library search ordering to newest packaged/added sounds first.
- Reworked the PySide desktop shell toward the brief:
  - Integrated Qt Multimedia playback path with `QMediaPlayer` + `QAudioOutput`, play/pause state, seek slider, and time display.
  - Fixed playback target resolution for records returned from SQLite search: local `.m4a` paths persisted in the DB are now considered playable even when raw catalog JSON is not hydrated.
  - Library table has archive-useful columns: play, sound, artist/source, status, added, packaged, videos, local audio, trend/context.
  - Table headers are sortable, interactive/resizable/movable, and now persist header layout state for library and shortcut inbox tables in local `AppSettings`.
  - Navigation uses real `QStackedWidget` views: Library, full Shortcut inbox, Review queues placeholder, Collections placeholder, Worker status placeholder, Settings dialog.
  - Right detail panel is formatted archive metadata instead of raw JSON-first: artwork/evidence preview, local audio path summary, associated video rows, and raw metadata hidden behind a `Raw metadata` toggle.
  - Applied a warmer retro brushed-metal/iTunes/LimeWire-ish stylesheet with skeuomorphic cards, inset tables, chrome buttons, and album-art well.
- Added regression coverage first, then implemented:
  - `tests/test_redesign_archive_model.py`
  - `tests/test_desktop_ui_source.py`
  - `tests/test_settings.py`
- Rebuilt distribution artifacts and refreshed existing Mac launcher archives.

## verification

- RED check for this run before implementation:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_play_target_uses_indexed_local_audio_path_from_database_rows tests/test_settings.py::test_settings_round_trips_table_header_layout_bytes tests/test_desktop_ui_source.py::test_desktop_persists_library_and_inbox_table_layouts -q`
  - result: 3 expected failures for missing DB-row local audio playback target, missing table-layout settings persistence, and missing desktop save/restore hooks.
- Focused tests after implementation:
  - same command
  - result: `3 passed in 0.17s`
- Full test suite:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q`
  - result: `50 passed in 1.30s`
- Lint:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .`
  - result: `All checks passed!`
- Real-data indexer smoke against live vault:
  - `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ...`
  - result: `real-data index: 1480 records; 1480 audio; 1480 evidence images; 751 associated-video manifests`
  - newest packaged timestamp observed: `2026-05-07T07:46:32.828941Z`
- GUI import/click-through smoke:
  - `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ... SoundVaultWindow ...`
  - result: blocked on this Linux host with `ImportError: libEGL.so.1: cannot open shared object file` before Qt app construction.
- Build:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation`
  - result: rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`
- Mac launcher refresh:
  - `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py`
  - verified wheel is present in the `.app` wheelhouse, `Info.plist` executable is `SoundVault`, and launcher mode is executable (`0o755`).
  - wheel: `dist/sound_vault_desktop-0.1.0-py3-none-any.whl`
    - size `25976`, sha256 `a00601728aa630dfebadf88414a4bbf208372623588054513b5a05afd1ee99c6`
  - zip: `dist/SoundVault-mac-launcher-0.1.0-20260507.zip`
    - size `32849`, sha256 `8e664792d3ab954c4ec9159e9d6254705f31b46f5b69786dcf75ee3ecd4d9fd3`
  - tar: `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz`
    - size `27136`, sha256 `8bf4d2062b578f4465044e4038af848f282fa721c81274676a4ce68c7e8a478b`

## latest cron iteration — 2026-05-07T09:55:02Z

### completed

- Re-inspected live vault metadata/media shape. The catalog now indexes 1,659 sound rows; all observed rows have local `.m4a` audio, evidence images, source confidence, vault version, and canonical URLs; 753 rows have parsed associated-video manifests. Usage counts are still not populated in current scrape data (`0` non-null usage counts), so the UI labels them `unknown` instead of lying.
- Extended the archive model and SQLite index with context fields from real metadata: `usage_count`, `source_provider`, `source_confidence`, `vault_version`, and `canonical_url`.
- Added those context fields to searchable text, so searches can find records by confidence/provider/canonical URL fragments.
- Enriched the right detail panel with usage count, source provider/confidence, vault version, and canonical URL.
- Added local associated-video screenshot icons to the clip column in the associated videos table when `screenshot_path` exists.

### verification for latest iteration

- RED check before implementation:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_build_index_enriches_local_audio_dates_images_and_videos tests/test_redesign_archive_model.py::test_index_database_round_trips_archive_context_fields tests/test_desktop_ui_source.py::test_desktop_surfaces_archive_context_and_video_thumbnails -q`
  - result: 3 expected failures for missing archive context fields and missing video thumbnail/icon surfacing.
- Focused tests after implementation:
  - same command
  - result: `3 passed in 0.10s`
- Full test suite:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q`
  - result: `52 passed in 0.98s`
- Lint:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .`
  - result: `All checks passed!`
- Real-data indexer smoke:
  - `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ...`
  - result: `real-data index: 1659 records; 1659 audio; 1659 evidence images; 753 associated-video manifests`
  - context fields: `1659 confidence; 1659 vault version; 1659 canonical urls; 0 usage counts`
  - newest packaged timestamp observed: `2026-05-07T09:53:32.123985Z`
- GUI import/click-through smoke:
  - `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ... SoundVaultWindow ...`
  - result: still blocked on this Linux host with `ImportError: libEGL.so.1: cannot open shared object file` before Qt app construction.
- Build:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation`
  - result: rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`
- Mac launcher refresh:
  - `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py`
  - verified wheel is present in the `.app` wheelhouse, `Info.plist` executable is `SoundVault`, and launcher mode is executable (`0o755`).
  - wheel: `dist/sound_vault_desktop-0.1.0-py3-none-any.whl`
    - size `26483`, sha256 `9b5e84cb5303a88b2f9744b9f97fe87c70c7dd774969a8e71b096084a95cc3e5`
  - zip: `dist/SoundVault-mac-launcher-0.1.0-20260507.zip`
    - size `33356`, sha256 `70da507da292abb43a586c8c3f6c338e9e6d148af93a8e264be53ff5448b1803`
  - tar: `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz`
    - size `27647`, sha256 `76dd2e67413a521debc860737a5f1b2405219519182c79cb5f5b077059904a6d`

## latest cron iteration — 2026-05-07T12:02:16Z

### completed

- Re-inspected the live vault and sample folder metadata. Current observed shape:
  - `catalog/sounds.jsonl` plus `sounds/<music_id> - .../metadata.json` remain the canonical sound context.
  - Each packaged folder has local `.m4a`; many also have `associated_videos_manifest.json`, `videos.jsonl`, a `*-music-page.jpg`, downloaded `.mp4` clips, and per-video screenshots/JSON.
  - Latest real-data smoke observed 1,842 catalog rows / 1,826 unique indexed sounds; 1,842 rows have local audio and evidence images; 753 rows have associated-video rows; 2,234 associated videos were parsed.
- Promoted richer associated-video manifest context out of raw files:
  - `SoundRecord` now carries `source_music_url`, `music_page_title`, and `video_manifest_captured_at`.
  - `AssociatedVideo` now carries `page_title`, `captured_at`, and local `download_bytes` from manifest/download metadata.
- Updated the right detail panel to show source music URL, sound-page title, manifest capture time, and per-video page title/capture/download-size notes alongside thumbnails.
- Rebuilt the Python wheel and refreshed the existing Mac launcher zip/tar.

### verification for latest iteration

- RED check before implementation:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_build_index_promotes_video_page_titles_capture_times_and_download_bytes -q`
  - result: expected failure, `AttributeError: 'SoundRecord' object has no attribute 'source_music_url'`.
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_desktop_ui_source.py::test_desktop_surfaces_archive_context_and_video_thumbnails -q`
  - result: expected failure for missing `source music:`/`music page:`/`captured:` UI surfacing.
- Focused tests after implementation:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_desktop_ui_source.py::test_desktop_surfaces_archive_context_and_video_thumbnails tests/test_redesign_archive_model.py::test_build_index_promotes_video_page_titles_capture_times_and_download_bytes -q`
  - result: `2 passed in 0.13s`
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py tests/test_desktop_ui_source.py -q`
  - result: `14 passed in 0.10s`
- Full test suite:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q`
  - result: `53 passed in 0.64s`
- Lint:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .`
  - result: `All checks passed!`
- Real-data indexer/DB smoke:
  - `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ...`
  - result: `indexed 1842`, `unique_stats 1826`, newest DB rows are packaged on `2026-05-07T12:01:22.744043Z`, `2026-05-07T12:00:38.137551Z`, and `2026-05-07T11:59:55.366202Z` with local audio present.
  - rich manifest context result: `with_page_title 1841`, `with_source_music 1841`, `video_total 2234`.
- Build:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation`
  - result: rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`
- Mac launcher refresh:
  - `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py`
  - verified wheel is present in the `.app` wheelhouse, `Info.plist` executable is `SoundVault`, and launcher mode is executable (`0o755`).
  - wheel: `dist/sound_vault_desktop-0.1.0-py3-none-any.whl`
    - size `26883`, sha256 `cfa28c7585e481eaae6dc73ec9bd27de7e2f4965fc8f696f886ecf083ea5fae9`
  - zip: `dist/SoundVault-mac-launcher-0.1.0-20260507.zip`
    - size `33756`, sha256 `c09db75997eb324accc0782407b33e6a5d1583923bbeddc187af6ced6f424e34`
  - tar: `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz`
    - size `28048`, sha256 `5a7a366666abeb5b2b279a9299f725f4c52e0e612dd4430778475fe0a07eea5b`

## blockers / caveats

- Linux host cannot GUI-click-through the PySide app here because `libEGL.so.1` is missing. Tests/lint/build pass, but actual Qt Multimedia playback still needs macOS click-through from the refreshed launcher.
- The refreshed Mac artifact is still an unsigned launcher bundle built from Linux, not a notarized standalone `.app`. True standalone signed/notarized macOS packaging still requires a macOS build/signing host.
- There are broad pre-existing uncommitted changes in the repo from earlier cron/build loops; this run preserved them and only layered targeted fixes/tests/docs/artifact refresh.

## next priorities

1. Mac click-through: launch the refreshed `.app`, verify PySide Multimedia plays `.m4a`, seek/progress updates, and screenshots render correctly.
2. Replace placeholder Review/Collections/Worker views with real data or remove them until they earn their chrome.
3. Improve associated-video cards beyond table icons: larger thumbnail cells/cards, local `.mp4` open/play affordances, and stats/download diagnostics from `videos.jsonl`.
4. Add evidence playback probes and scraper/import hooks for true album/sound artwork; current images are evidence screenshots, not guaranteed cover art.
5. Reconcile catalog/dedup semantics: early run saw 1,480 rows; latest live build sees 1,659 records. Clarify whether new packaging during the morning is expected and make CLI/UI counts tell one story.


## latest cron iteration — 2026-05-07T14:09:59Z

### completed

- Re-inspected the live vault again while the packaging corpus continued growing. Current observed shape: `catalog/sounds.jsonl` has 2,023 rows, 2,007 unique catalog IDs, and 16 duplicate catalog rows; packaged folders contain local `.m4a`, `metadata.json`, `associated_videos_manifest.json`, `videos.jsonl`, music-page screenshots, and per-video screenshots/clips where available.
- Fixed catalog/index semantics so the archive model now deduplicates repeated TikTok music IDs before handing records to the UI/index DB. For duplicate catalog rows, the newest `packaged_at`/`saved_at` row wins; if dates are tied/missing, the later catalog row wins. This keeps the Library count aligned to unique local sounds instead of showing repeated historical package rows.
- Preserved existing malformed-row tolerance and catalog-order behavior for non-duplicate rows; DB search still sorts by newest packaged/added date for the Library table.

### verification for latest iteration

- RED check before implementation:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_build_index_deduplicates_catalog_rows_to_newest_packaged_sound -q`
  - result: expected failure, `assert 2 == 1`, proving duplicate catalog rows were being surfaced as separate sounds.
- Focused GREEN checks:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_build_index_deduplicates_catalog_rows_to_newest_packaged_sound tests/test_production_hardening.py::test_catalog_skips_malformed_rows_and_keeps_valid_records tests/test_production_hardening.py::test_index_rebuild_dedupes_duplicate_music_ids_last_record_wins -q`
  - result: `3 passed in 0.13s`
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py -q`
  - result: `6 passed in 0.08s`
- Full test suite:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q`
  - first result after naive dedupe: 2 regressions caught in production-hardening tests (catalog order and last-row-wins ties); fixed immediately.
  - final result: `54 passed in 0.70s`
- Lint:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .`
  - result: `All checks passed!`
- Real-data indexer/DB smoke:
  - `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ...`
  - result: `catalog_rows 2023; unique_catalog_ids 2007; duplicate_catalog_rows 16`
  - result: `unique indexed 2008; duplicate ids after dedupe 0`
  - result: `audio 2008; evidence_images 2008; manifests/context 2008; associated_video_rows 2186`
  - newest DB rows: `6987903970886961925:2026-05-07T14:08:52.594156Z`, `6972202367525586945:2026-05-07T14:08:09.181271Z`, `6971276585076919045:2026-05-07T14:07:27.906777Z`, all with local audio.
- GUI import/click-through smoke:
  - `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ...`
  - result: still blocked on this Linux host with `ImportError: libEGL.so.1: cannot open shared object file`; macOS click-through is still required for real Qt Multimedia playback verification.
- Build:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation`
  - result: rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`
- Mac launcher refresh:
  - `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py`
  - verified wheel is present in both zip/tar archives, `Info.plist` executable is `SoundVault`, and launcher mode is executable (`0o755`).
  - wheel: `dist/sound_vault_desktop-0.1.0-py3-none-any.whl`
    - size `27041`, sha256 `558a9440bb13a81fb5c7fdcd9faf216763b251eb5c53d477c1fc6c3ddcdbc3ab`
  - zip: `dist/SoundVault-mac-launcher-0.1.0-20260507.zip`
    - size `33914`, sha256 `980c2b31a68428ae7d7d3f583d7f5e3a1580800dc66556673db805103ea77d5e`
  - tar: `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz`
    - size `28204`, sha256 `c699860480b8e64856f3f5bf62ad6613fb7631b91944caa83a35f06541f46263`

### blockers / caveats

- Linux host still lacks `libEGL.so.1`, so GUI construction/click-through and actual in-app `.m4a` playback remain unverified here.
- Mac launcher is refreshed from Linux but remains unsigned/not-notarized. Signed standalone packaging still requires a macOS signing/build host.
- Repo still contains broad pre-existing uncommitted changes from earlier redesign/QA loops; this run preserved them and only made targeted dedupe/test/doc/artifact changes.

### next priorities

1. macOS click-through from the refreshed launcher: verify Qt Multimedia playback, pause/resume, seeking, and evidence thumbnails on real local `.m4a` files.
2. Replace placeholder Review/Collections/Worker views with real archive data; Review queues should probably group by status/source confidence/missing evidence.
3. Upgrade associated videos from table rows/icons to proper retro clip cards with larger screenshots, local `.mp4` affordances, and download diagnostics.
4. Tighten catalog/data counting UX so the app can explicitly show catalog rows vs unique indexed sounds vs packaged folders without confusing the operator.

## latest cron iteration — 2026-05-07T16:16:49Z

### completed

- Re-inspected the live vault and fixed the previous ad-hoc inspection bug that counted only `id`; the real catalog key is `tiktok_music_id`.
- Added `CatalogStats`/`inspect_catalog_stats()` so the app can report catalog rows, unique catalog IDs, duplicate catalog rows, malformed rows, and packaged sound folders without conflating them with indexed Library rows.
- Surfaced catalog-vs-unique counts in the desktop chrome via a new Catalog stat card and in Worker Status archive-health rows. This closes the "counts tell one story" gap without disturbing the legacy `stats_text()` summary expected by existing tests.
- Current live vault shape: `catalog_rows 2052; unique_catalog_ids 2036; duplicate_catalog_rows 16; malformed_rows 0; packaged_sound_folders 2036`; deduped unique index has `2036` records, all with local audio/evidence/context, and `2186` associated-video rows.

### verification for latest iteration

- RED check before implementation:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_inspect_catalog_stats_reports_rows_unique_duplicates_and_packaged_folders tests/test_desktop_ui_source.py::test_desktop_surfaces_catalog_row_vs_unique_index_counts -q`
  - result: expected import/source failures for missing catalog stats API and desktop count surfacing.
- Focused GREEN checks:
  - same focused command
  - result: `2 passed in 0.10s`
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py tests/test_desktop_ui_source.py tests/test_library_view_model_inbox.py -q`
  - result: `26 passed in 0.15s`
  - regression check after preserving legacy summary string:
    - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_redesign_archive_model.py::test_inspect_catalog_stats_reports_rows_unique_duplicates_and_packaged_folders tests/test_desktop_ui_source.py::test_desktop_surfaces_catalog_row_vs_unique_index_counts tests/test_finish_blockers.py::test_view_model_async_rebuild_runs_in_background_and_updates_index tests/test_library_view_model.py::test_library_view_model_rebuilds_index_and_selects_preview -q`
    - result: `4 passed in 0.41s`
- Full test suite:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q`
  - result: `78 passed in 0.70s`
- Lint:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .`
  - result: `All checks passed!`
- Real-data indexer smoke:
  - `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ...`
  - result: `catalog_rows 2052; unique_catalog_ids 2036; duplicate_catalog_rows 16; malformed_rows 0; packaged_sound_folders 2036`
  - result: `unique indexed 2036; duplicate ids after dedupe 0`
  - result: `audio 2036; evidence_images 2036; manifests/context 2036; associated_video_rows 2186`
  - newest records: `6775886991297940230:2026-05-07T14:28:32.687250Z`, `6739962169779161861:2026-05-07T14:27:48.898754Z`, `6964101918863969030:2026-05-07T14:27:07.220457Z`, all with local audio.
- GUI import/click-through smoke:
  - `QT_QPA_PLATFORM=offscreen PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python - <<'PY' ... SoundVaultWindow ...`
  - result: still blocked on this Linux host with `ImportError: libEGL.so.1: cannot open shared object file`; macOS click-through remains required for actual Qt Multimedia playback verification.
- Build:
  - `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation`
  - result: rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`
- Mac launcher refresh:
  - `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py`
  - verified wheel is present in both zip/tar archives, `Info.plist` executable is `SoundVault`, launcher mode is `0o755`.
  - wheel: `dist/sound_vault_desktop-0.1.0-py3-none-any.whl`
    - size `29694`, sha256 `bf45eb78d1f7aef6f8783e2fbf05dc48e0a901a33b851778ba7a46a9ea762f14`
  - zip: `dist/SoundVault-mac-launcher-0.1.0-20260507.zip`
    - size `36567`, sha256 `9b62c8984ff6b9aa843572f240727af9b088c41ef97016dde4471ae31ece2c3a`
  - tar: `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz`
    - size `30855`, sha256 `37b82227eb73b057996cdf5cf05fff3ccd8a2d67039cd913d63233abd91a9f20`

### blockers / caveats

- Linux host still lacks `libEGL.so.1`, so GUI construction/click-through and actual in-app `.m4a` playback remain unverified here.
- Mac launcher is refreshed from Linux but remains unsigned/not-notarized. Signed standalone packaging still requires a macOS signing/build host.
- Repo still contains broad pre-existing uncommitted changes from earlier redesign/QA loops; this run preserved them and only made targeted catalog-stats/test/doc/artifact changes.

### next priorities

1. macOS click-through from the refreshed launcher: verify Qt Multimedia playback, pause/resume, seeking, evidence thumbnails, and the new Catalog stat card against the real vault.
2. Upgrade associated videos from table rows/icons to proper retro clip cards with larger screenshots, local `.mp4` affordances, and download diagnostics.
3. Replace or deepen the remaining real-but-basic Review/Worker views; Review queues should group by status/source confidence/missing evidence.
4. Add scraper/import hooks for true album/sound artwork; current images are evidence screenshots, not guaranteed cover art.
