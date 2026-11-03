# soundcache-relay (Cloudflare Worker)

The SoundCache phone relay: it carries a share link from the iOS Shortcut to the
paired Mac app, and serves the public "most saved" leaderboard. It runs on Cloudflare
Workers with state in D1, and is wire-compatible with the original FastAPI relay
(`src/sound_vault/relay/`). Paths, methods, JSON bodies, status codes, `{"detail": ...}`
error shapes, secret hashing, rate limiting and log masking all match, so no client
needs an update.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/health` | liveness |
| `POST /v1/pairing/create` | Mac app creates a pair code + device id/secret |
| `POST /v1/inbox/submit` | iOS Shortcut queues a link for a pair code |
| `GET /v1/inbox/poll` | Mac app takes its queued links (destructive; refreshes the pairing) |
| `POST /v1/events/save` | anonymous save event for the leaderboard |
| `GET /v1/leaderboard` | public top sounds (CORS: soundcache.io origins, GET only) |
| `POST /v1/admin/forward` | ops: move queued links between pair codes (`x-admin-token`) |

Production: Worker `soundcache-relay`, D1 database `soundcache-relay`, custom domain
`api.soundcache.io` (declared in `wrangler.jsonc`; Cloudflare manages its DNS record
and certificate).

## Develop and test

```bash
cd relay-worker
npm ci
npm run typecheck
npm test                      # vitest in workerd with a real local D1
npm run migrate:local && npm run dev   # http://127.0.0.1:8787
```

Compatibility check from the Python side (needs `fastapi`, `httpx`, `certifi`). It does a
full pair → submit → `RelayClient.poll_to_inbox` round trip, then diffs every response
against the FastAPI app:

```bash
# from the repository root, with `npm run dev` running and a fresh local D1
PYTHONPATH=src python relay-worker/test/compat/check_compat.py http://127.0.0.1:8787 --parity
```

`--parity` assumes an empty database (leaderboard counts are compared). Run it without
`--parity` against production.

## Deploy

```bash
cd relay-worker
npx wrangler d1 create soundcache-relay        # once; put the returned id in wrangler.jsonc
npm run migrate:remote                          # apply migrations/ to the remote D1
npx wrangler secret put RELAY_ADMIN_TOKEN       # once; value is never committed
npm run deploy
```

Wrangler needs `CLOUDFLARE_API_TOKEN` (Workers Scripts, D1, Workers Routes and the
soundcache.io zone's DNS edit) and `CLOUDFLARE_ACCOUNT_ID` in the environment.

A Workers custom domain can't be created while a CNAME exists for the same hostname.
The first cutover therefore deletes the old `api` CNAME, then deploys.

## Configuration

| Name | Kind | Default |
| --- | --- | --- |
| `DB` | D1 binding | `soundcache-relay` |
| `RELAY_ADMIN_TOKEN` | secret | unset; the admin route then 404s |
| `SOUND_VAULT_RELAY_RATE_LIMIT` | var | `60` requests per window per bucket (`0` disables) |
| `SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS` | var | `60` |

## Differences from the Python relay

- The rate limiter's client IP is `CF-Connecting-IP`, which the caller can't forge. The
  Python relay used the left-most `X-Forwarded-For`. The limiter is a D1 fixed-window
  counter (same algorithm as `PostgresRateLimiter`) and fails open.
- Pairing codes are made unique against D1, not against an in-process registry.
