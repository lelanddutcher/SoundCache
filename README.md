# TikTok Sound Vault

**TikTok Sound Vault is a private, local-first sound intelligence library for editors.**

It turns messy TikTok saved sounds, music-page URLs, screen recordings, local audio, artwork, transcripts, and example videos into a file-native vault that can be searched, audited, packaged, and used inside real editing workflows without depending on TikTok as the source of truth every time.

This is not a public TikTok downloader. It is a private editor/source-intelligence system: recover the back catalog, preserve evidence, make sounds findable by vibe/phrase/popularity/source, and keep every useful artifact browsable in Finder/NAS even if the app is not running.

## North Star

Build the **Sound Library for short-form editors**:

- a local archive of TikTok sounds that survives platform churn, broken links, missing metadata, and app UI changes;
- rich enough to answer “what is this sound, where did it come from, how is it used, and do we trust it?”;
- fast enough that an editor can search by title, artist, phrase, vibe, usage count, status, transcript, or local evidence while cutting;
- practical enough that audio files, folders, and sidecars remain useful in Finder, Premiere, Resolve, or a NAS share without opening the app;
- private by default, with hosted/mobile handoff only where it reduces friction.

The product shape is: **private vault first, desktop librarian second, capture/relay third, public platform never until the private workflow is excellent.**

## Current product

This repo is the desktop/librarian app and relay layer for the file-native vault.

It currently supports:

- indexing an existing `TikTok Sound Vault` folder;
- catalog-backed and folder-backed metadata discovery;
- SQLite as a disposable cache, not the durable source of truth;
- library search and filters over titles, artists, IDs, tags, transcripts, status, usage counts, and local media state;
- search focus/cursor preservation during table refreshes so typing remains in the search box;
- durable favorites and editor-created sorting bins stored in `catalog/library_collections.json`;
- sidebar Library drop-down with smart sorts, user bins, a plus button for new bins, and drag/drop row assignment;
- row-level favorite star controls beside row-level play controls;
- larger right-click menus with visible hover states, multi-select Add to Favorites / Add to sorting bin actions, and a Library `Mark as Duplicate` action;
- audio preview/playback paths for local assets;
- duplicate review queues backed by evidence-aware local reports, including manually marked Library groups, candidate playback, right-inspector metadata hydration, reviewed-group filtering, and reversible duplicate quarantine;
- right-inspector TikTok sound-page opening from `canonical_url`, `source_music_url`, or mobile/share URL metadata;
- associated-video hashtag extraction from captured top-video captions/cards, promoted into sound metadata and search;
- TikTok data-export favorite-sound import:
  fragment repair, normalized JSON/CSV outputs, existing-vault dedupe classification by music ID/URL, public oEmbed enrichment, and metadata-only vault packaging;
- Import/Workers dashboard controls for export import, oEmbed enrichment, metadata packaging, index rebuild, and import/package audit counts;
- archive-health views for missing/available audio, artwork, transcripts, evidence, and associated videos;
- iOS Shortcut URL handoff through a pairing-code relay;
- worker/backfill scripts for artwork, transcripts, popularity, associated videos, duplicate audits, and packaging checks;
- Mac launcher packaging for local testing.

Current Mac test build source of truth:

```text
dist/CURRENT-BEST-MAC-BUILD.txt
```

Current best Mac launcher at the time of this note: `20260518c`.

## Core principle: files are the product

The vault should remain useful if the database is deleted.

SQLite is a cache. The durable truth is:

1. `catalog/*.jsonl` / `catalog/*.csv` exports;
2. per-sound `metadata.json` sidecars;
3. `catalog/library_collections.json` for favorites and editor sorting bins;
4. local media assets and evidence files;
5. readable filenames and folder names.

A good sound folder should tell an editor what it is without a web app, database, or Slack thread.

## Vault layout

Expected vault shape:

```text
TikTok Sound Vault/
  catalog/
    imports/
      favorite_sounds_import_normalized_*.json|csv
      favorite_sounds_import_summary_*.json
      favorite_sounds_oembed_enriched_*.json|csv
    sounds.jsonl
    sounds.csv
    videos.jsonl
    assets.jsonl
  sounds/
    {music_id} - {title_slug} - {artist_slug}/
      metadata.json
      {rich_editor_filename}.m4a
      artwork.jpg|webp
      transcript.json|sound_transcript.json
      associated_videos_manifest.json
      videos/
        {rank}-{video_id}-{author}.mp4
        {rank}-{video_id}-{author}.json
      notes.md
  reports/
    duplicate-candidates.json
    duplicate-decisions.jsonl
    duplicate-quarantine/
    *_audit*.json|csv
  inbox/
    urls/
    imports/
    screen-recordings/
  workers/
    logs/
    failed/
```

Naming standard:

```text
7274985708375378731 - Geekd Up - Young Jeezy & Fabo/
Geekd Up - Young Jeezy & Fabo [TT-7274985708375378731] [football-hype] [approved].m4a
```

Rules:

- stable TikTok/music ID first for machine sorting;
- human title/artist/context in the folder and asset name;
- `[TT-{music_id}]` in filenames to prevent duplicate confusion;
- sidecars stay canonical even if filenames improve later;
- original captures and source evidence are preserved, derived previews are rebuildable.
- duplicate quarantine keeps the selected keeper folder in `sounds/` and moves only the non-keeper candidate folders into `reports/duplicate-quarantine/{timestamp}-{group}/`.

## What belongs in the vault

Each sound should eventually have:

- canonical TikTok music/sound ID;
- title and creator/artist/source attribution;
- canonical URL and source-page evidence;
- local audio preview or full captured audio where allowed/available;
- real TikTok artwork/cover image, not just a random video screenshot;
- usage count/popularity as TikTok reports it, separate from local evidence count;
- tags for mood, niche, edit use case, and trust/status;
- transcript or phrase/lyrics text when available;
- 1–3 associated/example videos to show how the sound is used;
- hashtags from the associated/example videos that explain trend niche, format, fandom, meme context, or creator community;
- duplicate/source-confidence notes when identity is uncertain.

## What does not belong

Avoid turning this into a hoarding tool or brittle scraper.

Do not make the database the only truth. Do not rely on opaque ID-only filenames. Do not treat “associated video count” as popularity. Do not overwrite valid evidence with empty retry results. Do not store credentials in project files, logs, README examples, or sidecars.

## Ingestion lanes

Preferred order:

1. **Existing packaged vault data** — catalog JSONL plus `sounds/*/metadata.json` folders.
2. **Direct TikTok music/share URLs** — normalize to music IDs and canonical URLs.
3. **TikTok data archive exports** — repair/normalize favorite-sound fragments, then enrich slowly.
4. **Authenticated browser capture** — only with explicit user permission; read-only, slow, stop on checkpoints.
5. **Local screen recordings** — use OCR/segmentation and preserve review evidence.
6. **Playback/audio capture fallback** — bounded previews when no direct asset path exists.

Every lane should leave evidence: source URL/file, timestamp, method, status, and failure notes.

### TikTok data-export import lane

The deterministic, non-authenticated back-catalog path is now app-native:

```bash
sound-vault --vault "/path/to/TikTok Sound Vault" \
  --import-favorite-sounds "/path/to/favorite sounds list.json" \
  --import-date-label 2026-05-17a

sound-vault --vault "/path/to/TikTok Sound Vault" \
  --enrich-favorite-sounds-oembed "/path/to/TikTok Sound Vault/catalog/imports/favorite_sounds_import_normalized_2026-05-17a.json"

sound-vault --vault "/path/to/TikTok Sound Vault" \
  --package-imported-sounds "/path/to/TikTok Sound Vault/catalog/imports/favorite_sounds_oembed_enriched_2026-05-17a.json"
```

The same lane is reachable from the desktop UI through **Ingest inbox** and **Worker status**.

Notes:

- the original TikTok export file is never modified;
- malformed TikTok favorite-sound JSON fragments are repaired in memory;
- normalized JSON/CSV and summaries are written under `catalog/imports/`;
- each imported row is classified as already in the vault, new to the vault, ambiguous, or not checked;
- import dedupe matches stable music IDs first, then normalized TikTok canonical/mobile/source URLs with query strings and tracking fragments stripped;
- import JSON/CSV rows include `vault_match_status`, `vault_match_reason`, `vault_match_music_id`, `vault_match_folder`, and `vault_match_url`;
- import summaries include `already_in_vault`, `new_to_vault`, `ambiguous_matches`, and `vault_match_counts`;
- oEmbed enrichment is resumable via checkpoint files and continues after per-row failures;
- oEmbed HTTPS uses the packaged `certifi` CA bundle so Python.org/Homebrew certificate state does not silently break live enrichment;
- metadata-only packages are valid searchable vault records;
- missing audio/artwork/transcripts/videos/popularity are explicit audit states, not import failures;
- packaging upserts `catalog/sounds.jsonl` and `catalog/sounds.csv` atomically and preserves existing useful package metadata.

## Desktop app role

The desktop app is the librarian, not the vault itself.

It should:

- open fast and show the library reliably;
- rebuild indexes from file-native truth;
- make missing assets obvious without inventing errors;
- let editors search by phrase, title, artist, tag, popularity, status, and source identity;
- keep search responsive and preserve keyboard focus while table results refresh;
- let editors favorite sounds, create manual sorting bins, drag rows into bins, and right-click Add to without touching the cache schema;
- provide smart sorts for favorites, missing transcript / likely instrumental, missing audio, high-popularity sounds, and associated-video evidence;
- make duplicate review actionable with Library multi-select marking, candidate playback, keeper selection, and reversible folder quarantine;
- keep the right inspector synchronized while reviewing duplicates, including title, artist/source, duration, transcript, folder/audio paths, evidence, and playable state;
- open the selected sound's TikTok music page from the right inspector so editors can inspect current videos under the sound;
- extract and search hashtags from associated video captions/cards without mixing them into editor-authored `tags`;
- avoid title/artist-only duplicate guesses when duration, transcript, artwork, thumbnail, audio path, or URL evidence says the sounds are different;
- never require login just to browse the local library;
- tolerate NAS paths, copied vaults, stale absolute paths, and rebuilt caches.

The first-launch rule: **populate the library before doing fancy enrichment.** Heavy sidecar hydration, media probing, ASR, video capture, and popularity backfills belong in explicit workers, not blocking library population.

## Design direction

The UI should feel like a tactile retro-futurist archive machine, not flat SaaS:

- brushed metal chrome;
- dark graphite inset panels;
- Aqua bevels and capsule readouts;
- fixed-frame artwork wells;
- knobs, sliders, toggles, meters;
- row-level play controls that behave like controls, not editable text;
- dense cards and table views;
- right-side evidence inspectors for metadata, artwork, transcripts, and associated videos;
- leather/paper/archive accents where useful;
- practical editor controls over decorative noise.

Reference vibe: an iTunes/LimeWire/Winamp-era local media tool rebuilt as a brushed-metal control-room workstation for source intelligence and modern creator workflows.

## Development setup

Requirements:

- Python 3.11+
- macOS, Windows, or Linux
- GUI extra: `PySide6`
- relay extra: `fastapi`, `uvicorn`, `pydantic`, `httpx`
- ASR extra: `faster-whisper`

Keep virtualenvs and dependency caches off NAS/CIFS mounts. Put environments on local disk and point `--vault` at the NAS/shared vault.

Linux/NAS example:

```bash
python3 -m venv /opt/data/venvs/sound-vault-desktop
/opt/data/venvs/sound-vault-desktop/bin/python -m pip install -U pip
/opt/data/venvs/sound-vault-desktop/bin/python -m pip install -e ".[gui,relay,asr,dev]"
```

Mac example:

```bash
python3 -m venv ~/venvs/sound-vault
source ~/venvs/sound-vault/bin/activate
python -m pip install -e ".[gui,relay,asr,dev]"
sound-vault --vault "$HOME/Documents/TikTok Sound Vault"
```

## Run

CLI smoke test:

```bash
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault "/nas/TikTok Sound Vault"
```

GUI:

```bash
sound-vault --vault "/path/to/TikTok Sound Vault"
```

Diagnostics without importing Qt:

```bash
sound-vault --diagnose --vault "/path/to/TikTok Sound Vault"
```

Default vault resolution:

1. `SOUND_VAULT_DEFAULT_VAULT`, if set;
2. saved GUI vault picker setting;
3. `/nas/TikTok Sound Vault`, if present;
4. `~/Documents/Sound Vault`.

App config/data paths can be overridden with:

```text
SOUND_VAULT_CONFIG_DIR=/path/to/config
SOUND_VAULT_DATA_DIR=/path/to/data
```

## Test and package

```bash
/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation
/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py --stamp YYYYMMDDx
```

Before handing off a Mac launcher:

- verify live `/nas` access if testing against the real vault;
- run ruff and the full test suite;
- run an installed-wheel CLI smoke against the real vault;
- run an offscreen installed-wheel Qt GUI smoke against the real vault, including search focus, transcript count, row play buttons, duplicate-review groups, and duplicate candidate playback state;
- verify archive contents, executable bits, app version, and checksum;
- update `dist/CURRENT-BEST-MAC-BUILD.txt`;
- move superseded builds into `dist/_deprecated/` with a short reason.

Useful current smoke signal for the real vault:

```text
2,036 indexed records
TRANSCRIPTS (1,363)
4 active smart duplicate groups / 8 candidate rows
search focus preserved after typing/filter refresh
first duplicate candidate playback enabled
Open TikTok sound enabled for records with canonical TikTok music URLs
Associated-video hashtag metadata promoted for 728 sounds in the current real vault
Searching `capcut` after an index rebuild returns associated-video hashtag matches
Favorites star column persists into catalog/library_collections.json
Sidebar sorting bins can be created, filtered, and populated without rebuilding SQLite
Right-click menu has large readable rows, visible hover state, and Add to submenu
Search-bar dropdown popups have explicit item foreground/background/selection colors
Popularity sort is numeric, not text-based
SQLite FTS search cache is active
Delegate-backed Library play controls avoid thousands of per-row button widgets
Real-vault search profile stays under 300 ms for the current smoke queries
Right inspector Transcript panel shows full transcript text without metadata-summary truncation
TikTok data-export import spine is available from CLI and Import/Workers UI
Metadata-only import packages remain searchable and show missing assets as audit state
Favorite-sound imports identify existing vault sounds by music ID and normalized TikTok URLs before packaging
CONT continuous playback advances through the visible Library order
RND random playback selects playable sounds from the current Library view
Duplicate Review mark/quarantine fixture smoke passes
Library multi-select can create manual Duplicate Review groups
```

## Relay

The relay is for moving URLs from mobile/Shortcut workflows into the desktop inbox. It is intentionally thin.

Local relay:

```bash
PYTHONPATH=src SOUND_VAULT_RELAY_HOST=127.0.0.1 SOUND_VAULT_RELAY_PORT=43117 \
  /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.relay.server
```

Docker relay:

```bash
docker build -f Dockerfile.relay -t sound-vault-relay .
docker run --rm -p 43117:43117 -v sound-vault-relay-data:/data \
  -e SOUND_VAULT_RELAY_STORAGE_PATH=/data/relay.sqlite3 sound-vault-relay
curl http://127.0.0.1:43117/v1/health
```

Relay security posture:

- URLs only, no media files;
- no TikTok credentials;
- device secret stays desktop-side;
- unknown pair codes are rejected;
- SQLite relay persistence via `SOUND_VAULT_RELAY_STORAGE_PATH`;
- rate limiting via `SOUND_VAULT_RELAY_RATE_LIMIT` and `SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS`;
- logs mask pair codes and redact device secrets/tokens.

## Roadmap

Near-term:

- make the Mac launcher boringly reliable;
- keep first-launch indexing fast and crash-resistant;
- add visible duplicate quarantine history, restore, and final delete/merge workflows;
- improve worker logs, retry queues, and partial-failure recovery;
- preserve file-native exports as the non-negotiable source of truth.

Next:

- better local ASR/phrase search UX;
- richer popularity/source-confidence views;
- safer associated-video enrichment and QC;
- stronger batch audit reports for missing/partial evidence;
- optional hosted relay for low-friction mobile saves.

Later:

- signed/notarized Mac app;
- cross-platform desktop packaging;
- iOS Shortcut polish;
- optional shared/private team vault workflows.

## Non-goals for now

- public TikTok scraping platform;
- mass downloading without evidence and review;
- storing user credentials;
- making the hosted relay responsible for media;
- replacing the file-native vault with a cloud-only database.

## Status

Private alpha. The app and workers are built around a real local vault and are evolving quickly. If the GUI and the files disagree, trust the files and fix the indexer/cache path. If a build breaks basic library population, restore the fast V1 indexing path before adding richer UI or enrichment.
