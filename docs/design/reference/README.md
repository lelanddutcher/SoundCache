# Track-list reference screenshots

Two screenshots of stock-music libraries, shared on 2026-11-03 as the target feel for
the Library redesign ([sprint plan](../../modernization-sprint.md)). They are layout
references only. Sound Cache keeps its own brand (`soundcache-web/design.md`), copy and
icons; nothing here ships in the app or on the site.

| File | Status |
|------|--------|
| `reference-a-filter-bar-list.png` | **Add the image file.** The screenshots were pasted into a chat session, and the session could not save them as files. |
| `reference-b-bpm-mood-list.png` | **Add the image file** (same reason). |

To add them: drag both PNGs into this folder on GitHub (Add file → Upload files), or
copy them here locally and commit. Keep the file names so the links in the sprint plan
resolve.

## Reference A — filter bar and track list

- **Top filter bar:** dropdown chips for Genre, Mood, Video Theme and Instrument on the
  left. On the right: a Highlights toggle, "Sort by Staff Picks" and a Filters button.
- **Rows** (about 90 px, dark ground, the hovered row slightly lighter):
  1. square cover art, about 56 px;
  2. a large white play triangle;
  3. title (white) over artist (grey), with a yellow **NEW** tag after some titles;
  4. genre list ("Hip Hop, Pop"), truncated with an ellipsis;
  5. small icons: an explicit **E**, a microphone (has vocals), a stems/versions square;
  6. duration ("02:11");
  7. a wide grey waveform taking about a third of the row;
  8. action icons: download, a "similar" toggle, a star, a ⋮ menu.

## Reference B — denser list with BPM and moods

- **Rows** about 72 px, tighter:
  1. cover art;
  2. title (bold, truncated) over artist;
  3. a small vocals badge after some titles;
  4. a **wide waveform with a thin progress scrubber under it**;
  5. duration over **BPM** ("3:59 / 98 BPM");
  6. two lines of tags: genres ("K-Pop, Korea") over moods ("Romantic, Dreamy");
  7. an "Adapt" text button;
  8. heart, +, download and link icons.
- Some rows are instrumental versions of another row ("… (Instrumental Version)").

## What we take from them

- One dense, scannable row per sound, waveform in the middle, actions on the right.
- Length with BPM under it; two short lines of tags.
- A NEW marker and a vocals marker.
- Filter chips plus one toggle and one sort control above the list.

How these map onto Sound Cache's own fields is in the sprint plan ("The row").
