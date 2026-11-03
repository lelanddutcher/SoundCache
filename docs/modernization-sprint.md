# Modernization sprint: the Library track list

Status: **planning** (started 2026-11-03). Owner: Leland.

Design mockup (private canvas until shared):
https://claude.ai/artifact/H1EcXjYznBaPx8Z8jMR58q. It has two boards: the Library screen
(interactive) and a row anatomy that maps each column to its data.
Reference screenshots: [`docs/design/reference/`](design/reference/README.md).

## Goal

Replace the spreadsheet-style Library table with a dense track list: one row per sound
with art, play, title and artist, a waveform, length and BPM, tags, uses and actions.
Keep it fast with tens of thousands of sounds, keep it file-native, and test every step
on Linux and macOS in GitHub Actions.

## Where we start

- The Library is already a model-backed `QTableView` (`SoundTableView`,
  `src/sound_vault/ui/desktop.py`). The new list can **paint rows with a delegate on
  the same model**, so this is not a rewrite.
- `desktop.py` is 6,470 lines. The sprint splits out the parts it touches. It is not a
  full refactor.
- The index (`db/index_db.py`) already has nearly every column the row needs: `title`,
  `artist`, `tags`, `usage_count`, `duration_seconds`, `added_at`, `artwork_path`,
  `transcript_text`, `associated_video_count`. **Missing: waveform peaks and BPM.**
- CI today covers only the relay worker (`relay-worker.yml`). This sprint adds
  `app-ci.yml`: the full Python suite on `ubuntu-latest` and `macos-latest` (Apple
  Silicon), with Qt headless.

## The row

| # | Column | Source | Work |
|---|--------|--------|------|
| 1 | Artwork | `artwork_path`, as a cached 112 px thumbnail | thumbnail cache |
| 2 | Play/pause | the existing player | wire to the delegate |
| 3 | Title, artist, NEW | `title`, `artist`; NEW when `added_at` is under 7 days | — |
| 4 | Vocals, videos | mic if `transcript_text` has words; camera if `associated_video_count` > 0 | — |
| 5 | Waveform | **new** `waveform.json` (about 400 peaks) beside the audio | compute at import, backfill |
| 6 | Length · BPM | `duration_seconds`; **new** `bpm` in `metadata.json` and the index | tempo estimate |
| 7 | Tags | `tags`, two short lines | — |
| 8 | Uses | `usage_count`, compacted (2.1M) | — |
| 9 | Actions | favorite, add to bin, drag/reveal, copy link, more | reuse existing actions |

Filter bar: Tags, Platform, Length, BPM and Bins chips; a **Vocals only** toggle; a
sort (Most used / Recently cached / Title A–Z); and a Filters button for the existing
advanced filters. A now-playing bar with a full waveform stays at the bottom.

## Workstreams and tickets

Sizes: S ≈ half a day, M ≈ 1–2 days, L ≈ 3+ days.

### 0. CI and dev loop (do first)
- **CI-1 (S)** `app-ci.yml`: pytest on Linux and macOS, Qt offscreen. *Done in this
  branch. Watch the first run and fix anything macOS-only.*
- **CI-2 (S)** Get `ruff check src tests scripts` to zero (41 findings today), then
  make it a CI gate.
- **CI-3 (M)** Fixture vault generator (`tests/fixtures/make_vault.py`): about 50
  sounds with synthetic audio (sine, noise and clicks at known tempos, made with
  numpy), artwork tiles and metadata. No real or copyrighted audio. Tests,
  screenshots and benchmarks all use it.
- **CI-4 (M)** Screenshot job: render the Library offscreen at 1440×900 from the
  fixture vault, in both themes. Upload the PNGs as a workflow artifact on every PR,
  so a reviewer sees the UI change without building the app. Start by comparing to
  stored images with a pixel tolerance; only fail on large diffs.
- **CI-5 (M)** macOS packaging smoke: build the `.app` unsigned with PyInstaller on
  `macos-latest` and launch it with a `--self-test` flag (new, exits 0 after the
  window shows). Signing and notarization stay local. Secrets never reach PR jobs in a
  public repo.
- **CI-6 (S)** Fix `uv.lock`. It still records version 0.3.0. Merge
  lelanddutcher/SoundCache#2 (and close #3), then have CI check that the lock is
  current.

### 1. Data: waveform and BPM
- **DATA-1 (M)** `vault/waveform.py`: decode with ffmpeg to mono 8 kHz, compute about
  400 min/max peaks, and write `waveform.json` beside the audio (file-native; the index
  only caches it). Hook it into import.
- **DATA-2 (M)** `vault/tempo.py`: BPM from an onset-strength autocorrelation in numpy
  (no librosa). Store the value with a confidence score; below the threshold, show "—".
  Fixture sounds at known tempos are the test oracle (±2 BPM).
- **DATA-3 (S)** Index schema: add `bpm REAL` and `waveform_path TEXT`, with a
  migration. Rebuilding stays possible from `metadata.json`.
- **DATA-4 (M)** Backfill as a background enrichment pass, the same as transcripts:
  resumable, with progress shown in Worker status.

### 2. The track-list view
- **UI-1 (L)** `ui/track_list.py`: a `QStyledItemDelegate` that paints the whole row
  (art, text, icons, waveform, actions) from the existing model. Hit-testing for play,
  waveform seek and the action icons. Target: 60 fps scrolling with 20k rows on an
  M1 MacBook Air.
- **UI-2 (M)** Thumbnail and waveform caches (LRU, loaded off the UI thread) so
  scrolling never decodes images or reads JSON synchronously.
- **UI-3 (M)** Filter bar: chips, Vocals only, sort, and Filters (the existing
  advanced panel). Filter state lives in the view model, which already filters.
- **UI-4 (M)** Now-playing bar: full waveform scrubber, previous/next, keyboard (Space
  play, ←/→ seek, F favorite).
- **UI-5 (S)** Table/list switch in the toolbar. Keep the current table as the
  fallback until UI-1 is stable.
- **UI-6 (M)** Accessibility: every row action reachable by keyboard, accessible
  names on icon buttons, text contrast of at least 4.5:1 (the mockup's grey #c5b3e6 on
  #0a0518 passes).

### 3. Split `desktop.py` (as touched)
- **REF-1 (M)** Move the Library view, filter bar and player into `ui/library/` and
  leave the rest. Each move ships with its tests green on both OSes.

## Testing plan

| Layer | What | Where |
|-------|------|-------|
| Unit | waveform peaks, tempo on fixtures, index migration, view-model filter/sort | Linux + macOS |
| GUI (offscreen) | delegate hit-testing, keyboard, filter bar state, now-playing | Linux + macOS |
| Visual | screenshots of the fixture vault, uploaded per PR, tolerant diff | Linux (fonts pinned) |
| Perf | scroll/paint benchmark with 20k fixture rows; fail on a >20% regression | macOS |
| Packaging | unsigned `.app` build plus `--self-test` launch | macOS |
| Manual | real vault on Leland's Mac before each release | local |

Rules: no test may touch the network or the real relay (set
`SOUND_VAULT_DISABLE_RELAY_POLL`); fixtures only, no real audio in the repo; every
UI-* ticket adds a screenshot to the visual job.

## Order (two weeks)

1. **Days 1–2:** CI-1 green on both OSes, CI-2, CI-3, CI-6.
2. **Days 3–5:** DATA-1, DATA-2, DATA-3; CI-4 screenshot job.
3. **Days 6–9:** UI-1, UI-2, UI-5. Demo with a switch between the old and new views.
4. **Days 10–12:** UI-3, UI-4, DATA-4 backfill, UI-6.
5. **Days 13–14:** perf pass, CI-5 packaging smoke, REF-1 cleanup, then release 0.5.0.

## Release checklist (0.5.0)

- Bump the version in `pyproject.toml` and `src/sound_vault/__init__.py`.
- Build, sign and notarize locally; upload to GitHub Releases.
- Update `soundcache-web/latest.json` to 0.5.0 with release notes. It is the in-app
  update feed, and it lagged two releases behind before 2026-11-03.
- Include the relay-pairing migration (users who paired before November 2026 are asked
  to re-pair).

## Risks

- **Delegate painting is more code than widgets.** Keep the old table behind the switch
  until UI-1 passes the perf target and accessibility review.
- **BPM is wrong on short or arrhythmic clips.** Show "—" below the confidence
  threshold instead of guessing.
- **macOS runner minutes.** Free for public repos, but jobs are slower. Linux runs on
  every push; the macOS packaging job runs on PRs to `main` and on tags only.
- **Fonts differ across OSes**, which breaks pixel diffs. Pin the bundled Quicksand and
  Unbounded in the screenshot job and diff on Linux only.
