<div align="center">

# Sound Cache ✦

**Hoard your favorite sounds.** A local-first desktop app that saves TikTok / Instagram / YouTube sounds into a folder that's *yours* — searchable, tagged, offline, and unbothered.

[soundcache.io](https://soundcache.io) · [Blog](https://soundcache.io/blog/) · [Pair your iPhone](https://soundcache.io/shortcut/)

`local-first` · `no login` · `no cloud` · `no telemetry you didn't ask for` · macOS today (Python, so Windows/Linux are an easy port)

![Sound Cache desktop app](docs/images/app-screenshot.png)

</div>

## Why

TikTok won't let you save the sound. "Favorites" are bookmarks that vanish when a video is deleted, a track is taken down, or an account goes private. Screen-recording is lossy, the sketchy mp3 sites are malware, and the link you texted yourself rots in your notes.

Sound Cache fixes it permanently: you save a sound once and it lands as a real, tagged audio file in a local folder you own — with the title, artist, artwork, transcript, and example videos — so your FYP's greatest hits survive platform churn. Built for **editors and content creators** who need the trends offline.

## How it works

You don't change your behavior — you already know how to hit share.

1. On TikTok, **tap the sound** (the spinning disc) → its sound page.
2. Tap **Share ↗** → **More •••** → **Save to Sound Cache**.
3. The share sheet hands off the *link* (never your audio) to a tiny relay.
4. Your desktop pulls it, downloads the sound, and files it with artwork + transcript.

One tap on your phone; a fully-tagged file appears on your computer. Offline. Unbothered. (Setup + the signed shortcut: **[soundcache.io/shortcut](https://soundcache.io/shortcut/)**.)

## Features

- **File-native vault** — `metadata.json` per sound is the source of truth; SQLite is a disposable, rebuildable FTS cache. Everything stays browsable in Finder / on a NAS even if the app never runs.
- **Search by anything** — title, artist, music ID, tags, spoken phrase (transcript), usage/popularity, status, or local-media state.
- **Rich ingest** — multi-platform (TikTok / Instagram / YouTube) via yt-dlp with a Playwright capture fallback; pulls artwork, popularity, transcript, and example videos.
- **User notes** — annotate any sound at share time or in-app; notes are indexed and searchable.
- **Transcripts** — local `faster-whisper` ASR; the inspector reports exactly *why* a transcript is empty (instrumental vs not-run-yet vs no-audio).
- **Editor-friendly** — drag a sound out of the window to copy the audio file into Premiere / Resolve / CapCut; favorites, sorting bins, duplicate review, archive-health coverage.
- **Private pairing** — a one-time pair code links your phone's share sheet to your desktop; the lower-left badge confirms it at a glance.

## Privacy & security

Local-first means what it says — your collection lives on your machine, not a server. There's no account, so there's nothing to leak.

- The **relay** only ever holds a link briefly on its way to your desktop (24h TTL), never your files or a profile of you, and you can stop it in one click.
- Submitted URLs are validated (http/https only; private/reserved/cloud-metadata hosts rejected) at the relay **and** at the desktop before fetch — SSRF-hardened, with a safe-redirect handler.
- All request fields are length-bounded; per-pair-code flood caps protect your inbox; logs redact URLs and never store free-form notes server-side.
- Opt-in leaderboard telemetry is anonymized (a sound id, title, platform) — no account, no device secret, no paths.

## Tech

Python 3.12 · PySide6 (Qt) desktop · FastAPI relay on Vercel + Neon Postgres · SQLite FTS5 · yt-dlp + Playwright · faster-whisper.

## Run it (dev)

```bash
# 1. External tools (TikTok sound capture drives a real browser via Playwright;
#    yt-dlp + ffmpeg handle download/transcode):
brew install node ffmpeg yt-dlp           # macOS (use your package manager elsewhere)
npm install                               # installs Playwright into ./node_modules
npx playwright install chromium           # one-time browser download

# 2. Python app:
python -m venv ~/venvs/sound-cache && source ~/venvs/sound-cache/bin/activate
pip install -e .
sound-vault                               # launch the desktop app
# in-app: Settings → Create pairing code → Pair iPhone → Connect TikTok
```

> TikTok serves a sound's audio only to a logged-in browser, so the first run
> walks you through **Connect TikTok** (a one-time login the app keeps locally).
> Without Node/Playwright/Chromium the app still runs, but TikTok sound capture
> is disabled — the in-app prompts tell you exactly what's missing.

Tests: `pytest -q` (set `SOUND_VAULT_DISABLE_RELAY_POLL=1 SOUND_VAULT_DISABLE_TRANSCRIBE=1` for a fast offline run).

---

<div align="center">
now go forth and hoard ✦ &nbsp;·&nbsp; <a href="https://soundcache.io">soundcache.io</a>
</div>
