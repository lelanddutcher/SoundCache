# Sound Vault desktop redesign brief — 2026-05-07

## user verdict
The first desktop pass works as a proof of plumbing, but the UI/design feels sloppy, generic, and underpowered. Go back to the drawing board.

## target feel
Retro iTunes / LimeWire / brushed-metal era Mac app, now sharpened by `docs/ui-art-direction-current.md`: tactile retro-futurist control-room dashboard, brushed silver chrome, dark graphite inset panels, hardware knobs/sliders/toggles, Aqua bevels, dense evidence modules, and occasional leather/paper editorial accents. Skeuomorphic, tactile, archive-grade, a culture time capsule. Top-tier Chugi millennial design is allowed if it serves the product. Avoid generic dashboard SaaS.

## product principle
This is not just a table of TikTok sounds. It is a local-first cultural archive of sounds, their evidence, their visuals, and their trend context. The local vault must remain useful after TikTok links rot.

## priority functionality
1. Integrated local audio playback
   - Play must use the local `.m4a` when available.
   - Build real in-app playback controls: play/pause, seek/progress, duration/current time, selected-track state.
   - Do not rely on OS open handlers as the primary playback path.

2. Serious library table
   - Adjustable/resizable columns.
   - Useful columns: title, artist/source, status, added/packaged date, videos count, local audio, trend/context flags.
   - Sort by date added/packaged.
   - Better density and scanning.

3. Real navigation
   - Library, Ingest Inbox, Review Queues, Collections, Worker Status, Settings must switch actual views or be removed until real.
   - Shortcut inbox deserves its own full window/tab with date sorting.

4. Rich right-side detail panel
   - Replace raw JSON dump with formatted sections.
   - Show album/artwork/sound-page image when present.
   - Show TikTok sound screenshots and relevant local evidence from vault structure.
   - Show associated videos as cards/rows with thumbnails, urls/local paths, stats, and notes.
   - Keep raw JSON behind a collapsible “raw metadata” inspector only.

5. Vault evidence model
   - Inspect actual vault folder structure and metadata shape before assuming fields.
   - Surface existing images/screenshots/videos stored per sound.
   - If artwork is missing from current scrape, add schema/import hooks so future scraper passes can capture it.

6. Mac-openable build loop
   - Keep tests green.
   - Rebuild Mac launcher artifacts after meaningful changes.
   - Be explicit about linux-host limits: signed/notarized standalone app requires macOS.

## implementation constraints
- Project path: `/nas/TikTok Sound Vault/product/sound-vault-desktop`
- Vault path: `/nas/TikTok Sound Vault`
- Use strict regression tests for behavior changes.
- Prefer PySide6 native widgets where practical.
- Do not store secrets in docs/logs.
- Do not mutate external services without explicit approval.

## quality bar
A user opening the app should immediately feel: “this is my local TikTok sound archive, browsable like a beloved music library, with evidence attached.”

Not acceptable:
- fake nav buttons
- raw unformatted JSON as the main detail experience
- play button that only punts to OS
- fixed cramped table columns
- generic modern admin dashboard vibes
