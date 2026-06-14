# Background fetch — share even when the app is closed

The desktop pulls links from the relay; nothing is pushed to it (so you never need
inbound networking). The question is *what does the pulling* when the GUI is closed.

## The always-on agent (macOS)

Install a `launchd` LaunchAgent that runs the headless poller in the background:

```bash
sound-vault-agent install            # default: poll every 180s
sound-vault-agent install --interval 300
sound-vault-agent status
sound-vault-agent uninstall
```

It runs `sound-vault-ingest --watch --poll-relay`, which every interval:
1. polls the relay with the saved device credentials,
2. writes any new links into `{vault}/inbox/urls/shortcut-inbox.jsonl`,
3. downloads + packages each into the vault, and
4. (if telemetry is on) reports an anonymized save event.

It reads the relay URL, pair code, and device secret from the app's saved settings,
so it stays **idle until you've paired the desktop** (Settings → Create pairing code).
Logs: `~/Library/Logs/sound-vault-ingest.log`.

`RunAtLoad` + `KeepAlive` mean it starts at login and restarts if it dies. Share a
sound on your phone; it lands in the vault within one poll interval — GUI open or not.

### Cost note

Each poll is one relay request (and can briefly wake the hosted DB). For a personal
vault, **180–300s is plenty** — sounds aren't time-critical, and a longer interval
keeps Vercel function invocations and Neon compute (and therefore credits) low. The
GUI's manual "Download & import" button is always available for an instant pull.

## Test the relay ↔ desktop loop locally (no cloud)

```bash
# 1) run the relay locally with SQLite persistence
SOUND_VAULT_RELAY_STORAGE_PATH=/tmp/sv-relay.sqlite3 \
SOUND_VAULT_RELAY_HOST=127.0.0.1 SOUND_VAULT_RELAY_PORT=43119 sound-vault-relay &

# 2) pair (what the desktop Settings button does)
curl -s localhost:43119/v1/pairing/create -H 'content-type: application/json' \
  -d '{"device_name":"My Mac"}'

# 3) submit a link (what the iOS Shortcut does) using the returned pair_code
curl -s localhost:43119/v1/inbox/submit -H 'content-type: application/json' \
  -d '{"pair_code":"<CODE>","url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","source":"ios_shortcut"}'

# 4) the desktop pulls + downloads (uses relay creds from settings)
sound-vault-ingest --poll-relay
```

The sound appears under `{vault}/sounds/`. This is exactly the path the hosted relay
serves over the internet once deployed to Vercel.

## Enabling the TikTok capture fallback

yt-dlp's TikTok *sound* extractor is broken, so TikTok original sounds are captured
via an authenticated headless browser. Install once:

```bash
npm install playwright && npx playwright install chromium
```

Then point the ingest at the capture script + a logged-in TikTok storage state via
env vars (read by `ingest/factory.build_downloader`):

```bash
export SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT="$PWD/scripts/capture_tiktok_audio.cjs"
export SOUND_VAULT_TIKTOK_STATE="/path/to/tiktok.storageState.json"
export SOUND_VAULT_TIKTOK_CAPTURE_CWD="$PWD"   # where node resolves `require('playwright')`
```

With these set, ingest tries yt-dlp first and falls back to the Playwright capture
for `platform == tiktok`. Verified end-to-end on a real shared sound.
