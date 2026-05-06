# Sound Vault Desktop + Pairing Relay — V1 Architecture

**Goal:** build a local-first cross-platform desktop app with a tiny no-login relay for iOS Shortcut link handoff.

## Product stance

Sound Vault is a private editor/source-intelligence tool. It indexes a local vault folder, previews audio/video evidence, and lets the user tag, approve, and collect sounds without turning the product into a cloud media platform.

## Components

```text
iOS Shortcut
  -> POST share URL + pairing code
  -> tiny relay service
  -> desktop app polls relay with device secret
  -> local inbox queue
  -> local vault worker resolves/packages/enriches
  -> SQLite/FTS cache + file-native sidecars
```

## Desktop app

Stack: Python + PySide6.

V1 screens:

1. Library table
2. Preview drawer
3. Ingest inbox
4. Collections
5. Worker/status
6. Settings / pairing

Local durable truth:

- `metadata.json` inside each sound folder
- `catalog/sounds.jsonl` rebuilt from sidecars
- `collections/*.json`
- `notes.md`

SQLite is an index/cache, not the primary record.

## Relay

Stack: FastAPI.

Rules:

- no accounts
- pairing codes expire quickly
- desktop has a device secret after pairing
- relay only stores URLs and basic source metadata
- no media files through relay
- links expire after delivery or TTL
- polling is pull-based from desktop, so users do not need public inbound networking

## API sketch

- `GET /v1/health`
- `POST /v1/pairing/create`
- `POST /v1/inbox/submit`
- `GET /v1/inbox/poll`

## Implementation note

The current checked-in relay store is in-memory so tests can lock behavior without choosing hosting storage yet. Production can swap the store for SQLite/Postgres/Redis without changing the app contract.
