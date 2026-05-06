# Sound Vault Desktop

Local-first desktop app for a private TikTok Sound Vault.

V1 goals:

- index an existing file-native vault folder
- search/preview/tag sounds locally
- keep SQLite as a cache, with `metadata.json` and catalog JSONL as durable truth
- accept iOS Shortcut share links through a pairing-code relay without accounts

This repo intentionally keeps cloud infrastructure thin: the relay moves URLs, not media.
