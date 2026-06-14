# Performance and architecture audit - 2026-05-16

## Goal

Retool Sound Vault from prototype behavior toward a performant, reliable, file-native editor workstation. The first pass focused on the search/index/table path because it is the most obvious daily-friction loop: typing in the search bar should not stall the app.

## Findings

- Search was using `LIKE '%query%'` against a large denormalized text column. That is simple but does not scale well, especially while the user is typing.
- Every Library refresh rebuilt the full `QTableWidget` row set on the UI thread.
- The Library play column created a real `QPushButton` widget for every visible row on every refresh.
- Every Library refresh called the playback resolver per row, which performed filesystem existence checks. On NAS/shared-drive vaults this is a major latency risk.
- Sorting by playability also called the playback resolver, adding more path checks to refresh/sort operations.
- The UI remains PySide/Python for this slice, but the audit confirms the next serious architectural step should be a model/view table backed by a query model or thin cache layer. The current widget table can be made faster, but it is not the ideal long-term table architecture.

## Changes made in this pass

- Added a rebuildable SQLite FTS5 table, `sounds_search`, alongside the existing `sounds` cache table.
- Search queries now use tokenized prefix FTS instead of broad `%LIKE%` scans.
- Rebuilds create `sounds_rebuild` and `sounds_search_rebuild` first, then swap them into place only after both are populated, preserving the last-good cache on failure.
- Added query indexes for common filters: status, usage, duration, local audio, artwork, associated videos, and packaged/added dates.
- Replaced per-row Library `QPushButton` widgets with a `PlayButtonDelegate` that paints and handles a button-like play control without constructing thousands of widgets.
- Library refresh now uses indexed/cached playable pointers for row display and sorting. Actual path validation still happens when the user plays a sound.
- Added `scripts/profile_desktop_performance.py` for repeatable offscreen startup/search profiling.
- Added regression tests for FTS cache creation, punctuation-safe searches, legacy-cache migration, delegate-backed row play controls, and transport behavior.

## Real-vault before/after signal

Measured against `/Volumes/hermes-share/TikTok Sound Vault` with `2,036` indexed sounds in offscreen Qt.

Before this pass:

```text
initial refresh: 469.74 ms
search "b": 1083.77 ms / 2,036 rows
search "bi": 513.08 ms / 444 rows
search "bitch": 314.95 ms / 252 rows
search "needle": 137.40 ms / 2 rows
```

After FTS plus delegate-backed play controls:

```text
initial refresh: 197.68 ms
search "b": 185.02 ms / 1,351 rows
search "bi": 68.29 ms / 403 rows
search "bitch": 60.86 ms / 252 rows
search "needle": 18.00 ms / 1 row
```

## Remaining architecture work

- Move Library from `QTableWidget` to `QAbstractTableModel`/`QTableView` so refreshes update model data instead of rebuilding widget items.
- Run search on a cancellable worker so slow disks or huge vaults cannot block the UI thread.
- Add a search result limit plus virtual paging for very large vaults.
- Add latency budgets to CI smoke tests where stable enough, and keep the script-based real-vault profile for local build gates.
- Consider Rust/Tauri or a Rust sidecar only after the model/view rewrite and FTS path are exhausted. The current evidence says the biggest immediate wins are table architecture and indexed search, not a language switch by itself.
- Replace remaining per-row widgets in duplicate review with delegates if duplicate reports grow large.
- Continue redesigning the chrome, but do not let retro visuals add widget count or repaint cost to high-frequency paths.
