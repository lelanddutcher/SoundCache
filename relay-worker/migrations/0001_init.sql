-- SoundCache relay schema, ported from src/sound_vault/relay/pg_store.py.
-- Same tables and columns as the Postgres store; only hashes of pair codes and
-- device secrets are ever stored.

CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    device_secret_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pair_codes (
    pair_code_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    device_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id TEXT PRIMARY KEY,
    pair_code_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inbox_pair ON inbox_items (pair_code_hash);
CREATE INDEX IF NOT EXISTS idx_inbox_expires ON inbox_items (expires_at);

CREATE TABLE IF NOT EXISTS save_events (
    id TEXT PRIMARY KEY,
    sound_id TEXT NOT NULL,
    occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_save_events_sound ON save_events (sound_id);
CREATE INDEX IF NOT EXISTS idx_save_events_time ON save_events (occurred_at);

CREATE TABLE IF NOT EXISTS sound_meta (
    sound_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rate_limit_hits (
    key TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, window_start)
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_window ON rate_limit_hits (window_start);
