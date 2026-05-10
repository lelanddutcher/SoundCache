# Relay Deployment Notes

The relay is intentionally tiny. It moves share URLs from iOS Shortcut to the desktop app. It does not store media, TikTok credentials, or user libraries.

## Local run

```bash
PYTHONPATH=src SOUND_VAULT_RELAY_HOST=127.0.0.1 SOUND_VAULT_RELAY_PORT=43117 \
  /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.relay.server
```

Health check:

```bash
curl http://127.0.0.1:43117/v1/health
```

## Docker run

```bash
docker build -f Dockerfile.relay -t sound-vault-relay .
docker run --rm -p 43117:43117 -v sound-vault-relay-data:/data \
  -e SOUND_VAULT_RELAY_STORAGE_PATH=/data/relay.sqlite3 sound-vault-relay
curl http://127.0.0.1:43117/v1/health
```

The Docker image sets `SOUND_VAULT_RELAY_HOST=0.0.0.0` so published ports work from outside the container.

## Hosted V1 target

Use a cheap container host first:

- Fly.io
- Render
- Railway
- small VPS with Caddy

The relay can run in memory for throwaway local tests, or with SQLite persistence using `SOUND_VAULT_RELAY_STORAGE_PATH`. For hosted private tests, mount persistent storage and set that env var so queued links, device registrations, and accepted pair-code routes survive restarts. Basic per-IP rate limiting and log masking are built in; hosted public alpha still needs a real restart/load test and abuse cleanup policy.

## Fly private-test deployment

```bash
fly launch --no-deploy --copy-config --name sound-vault-relay
fly volumes create relay_data --size 1
fly deploy
fly status
fly logs
```

`deploy/fly.toml.example` binds the container on `0.0.0.0:43117` and mounts `/data` for SQLite relay state. It still has `min_machines_running = 0`; that is acceptable with SQLite persistence for private tests, but public alpha should still run a hosted restart/load test and tune rate-limit/cleanup policy.

## Security posture

- pairing code: human-shareable route code for Shortcut submission
- device secret: desktop-only, sent as header while polling
- relay payload: URLs only
- TTL: queued links expire after delivery or age-out
- unknown pair codes are rejected
- no TikTok credentials
- no media files
- no accounts

## Environment knobs

```text
SOUND_VAULT_RELAY_HOST=0.0.0.0
SOUND_VAULT_RELAY_PORT=43117
SOUND_VAULT_RELAY_STORAGE_PATH=/data/relay.sqlite3
SOUND_VAULT_RELAY_RATE_LIMIT=60
SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS=60
SOUND_VAULT_PAIRING_TTL_SECONDS=600        # setup/claim window, not durable storage
SOUND_VAULT_INBOX_TTL_SECONDS=604800       # queued link TTL, planned configurable knob
```

## Public-alpha blockers

- abuse cleanup job for expired pair-code routes and inbox links
- hosted restart/load test against the real deployment target
- tune rate limits against real Shortcut/Desktop polling cadence
