# Relay Deployment Notes

The relay is intentionally tiny. It moves share URLs from iOS Shortcut to the desktop app. It does not store media or user libraries.

## Local run

```bash
PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.relay.server
```

Default local URL:

```text
http://127.0.0.1:43117
```

## Docker run

```bash
docker build -f Dockerfile.relay -t sound-vault-relay .
docker run --rm -p 43117:43117 sound-vault-relay
```

## Hosted V1 target

Use a cheap container host first:

- Fly.io
- Render
- Railway
- small VPS with Caddy

The first hosted V1 can use the in-memory store for controlled testing, but before public alpha switch relay storage to SQLite/Postgres/Redis so queued links survive restarts.

## Security posture

- pairing code: human-shareable, short-lived
- device secret: desktop-only, sent as header while polling
- relay payload: URLs only
- TTL: links expire after delivery or age-out
- no TikTok credentials
- no media files
- no accounts

## Environment knobs to add next

```text
SOUND_VAULT_RELAY_HOST=0.0.0.0
SOUND_VAULT_RELAY_PORT=43117
SOUND_VAULT_PAIRING_TTL_SECONDS=600
SOUND_VAULT_INBOX_TTL_SECONDS=604800
```
