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

### Quick look without building (optional)

Every push builds an unsigned Apple Silicon app in the `package` workflow. On the PR, open
**Checks → package → Summary → Artifacts → SoundCache-unsigned-arm64**, unzip it, then
right-click `Sound Cache.app` → **Open** (it is not signed, so a double-click is blocked).
It already passed its own self-test in CI. Run
`"Sound Cache.app/Contents/MacOS/Sound Cache" --self-test` to repeat that on your Mac.
The self-test uses a throwaway vault. Opening the app normally uses your real vault, so do the
backup above first.

## 1. Get the branch and run the tests

```bash
cd ~/path/to/SoundCache
git fetch origin && git checkout claude/cool-cerf-1ovo82 && git pull
~/venvs/sound-vault/bin/pip install -e ".[gui,dev]"
SOUND_VAULT_DISABLE_RELAY_POLL=1 QT_QPA_PLATFORM=offscreen \
  ~/venvs/sound-vault/bin/python -m pytest -q
```

Expect about 690 passed, 7 skipped. (The extras add numpy and qrcode.)

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

Now playing [2]
- [ ] The bottom bar shows the playing sound's art, title and time. Prev/next step through the list, and the bar's waveform seeks.
- [ ] The bar is on every view (Inbox, Review, Dedupe, Worker too). With nothing playing it shows the selected sound, and its Play starts that sound.
- [ ] Repeat (continuous play) keeps playing down the list; shuffle plays a random sound. These replace the old CONT / RND buttons in the top header, which is gone.
- [ ] Starting a different sound resets the bar's progress. Long titles end in "…" rather than pushing the controls.

Sidebar [5]
- [ ] Each view (Library, Inbox, Review, Dedupe, Worker) shows its count, and the counts update after an import or a review.
- [ ] On a short window (under ~860 px tall) the status cards above the list collapse so more rows fit.

Detail pane [6]
- [ ] The selected sound's waveform shows above the slider; clicking it seeks while playing.
- [ ] The stats grid (uses, BPM, length, added, example videos, status) matches the row.

Inbox [7]
- [ ] The Inbox tabs (All / Waiting / Failed) filter the list, and each tab's count matches its rows.

TikTok capture (new browser: Chrome 157, sandbox on)
- [ ] `npm ci`, then Settings → Connect TikTok opens the login window and signing in works.
- [ ] Paste a TikTok sound link → it imports with audio. If capture fails, check the log for `[launch] sandboxed Chromium failed` and paste it here.
- [ ] Building the release: `packaging/add_browser.sh` bundles Chrome for Testing 157 (not Playwright's 156). Run the app from `dist/` and repeat the two checks above.

Pairing (if you paired before November)
- [ ] Settings shows `https://api.soundcache.io`, and a fresh pairing + QR works from your phone.

If anything is off, note the window size and paste a screenshot into the session.

## 4. Ship 0.5.0

Only after the checklist passes:

1. Bump the version in `pyproject.toml` and `src/sound_vault/__init__.py` to 0.5.0.
2. Refresh the Chromium pin: `python packaging/update_chrome_pin.py` (commit if it changed).
3. Build with the verified pipeline in `packaging/README.md` ("The actual release pipeline"):
   `fetch_vendor.sh` → `pip install -U -e ".[gui,ingest]"` + `npm ci` → pyinstaller →
   `check_vendor.sh` → `add_browser.sh` → sign/notarize → DMG. These steps refuse unpatched or
   unverified node, ffmpeg and Chromium. See `docs/security-dependencies.md`.
4. `gh release create v0.5.0 dist/SoundCache-0.5.0-arm64.dmg` with notes.
5. In soundcache-web, set `latest.json` to 0.5.0 with short notes (500-character limit) and push to main.
6. Deploy the relay: `cd relay-worker && npm ci && npx wrangler deploy` (CORS change and patched tooling).

## 5. Prompt for a local Claude Code session

Paste this into Claude Code opened in the repo on your Mac:

> Read docs/founder-local-test.md and docs/modernization-sprint.md. I've backed up my vault at
> <path>. Check out claude/cool-cerf-1ovo82, install with ~/venvs/sound-vault/bin/pip install -e
> ".[gui,dev]", run the test suite and scripts/render_screenshots.py --check, and report results.
> Then launch the app against my vault (<path>) and walk me through section 3's checklist one item
> at a time: tell me what to click, wait for my answer, and fix anything that fails with a test
> and a commit. Don't build or sign a release until I say the checklist passed.
