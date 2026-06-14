# Sound Vault UI art direction — current source of truth

Source image references supplied by Leland:

- 2026-05-13: `/opt/data/cache/images/img_5fcbc2e42938.png`
- 2026-05-15: detailed brushed-metal dashboard reference in the Codex thread

## north star

A tactile retro-futurist control-room dashboard for a private sound archive: brushed-metal Mac chrome, dark graphite inset panels, analog hardware controls, dense evidence modules, and small pockets of physical-world material like leather/paper notes. It should feel like a beloved 2000s pro utility crossed with a studio rack, not a flat SaaS admin panel.

The 20260515g desktop build is the current implementation baseline. It introduces the graphite app shell, layered brushed-metal main deck, pill search, deeper inset tables/cards, tactile row play buttons, fixed artwork framing, and a clearer danger-button language for duplicate quarantine. Future UI work should extend this direction rather than reset back to flat panels.

## preserve from the reference

- **Outer shell:** thick dark hardware frame, inset content well, rounded bevels, visible panel seams, subtle drop shadows.
- **Top chrome:** brushed silver toolbar with pill search, rounded square utility buttons, traffic-light/window-control nostalgia.
- **Left rail:** heavy vertical source list, selected row as a dark pressed capsule, small icons, hardware-status card, analog dial/meter motif.
- **Main content:** modular dashboard grid with dark graphite panels, beveled card edges, compact labels, status pills, progress bars, dense readable rows.
- **Controls:** physical knobs, toggle switches, sliders, pill buttons, tiny indicator LEDs, analog meters. Controls should look pressable/touchable.
- **Color:** graphite/blackened teal base, silver metal chrome, muted beige panels, electric blue active data, green health LEDs, amber/red status severity.
- **Typography:** compact utilitarian sans, uppercase micro-labels, large numeric readouts, monospaced/terminal-style metadata where useful.
- **Texture:** brushed metal, faint noise/grain, inset shadows, leather stitching/paper notes sparingly for editorial notes or review queues.

## translation to Sound Vault

- Treat the library as the central rack/table, not a generic spreadsheet.
- Use the right panel as an evidence inspector: artwork, screenshots, videos, transcripts, raw metadata behind an inspector.
- Use top chrome as playback/search/navigation: jukebox transport, global search, current selection readout.
- Use the left rail as source/status: Library, Inbox, Review, Dedupe, Worker, Settings plus vault health meters.
- Use hardware toggles/sliders for filters, worker states, evidence availability, and review decisions.
- Keep density high, but make every row and panel feel intentionally machined.

## current interaction rules

- Search is a primary keyboard surface. Refreshing the table must not steal focus or move the cursor out of the search field.
- Table playback cells are controls. They should render as compact tactile play/pause buttons and must never be editable text fields.
- Duplicate review is an editor workflow, not just a report viewer. A reviewer must be able to audition candidates, choose a keeper, and quarantine duplicates in a reversible way.
- Artwork belongs in a consistent fixed frame with aspect-fit rendering and explicit fallback states.
- Associated videos should be inspectable evidence, with local open/play behavior where a file exists and URL behavior where only source metadata exists.
- The right inspector should elevate useful editor fields first and keep raw JSON available as a fallback, not as the main experience.

## next visual debt

- Add true hardware widgets for source filters, duplicate decisions, worker state, and archive-health meters.
- Move large tables toward a model/delegate implementation so row controls stay fast at full vault scale.
- Add a richer duplicate-review layout with A/B candidate comparison, waveform/metadata cues, and restore-from-quarantine controls.
- Develop a small icon vocabulary for Library, Evidence, Transcripts, Popularity, Dedupe, Worker, Open folder, Open video, Copy metadata, and Rebuild index.
- Add screenshot regression captures for the main library, evidence inspector, transcript filter, popularity filter, and duplicate review.

## avoid

- Flat SaaS cards.
- Generic light dashboard whitespace.
- Neon cyberpunk glow.
- Placeholder controls that do not connect to real Sound Vault behavior.
- Visual chrome that hides whether the vault has actually indexed/populated.
- Decorative skeuomorphism that makes search, playback, or dedupe harder to operate.
