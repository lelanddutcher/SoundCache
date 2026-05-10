# Sound Vault revision brief — 2026-05-08

Leland’s QA verdict: the app is not earned yet. The previous pass fixed plumbing, but not the product.

## Non-negotiable product read

Sound Vault is a local editor archive/player for TikTok sounds. It needs to help an editor find a sound by memory, phrase, vibe, duration, source context, and trend usage. It should not feel like a generic admin table with beige paint.

## Design direction

Reference lane:

- early iTunes / brushed-metal Mac media library
- LimeWire-style searchable file/media catalog
- Winamp/media-player affordances where useful, but not skin cosplay
- tactile local archive: list, bins, right inspector, playable media, useful file paths

External references found for the next design pass:

- 512 Pixels — “The Brushed Metal Diaries: Et Tu, iTunes?”: https://512pixels.net/2013/04/brushed-metal-itunes/
- Robservatory iTunes 4.9/5 UI comparison: https://robservatory.com/and-then-there-were-seven/
- Wikimedia LimeWire screenshot: https://commons.wikimedia.org/wiki/File:LimeWire_screen.png
- Winamp/media-player design history/search references from Winamp forums + Dribbble search

The art direction should be: compact, dense, editor-first, tactile. Not neon. Not dashboard cards. Not fake retro decoration.

## Current failures to fix

1. **Library access**
   - Must load the whole indexed catalog, not an arbitrary visible slice.
   - Current real data: 2,052 catalog rows / 2,036 unique IDs / 2,036 packaged folders.
   - UI must always show visible count vs indexed count.

2. **Columns**
   - User must be able to resize columns dynamically.
   - Table layout should persist.
   - Sound title/filename must be readable. No emoji prefix masking the useful name.
   - Add useful filters/sorts: duration buckets, has audio, has artwork, has transcript/spoken word, has associated videos.

3. **Playback**
   - Row play control must be an actual button, not text in a column.
   - Right-side play button can stay, but table play must be direct.
   - Scrubber click should seek immediately while playing.

4. **Artwork**
   - Current preview art is often just an evidence screenshot.
   - Scraper must capture the actual TikTok music-page artwork/cover visible near the sound title.
   - Store as `artwork.jpg/png/webp` plus `artwork_manifest.json`.
   - Metadata schema: `paths.artwork`, `paths.artwork_manifest`, `assets[].asset_type = artwork`.
   - Desktop inspector should prefer `record.artwork_path` before evidence screenshots.

5. **Associated videos**
   - Inspector must surface downloaded associated/example videos with creator, URL, local path, screenshot/thumb, notes, capture status.
   - Missing/partial associated video coverage should become a review queue, not invisible failure.

6. **Spoken word / phrase search**
   - Add transcript sidecars for playable audio.
   - Any spoken words/catchphrases must be included in search metadata.
   - Schema: `transcript.json`, `paths.transcript`, `speech_transcript.text`, language, engine/model, timestamped segments.
   - Desktop search placeholder and DB index already need to treat transcript text as first-class searchable metadata.

## Acceptance criteria for next usable Mac build

- Opening the latest Mac launcher indexes 2,036 unique sounds from the current vault.
- Empty search displays all records and shows `2,036 displayed / 2,036 indexed` or equivalent.
- Sound column is readable and does not start with `♬`/emoji sludge.
- User can resize/move columns and layout survives restart.
- Table play column contains clickable buttons.
- Scrubber click seeks while audio is playing.
- Inspector artwork uses true `artwork.*` when present, screenshots only as fallback.
- At least a small artwork backfill batch has succeeded and can be viewed in-app.
- Transcript script exists, writes sidecars, and index/search picks them up.
- Tests, ruff, wheel build pass before shipping a refreshed Mac launcher.
