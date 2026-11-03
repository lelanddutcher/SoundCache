// D1-backed relay state. Mirrors src/sound_vault/relay/pg_store.py statement for
// statement (same tables, hashing, TTLs and refresh-on-poll semantics).

export const PAIR_CODE_TTL_SECONDS = 30 * 24 * 60 * 60; // 30 days
export const INBOX_ITEM_TTL_SECONDS = 30 * 24 * 60 * 60; // 30 days

const WORDS = [
  "RIVER", "ANCHOR", "MIXER", "PIXEL", "VAULT", "FRAME", "SIGNAL", "NOVA",
  "ECHO", "FINDER", "CLIP", "ROUTE", "BEACON", "FADER", "INDEX", "MARKER",
];
const CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ";

const encoder = new TextEncoder();

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** inbox._hash_pair_code: sha256(code.strip().upper()). */
export function hashPairCode(code: string): Promise<string> {
  return sha256Hex(pyStrip(code).toUpperCase());
}

/** inbox._hash_secret: sha256(secret) — secrets are full-entropy, never stored raw. */
export function hashSecret(secret: string): Promise<string> {
  return sha256Hex(secret);
}

/** Python str.strip() (whitespace only). */
export function pyStrip(value: string): string {
  return value.replace(/^\s+|\s+$/gu, "");
}

/** secrets.compare_digest equivalent. */
export function timingSafeEqual(a: string, b: string): boolean {
  const ab = encoder.encode(a);
  const bb = encoder.encode(b);
  if (ab.byteLength !== bb.byteLength) {
    crypto.subtle.timingSafeEqual(ab, ab); // keep the timing roughly uniform
    return false;
  }
  return crypto.subtle.timingSafeEqual(ab, bb);
}

/** secrets.token_urlsafe(n): n random bytes, base64url, no padding. */
export function tokenUrlsafe(nbytes: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(nbytes));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function choice(alphabet: readonly string[] | string): string {
  // Both alphabets have power-of-two sizes (16 words, 32 chars) so modulo is unbiased.
  const [r] = crypto.getRandomValues(new Uint32Array(1));
  return alphabet[r % alphabet.length];
}

export function newPairCode(): string {
  const group = () => Array.from({ length: 4 }, () => choice(CODE_ALPHABET)).join("");
  return [choice(WORDS), group(), group()].join("-");
}

export interface InboxItem {
  id: string;
  url: string;
  source: string;
  note: string;
  created_at: number;
}

export interface LeaderboardEntry {
  sound_id: string;
  title: string;
  artist: string;
  platform: string;
  saves: number;
}

const WINDOWS: Record<string, number | null> = {
  all: null,
  "24h": 24 * 60 * 60,
  "7d": 7 * 24 * 60 * 60,
  "30d": 30 * 24 * 60 * 60,
};

export function windowSeconds(window: string): number | null {
  return WINDOWS[pyStrip(window || "all").toLowerCase()] ?? null;
}

export class RelayStore {
  constructor(private readonly db: D1Database, private readonly now: () => number) {}

  async createPairing(deviceName: string): Promise<{ code: string; deviceId: string; deviceSecret: string; expiresAt: number }> {
    void deviceName; // accepted for parity; the Python registry never persisted it either
    let code = "";
    for (let attempt = 0; attempt < 20; attempt++) {
      const candidate = newPairCode();
      const hit = await this.db
        .prepare("SELECT 1 FROM pair_codes WHERE pair_code_hash = ?")
        .bind(await hashPairCode(candidate))
        .first();
      if (!hit) {
        code = candidate;
        break;
      }
    }
    if (!code) throw new Error("unable to allocate unique pairing code");
    const deviceId = `dev_${tokenUrlsafe(18)}`;
    const deviceSecret = tokenUrlsafe(32);
    const now = this.now();
    const expiresAt = now + PAIR_CODE_TTL_SECONDS;
    await this.db.batch([
      this.db
        .prepare(
          `INSERT INTO devices (device_id, device_secret_hash) VALUES (?, ?)
           ON CONFLICT (device_id) DO UPDATE SET device_secret_hash = excluded.device_secret_hash`,
        )
        .bind(deviceId, await hashSecret(deviceSecret)),
      this.db
        .prepare(
          `INSERT INTO pair_codes (pair_code_hash, expires_at, device_id) VALUES (?, ?, ?)
           ON CONFLICT (pair_code_hash) DO UPDATE SET expires_at = excluded.expires_at, device_id = excluded.device_id`,
        )
        .bind(await hashPairCode(code), expiresAt, deviceId),
    ]);
    return { code, deviceId, deviceSecret, expiresAt };
  }

  async canAcceptPairCode(pairCode: string): Promise<boolean> {
    const codeHash = await hashPairCode(pairCode);
    const row = await this.db
      .prepare("SELECT expires_at FROM pair_codes WHERE pair_code_hash = ?")
      .bind(codeHash)
      .first<{ expires_at: number }>();
    if (!row) return false;
    if (Number(row.expires_at) < this.now()) {
      await this.db.prepare("DELETE FROM pair_codes WHERE pair_code_hash = ?").bind(codeHash).run();
      return false;
    }
    return true;
  }

  async submitLink(pairCode: string, url: string, source: string, note: string): Promise<string> {
    const now = this.now();
    const id = `in_${tokenUrlsafe(12)}`;
    await this.db
      .prepare(
        `INSERT INTO inbox_items (id, pair_code_hash, url, source, created_at, expires_at, note)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(id, await hashPairCode(pairCode), url, source, now, now + INBOX_ITEM_TTL_SECONDS, pyStrip(note || ""))
      .run();
    return id;
  }

  /** Destructive poll: returns and deletes this pairing's waiting items. */
  async poll(deviceId: string, deviceSecret: string, pairCode: string): Promise<InboxItem[]> {
    const now = this.now();
    const wanted = await hashPairCode(pairCode);
    const device = await this.db
      .prepare("SELECT device_secret_hash FROM devices WHERE device_id = ?")
      .bind(deviceId)
      .first<{ device_secret_hash: string }>();
    if (!device || !timingSafeEqual(String(device.device_secret_hash), await hashSecret(deviceSecret))) return [];
    const pair = await this.db
      .prepare("SELECT expires_at, device_id FROM pair_codes WHERE pair_code_hash = ?")
      .bind(wanted)
      .first<{ expires_at: number; device_id: string }>();
    if (!pair) return [];
    if (Number(pair.expires_at) < now) {
      await this.db.prepare("DELETE FROM pair_codes WHERE pair_code_hash = ?").bind(wanted).run();
      return [];
    }
    if (String(pair.device_id) !== deviceId) return [];
    // Keep an actively-polled pairing alive (the 2026-08-14 silent-lapse fix), purge
    // expired items, then atomically take this pairing's items. D1 batches run as one
    // transaction, so two concurrent polls can never both receive the same item.
    const results = await this.db.batch<InboxItem>([
      this.db.prepare("UPDATE pair_codes SET expires_at = ? WHERE pair_code_hash = ?").bind(now + PAIR_CODE_TTL_SECONDS, wanted),
      this.db.prepare("DELETE FROM inbox_items WHERE expires_at < ?").bind(now),
      this.db
        .prepare(
          `DELETE FROM inbox_items WHERE pair_code_hash = ? AND expires_at >= ?
           RETURNING id, url, source, note, created_at`,
        )
        .bind(wanted, now),
    ]);
    const rows = (results[2].results || []) as InboxItem[];
    return rows
      .map((r) => ({ id: String(r.id), url: String(r.url), source: String(r.source), note: String(r.note ?? ""), created_at: Number(r.created_at) }))
      .sort((a, b) => a.created_at - b.created_at);
  }

  /** PostgresInboxStore.forward_pending. Throws Error("target pair code is unknown or expired"). */
  async forwardPending(opts: { sourceCode: string; targetCode: string; excludeSources: string[]; dryRun: boolean }): Promise<Record<string, unknown>> {
    const now = this.now();
    const sourceHash = await hashPairCode(opts.sourceCode);
    const targetHash = await hashPairCode(opts.targetCode);
    const target = await this.db
      .prepare("SELECT expires_at FROM pair_codes WHERE pair_code_hash = ?")
      .bind(targetHash)
      .first<{ expires_at: number }>();
    if (!target || Number(target.expires_at) < now) throw new ForwardError("target pair code is unknown or expired");
    let where = "pair_code_hash = ? AND expires_at >= ?";
    const params: unknown[] = [sourceHash, now];
    if (opts.excludeSources.length) {
      where += ` AND source NOT IN (${opts.excludeSources.map(() => "?").join(",")})`;
      params.push(...opts.excludeSources);
    }
    const { results } = await this.db
      .prepare(`SELECT url, source, created_at FROM inbox_items WHERE ${where} ORDER BY created_at`)
      .bind(...params)
      .all<{ url: string; source: string; created_at: number }>();
    const items = results.map((r) => ({ url: String(r.url), source: String(r.source), created_at: Number(r.created_at) }));
    if (opts.dryRun) return { dry_run: true, would_move: items.length, items };
    if (items.length) {
      await this.db
        .prepare(`UPDATE inbox_items SET pair_code_hash = ? WHERE ${where}`)
        .bind(targetHash, ...params)
        .run();
    }
    return { dry_run: false, moved: items.length, items };
  }

  async recordSave(soundId: string, platform: string, title: string, artist: string): Promise<void> {
    const ts = this.now();
    await this.db.batch([
      this.db.prepare("INSERT INTO save_events (id, sound_id, occurred_at) VALUES (?, ?, ?)").bind(`ev_${tokenUrlsafe(10)}`, soundId, ts),
      this.db
        .prepare(
          `INSERT INTO sound_meta (sound_id, title, artist, platform, updated_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (sound_id) DO UPDATE SET
             title = COALESCE(NULLIF(excluded.title, ''), sound_meta.title),
             artist = COALESCE(NULLIF(excluded.artist, ''), sound_meta.artist),
             platform = COALESCE(NULLIF(excluded.platform, ''), sound_meta.platform),
             updated_at = excluded.updated_at`,
        )
        .bind(soundId, title || "", artist || "", platform || "", ts),
    ]);
  }

  async leaderboard(limit: number, window: string): Promise<LeaderboardEntry[]> {
    const capped = Math.max(1, Math.min(Math.trunc(limit), 1000));
    const seconds = windowSeconds(window);
    const cutoff = seconds !== null ? this.now() - seconds : null;
    const sql = `
      SELECT e.sound_id AS sound_id, COUNT(*) AS saves,
             COALESCE(m.title, '') AS title, COALESCE(m.artist, '') AS artist, COALESCE(m.platform, '') AS platform
      FROM save_events e
      LEFT JOIN sound_meta m ON m.sound_id = e.sound_id
      ${cutoff !== null ? "WHERE e.occurred_at >= ?" : ""}
      GROUP BY e.sound_id
      ORDER BY saves DESC, e.sound_id
      LIMIT ?`;
    const stmt = this.db.prepare(sql);
    const bound = cutoff !== null ? stmt.bind(cutoff, capped) : stmt.bind(capped);
    const { results } = await bound.all<LeaderboardEntry>();
    return results.map((r) => ({
      sound_id: String(r.sound_id),
      title: String(r.title),
      artist: String(r.artist),
      platform: String(r.platform),
      saves: Number(r.saves),
    }));
  }

  /**
   * PostgresRateLimiter.allow: durable fixed-window counter shared by every isolate.
   * Fails OPEN on any storage error — rate limiting must never take the relay down.
   */
  async allow(key: string, limit: number, windowSecondsValue: number): Promise<boolean> {
    if (limit <= 0) return true;
    const window = windowSecondsValue <= 0 ? 1 : windowSecondsValue;
    const now = Math.trunc(this.now());
    const windowStart = Math.floor(now / window) * window;
    try {
      const row = await this.db
        .prepare(
          `INSERT INTO rate_limit_hits (key, window_start, count) VALUES (?, ?, 1)
           ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit_hits.count + 1
           RETURNING count`,
        )
        .bind(key, windowStart)
        .first<{ count: number }>();
      // Opportunistic cleanup of old windows; sampled so it costs ~1 write per 20 hits.
      if (Math.random() < 0.05) {
        await this.db.prepare("DELETE FROM rate_limit_hits WHERE window_start < ?").bind(windowStart - window * 5).run();
      }
      return Number(row?.count ?? 0) <= limit;
    } catch (err) {
      console.error("relay.rate_limiter_error", err instanceof Error ? err.message : String(err));
      return true;
    }
  }
}

export class ForwardError extends Error {}
