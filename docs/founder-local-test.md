# Founder local test: the 0.5 track list

Everything below runs on your Mac with your real vault. CI already runs the full test
suite, lint, and headless screenshots at five window sizes on Linux and macOS for
every push (see the PR's checks and the `screenshots-*` artifacts). This pass covers
what CI can't: your real library, real audio playback, drag into your editor, and the
signed build.

Budget: about 30 minutes, plus a build/notarize cycle if it all checks out.

## 0. Before you start (important)

- **Back up the vault** (Time Machine, or copy it). On first launch, 0.5 analyzes
  every sound in the background: it writes `waveform.json` into each sound folder
  and adds `bpm`, `bpm_confidence`, `analysis` and `paths.waveform` to each
  `metadata.json`. It is additive (nothing is removed or renamed), but it touches
  every folder.
- To try the UI without touching the vault, launch with
  `SOUND_VAULT_DISABLE_ANALYSIS=1` (waveforms stay flat, BPM shows "—").
- **If you paired your iPhone before November 2026**, the app will move you from
  the old Vercel relay to `api.soundcache.io` and ask for a new pairing (Settings →
  Create pairing code, then scan the QR again).

## 1. Get the branch and run the tests

```bash
cd ~/path/to/SoundCache
git fetch origin && git checkout claude/cool-cerf-1ovo82 && git pull
~/venvs/sound-vault/bin/pip install -e ".[gui,dev]"
SOUND_VAULT_DISABLE_RELAY_POLL=1 QT_QPA_PLATFORM=offscreen \
  ~/venvs/sound-vault/bin/python -m pytest -q
```

Expect about 680 passed, 7 skipped. (The extras add numpy and qrcode.)

## 2. Look at the screenshots without your vault

```bash
~/venvs/sound-vault/bin/python scripts/render_screenshots.py /tmp/sc-shots --check
open /tmp/sc-shots
```

## 3. Run it on your real vault

```bash
~/venvs/sound-vault/bin/python -m sound_vault.app
```

Watch the status bar: "Drawing waveforms… N/M" while the backfill runs (roughly 0.1–0.3 s
per sound; a 1,300-sound vault takes a few minutes, and the app stays usable).

### Checklist (design board in brackets)

Library list [1, 3]
- [ ] The Library opens in the track list. The List/Table button switches to the old grid and back; the choice survives a restart.
- [ ] Waveforms fill in as the backfill runs. BPM looks right on songs you know; spoken/meme clips show "—".
- [ ] Click a row's play button: it plays, the icon turns to pause, and the waveform fills lilac as it plays.
- [ ] Click inside a playing row's waveform: it seeks there.
- [ ] Heart, + (add to bin), ↓ (reveal in Finder), link (copies the TikTok URL) and ⋮ (the full menu) all work.
- [ ] Drag a row into Premiere / Resolve / CapCut: the real audio file lands.
- [ ] NEW appears on sounds saved in the last 7 days.

Filters and sort [1]
- [ ] BPM chip, Vocals only, and Sort (Most used / Recently cached / Title / BPM) each change the list.
- [ ] The old Duration / Media / Status / Usage filters still work and combine with the new ones.
- [ ] Search still finds lyrics, artists and tags.

Window sizes [4]
- [ ] Shrink the window to its minimum (1080×700): the filter chips wrap to two lines and nothing overlaps.
- [ ] Narrow the window: tags drop out first, then uses, then the waveform. Nothing is cut mid-word.
- [ ] Full screen on a big display: every column shows and rows don't stretch weirdly.
- [ ] View ▾ → Compact gives denser rows; ⌘I hides the detail pane for more room.

Keyboard [4]
- [ ] Space plays/pauses, ↑/↓ moves, F favorites, B opens Add to bin, ⌘1–⌘5 switch views.

Detail pane [6]
- [ ] The selected sound's waveform shows above the slider; clicking it seeks while playing.

Pairing (if you paired before November)
- [ ] Settings shows `https://api.soundcache.io`, and a fresh pairing + QR works from your phone.

If anything is off, note the window size and paste a screenshot into the session.

## 4. Ship 0.5.0

Only after the checklist passes:

1. Bump the version in `pyproject.toml` and `src/sound_vault/__init__.py` to 0.5.0.
2. Build, sign, notarize and wrap, as in `packaging/README.md` ("The actual release pipeline").
3. `gh release create v0.5.0 dist/SoundCache-0.5.0-arm64.dmg` with notes.
4. In soundcache-web, set `latest.json` to 0.5.0 with short notes (500-character limit) and push to main.
5. Deploy the relay CORS change: `cd relay-worker && npx wrangler deploy`.

## 5. Prompt for a local Claude Code session

Paste this into Claude Code opened in the repo on your Mac:

> Read docs/founder-local-test.md and docs/modernization-sprint.md. I've backed up my vault at
> <path>. Check out claude/cool-cerf-1ovo82, install with ~/venvs/sound-vault/bin/pip install -e
> ".[gui,dev]", run the test suite and scripts/render_screenshots.py --check, and report results.
> Then launch the app against my vault (<path>) and walk me through section 3's checklist one item
> at a time: tell me what to click, wait for my answer, and fix anything that fails with a test
> and a commit. Don't build or sign a release until I say the checklist passed.
