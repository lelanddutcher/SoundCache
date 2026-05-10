# Sound Vault QOL polish progress — 2026-05-08

No user-facing build shipped. This pass adds source-level and tested UI/model improvements while ASR continues in the background.

## Implemented

- Library status filter:
  - All statuses
  - Approved
  - Needs review
  - Unreviewed
- Evidence filters:
  - Has evidence
  - Missing evidence
- Review queue drill-down:
  - Review queue rows now carry filter targets.
  - Double-clicking a review row jumps to Library and applies the matching status/media filter.
- Selection/scroll/sort preservation:
  - Refreshing search/filter results now tries to keep the selected sound, scroll position, and sort state instead of snapping back to row 0.
- Copy metadata workflow:
  - Preview panel has a Copy metadata button.
  - Copies deterministic editor-friendly metadata: sound, artist/source, music ID, status, usage count, canonical URL, folder/audio/artwork paths, tags, and quality gaps.
- Keyboard shortcuts:
  - Find focuses search.
  - Copy copies selected metadata.
  - Space toggles play/pause for the selected sound.
  - Escape clears search.
- Persisted library search state:
  - Query, duration filter, media filter, status filter, usage filter, and selected music ID are saved in app settings.
- Popularity filtering:
  - Library can filter unknown usage, under 1K uses, 1K+ uses, 100K+ uses, and 1M+ uses once OCR/backfill populates `usage_count`.
- Column visibility:
  - Library has a Columns menu with persisted hidden columns, while the core sound-title column is protected from being hidden.
- Right-click quick actions:
  - Library rows expose a context menu for play/pause, copy metadata, copy local audio path, copy canonical URL, and open sound folder.
- Fresh retro UI direction:
  - Added an iTunes-5/Aqua-inspired smooth-metal header deck with a dark capsule “Now Playing” display.
  - Added LimeWire-2001-inspired early-web portal tabs, lime/blue status language, source-list grouping, compact Verdana-era typography, beveled controls, and crisp white panes.
  - Reworked the app palette away from beige into cool OS-X/early-web gray, deep blue navigation, and electric lime status/action accents.
- Index/model hardening:
  - Exact `preview_for(music_id)` lookup avoids fuzzy-search mismatches.
  - SQLite index now preserves source music URL, music page title, and captured-at context across restarts.

## Verification

- RED tests added first for new behavior.
- Focused new tests: `12 passed`.
- Related desktop UI source tests: `24 passed in 0.05s`.
- Full suite with repo root import path: `107 passed in 5.70s`.
- Ruff: `All checks passed!`.
- Offscreen visual screenshot could not be captured in this Linux container because PySide import requires missing `libEGL.so.1`; source/tests are verified, but macOS visual QA remains pending.

## Background worker status at this checkpoint

- ASR process: completed (`proc_0e6e3f1f47de`, exit code 0).
- ASR worker summary: `processed 2036; text=1363; empty=673; errors=0`.
- Verified transcript sidecars on disk: `2036 / 2036`.
- Verified text transcripts: `1363`.
- Verified empty/no-speech transcript sidecars: `673`.
- Verified transcription errors: `0`.
- Rebuilt desktop SQLite index at `/opt/data/home/.local/share/sound-vault/index.sqlite3` with `2036` records.
- Verified indexed transcript coverage: `2036` transcript paths, `1363` searchable transcript texts, `673` empty transcript texts.
- Smoke-tested real transcript search phrases: `Ballerina cappuccina`, `rats do`, and `How am I supposed to leave` returned indexed hits.
- Targeted transcript/index/view-model tests: `10 passed in 1.30s`.
- True artwork files previously counted: `573 / 2036`.
- Artwork process: previously running (`proc_28fc582f7c5e`); needs fresh recount before claiming current artwork completion.

## Still blocked before next app handoff

- Complete/verify ASR transcription lane.
- Extract and backfill real TikTok usage counts from music-page screenshots.
- Re-index real metadata and verify transcript/popularity search/sort in app.
- Package only after requested lanes are functionally complete.
