# Sound Vault QA response build - 2026-05-16a

This build is the first core performance/stability rewrite slice under the new retooling goal.

## fixed in build 20260516a

- Added a rebuildable SQLite FTS5 search cache, `sounds_search`, so search no longer relies on broad `%LIKE%` scans.
- Rebuilds now create `sounds_rebuild` and `sounds_search_rebuild` before swapping either table into place.
- Added query indexes for common Library filters and sort inputs.
- Replaced thousands of per-row Library play button widgets with a `PlayButtonDelegate` that paints and handles button-like controls.
- Removed per-row filesystem checks from Library refresh and playability sorting. Actual path validation still happens at playback time.
- Added `scripts/profile_desktop_performance.py` for repeatable offscreen profiling against fixture or real vaults.
- Added `docs/performance-architecture-audit-2026-05-16.md` with findings, before/after numbers, and next architecture moves.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260516a.tar.gz
sha256: e137efd8acc042c53d981df13122a234efaff93323fbdd294ff9851b4cc10db7
size: 61,486 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260516a.zip
sha256: 6eb0fb62d2d2a6ebe0f72b0abccf211dcd6c9aba07b90aa958b17d1f6cf4d6be
size: 73,288 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 8136d3561b8d84170c08ff7c334def7b27de8d651522832596813ab1994db1d8
size: 58,289 bytes
```

## real-vault performance profile

Measured against `/Volumes/hermes-share/TikTok Sound Vault` with `2,036` indexed records.

Before this pass:

```text
initial refresh: 469.74 ms
search "b": 1083.77 ms
search "bi": 513.08 ms
search "bitch": 314.95 ms
search "needle": 137.40 ms
```

After this pass:

```text
initial refresh: 197.68 ms
search "b": 185.02 ms
search "bi": 68.29 ms
search "bitch": 60.86 ms
search "needle": 18.00 ms
```

Installed-wheel harness:

```text
search "b": 180.03 ms
search "bi": 69.66 ms
search "bitch": 57.15 ms
search "needle": 20.66 ms
```

## verification

Full suite:

```text
pytest -q
153 passed
```

Targeted FTS/search/table tests:

```text
pytest -q tests/test_index_db.py tests/test_desktop_gui_workflows.py::test_desktop_gui_qa_harness_exercises_core_editor_workflows tests/test_desktop_gui_workflows.py::test_continuous_play_advances_through_visible_playable_rows tests/test_desktop_gui_workflows.py::test_random_transport_selects_and_plays_a_random_playable_row tests/test_desktop_ui_source.py::test_desktop_library_has_duration_filters_visible_counts_and_real_row_play_controls tests/test_desktop_ui_source.py::test_desktop_chrome_controls_are_wired_to_real_behavior
14 passed
```

Installed-wheel GUI smoke:

```text
indexed: 2036
delegate: PlayButtonDelegate
focused search for "bitch": preserved
rows after search: 252
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel;
- launcher scripts are executable mode `700`.
