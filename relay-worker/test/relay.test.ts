// Behavioural tests for the Worker relay. Each block mirrors the Python suite in
// tests/test_relay_*.py, test_inbox.py and test_pairing.py, which define the contract.
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import { CORS_ORIGIN_REGEX, handle, maskLogFields, type Env } from "../src/index";
import { hashPairCode, hashSecret } from "../src/store";
import { isSafePublicUrl } from "../src/url-safety";

const BASE = "https://api.soundcache.io";
let clock = 1000;
let overrides: Partial<Env> = {};

function relayEnv(): Env {
  return { ...(env as unknown as Env), SOUND_VAULT_RELAY_RATE_LIMIT: "1000", ...overrides };
}

async function call(method: string, path: string, opts: { body?: unknown; rawBody?: string; headers?: Record<string, string> } = {}) {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  let body: string | undefined = opts.rawBody;
  if (opts.body !== undefined) {
    body = JSON.stringify(opts.body);
    headers["content-type"] ??= "application/json";
  }
  const res = await handle(new Request(BASE + path, { method, headers, body }), relayEnv(), { now: () => clock });
  const text = await res.text();
  let data: any = text;
  try {
    data = JSON.parse(text);
  } catch {
    /* plain text */
  }
  return { status: res.status, data, headers: res.headers };
}

const post = (path: string, body: unknown, headers?: Record<string, string>) => call("POST", path, { body, headers });
const get = (path: string, headers?: Record<string, string>) => call("GET", path, { headers });

async function pair(deviceName = "Studio Mac") {
  const r = await post("/v1/pairing/create", { device_name: deviceName });
  expect(r.status).toBe(200);
  return r.data as { pair_code: string; device_id: string; device_secret: string; expires_at: number };
}

const creds = (p: { device_id: string; device_secret: string }) => ({ "x-device-id": p.device_id, "x-device-secret": p.device_secret });
const pollPath = (code: string) => `/v1/inbox/poll?pair_code=${encodeURIComponent(code)}`;

beforeEach(async () => {
  clock = 1000;
  overrides = {};
  await env.DB.batch(
    ["devices", "pair_codes", "inbox_items", "save_events", "sound_meta", "rate_limit_hits"].map((t) => env.DB.prepare(`DELETE FROM ${t}`)),
  );
});

describe("GET /v1/health", () => {
  it("returns ok", async () => {
    const r = await get("/v1/health");
    expect(r.status).toBe(200);
    expect(r.data).toEqual({ status: "ok" });
    expect(r.headers.get("content-type")).toBe("application/json");
  });

  it("strips the old serverless entrypoint prefix", async () => {
    expect((await get("/api/index/v1/health")).data).toEqual({ status: "ok" });
    expect((await get("/api/index.py/v1/health")).data).toEqual({ status: "ok" });
  });

  it("404s unknown routes and 405s wrong methods with FastAPI shapes", async () => {
    expect(await get("/nope")).toMatchObject({ status: 404, data: { detail: "Not Found" } });
    expect(await post("/v1/health", {})).toMatchObject({ status: 405, data: { detail: "Method Not Allowed" } });
    expect((await get("/api/v1/health")).status).toBe(404); // a bare /api prefix is not stripped
  });

  it("redirects a trailing slash like Starlette", async () => {
    const r = await get("/v1/health/");
    expect(r.status).toBe(307);
    expect(r.headers.get("location")).toBe(`${BASE}/v1/health`);
  });
});

describe("POST /v1/pairing/create", () => {
  it("returns a WORD-XXXX-XXXX code, device id, secret and a 30-day expiry", async () => {
    const p = await pair();
    expect(p.pair_code).toMatch(/^[A-Z]+-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$/);
    expect(p.device_id).toMatch(/^dev_[A-Za-z0-9_-]{24}$/);
    expect(p.device_secret).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(p.expires_at).toBe(1000 + 30 * 24 * 60 * 60);
  });

  it("stores only hashes of the pair code and device secret", async () => {
    const p = await pair();
    const dump = JSON.stringify([
      (await env.DB.prepare("SELECT * FROM devices").all()).results,
      (await env.DB.prepare("SELECT * FROM pair_codes").all()).results,
    ]);
    expect(dump).not.toContain(p.device_secret);
    expect(dump).not.toContain(p.pair_code);
    expect(dump).toContain(await hashSecret(p.device_secret));
    expect(dump).toContain(await hashPairCode(p.pair_code));
  });

  it("validates device_name like pydantic", async () => {
    const missing = await post("/v1/pairing/create", {});
    expect(missing.status).toBe(422);
    expect(missing.data.detail[0]).toMatchObject({ type: "missing", loc: ["body", "device_name"], msg: "Field required" });
    const long = await post("/v1/pairing/create", { device_name: "x".repeat(129) });
    expect(long.data.detail[0]).toMatchObject({ type: "string_too_long", ctx: { max_length: 128 } });
    expect((await post("/v1/pairing/create", { device_name: 5 })).data.detail[0].type).toBe("string_type");
    expect((await call("POST", "/v1/pairing/create")).data.detail[0]).toMatchObject({ type: "missing", loc: ["body"] });
    const bad = await call("POST", "/v1/pairing/create", { rawBody: "{nope", headers: { "content-type": "application/json" } });
    expect(bad.status).toBe(422);
    expect(bad.data.detail[0].type).toBe("json_invalid");
  });
});

describe("POST /v1/inbox/submit + GET /v1/inbox/poll", () => {
  it("round-trips a link with its note, then empties the queue", async () => {
    const p = await pair();
    const submit = await post("/v1/inbox/submit", {
      pair_code: p.pair_code,
      url: "https://www.tiktok.com/t/abc/",
      source: "ios_shortcut",
      note: "  use for gym intros  ",
    });
    expect(submit.status).toBe(200);
    expect(submit.data.status).toBe("queued");
    expect(submit.data.id).toMatch(/^in_[A-Za-z0-9_-]{16}$/);

    const first = await get(pollPath(p.pair_code), creds(p));
    expect(first.status).toBe(200);
    expect(first.data).toEqual({
      items: [{ id: submit.data.id, url: "https://www.tiktok.com/t/abc/", source: "ios_shortcut", note: "use for gym intros" }],
      pairing: "ok",
    });
    expect((await get(pollPath(p.pair_code), creds(p))).data).toEqual({ items: [], pairing: "ok" });
  });

  it("defaults source and note, and accepts a lower-case pair code", async () => {
    const p = await pair();
    await post("/v1/inbox/submit", { pair_code: ` ${p.pair_code.toLowerCase()} `, url: "https://www.tiktok.com/t/abc/" });
    const r = await get(pollPath(p.pair_code), creds(p));
    expect(r.data.items[0]).toMatchObject({ source: "ios_shortcut", note: "" });
  });

  it("delivers items in submission order", async () => {
    const p = await pair();
    for (let i = 0; i < 3; i++) {
      clock = 1000 + i;
      await post("/v1/inbox/submit", { pair_code: p.pair_code, url: `https://www.tiktok.com/t/${i}/` });
    }
    const r = await get(pollPath(p.pair_code), creds(p));
    expect(r.data.items.map((i: any) => i.url)).toEqual([0, 1, 2].map((i) => `https://www.tiktok.com/t/${i}/`));
  });

  it("rejects an unknown pair code with 404", async () => {
    const r = await post("/v1/inbox/submit", { pair_code: "NOPE-0000", url: "https://www.tiktok.com/t/abc/", source: "ios_shortcut" });
    expect(r).toMatchObject({ status: 404, data: { detail: "unknown or expired pairing code" } });
  });

  it("rejects an expired pair code", async () => {
    const p = await pair();
    clock += 30 * 24 * 60 * 60 + 1;
    expect((await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "https://www.tiktok.com/t/abc/" })).status).toBe(404);
  });

  it("does not let device B poll device A's pair code", async () => {
    const a = await pair("Studio Mac");
    const b = await pair("Attacker Mac");
    await post("/v1/inbox/submit", { pair_code: a.pair_code, url: "https://www.tiktok.com/t/abc/" });
    const attacker = await get(pollPath(a.pair_code), creds(b));
    expect(attacker.status).toBe(200);
    expect(attacker.data.items).toEqual([]);
    const owner = await get(pollPath(a.pair_code), creds(a));
    expect(owner.data.items[0].url).toBe("https://www.tiktok.com/t/abc/");
  });

  it("returns nothing for a wrong secret", async () => {
    const p = await pair();
    await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "https://www.tiktok.com/t/abc/" });
    const r = await get(pollPath(p.pair_code), { "x-device-id": p.device_id, "x-device-secret": "wrong" });
    expect(r.data.items).toEqual([]);
    expect((await get(pollPath(p.pair_code), creds(p))).data.items).toHaveLength(1);
  });

  it("401s a poll without device credentials", async () => {
    const p = await pair();
    expect(await get(pollPath(p.pair_code))).toMatchObject({ status: 401, data: { detail: "missing device credentials" } });
    expect((await get(pollPath(p.pair_code), { "x-device-id": p.device_id })).status).toBe(401);
  });

  it("validates the pair_code query parameter", async () => {
    const missing = await get("/v1/inbox/poll", { "x-device-id": "d", "x-device-secret": "s" });
    expect(missing.status).toBe(422);
    expect(missing.data.detail[0]).toMatchObject({ type: "missing", loc: ["query", "pair_code"], input: null });
    const long = await get(pollPath("A".repeat(5000)), { "x-device-id": "d", "x-device-secret": "s" });
    expect(long.status).toBe(422);
  });

  it("refreshes an actively polled pairing so it never silently lapses", async () => {
    const p = await pair();
    clock += 29 * 24 * 60 * 60;
    await get(pollPath(p.pair_code), creds(p));
    clock += 2 * 24 * 60 * 60; // past the original expiry
    expect((await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "https://www.tiktok.com/music/sound-2" })).status).toBe(200);
    expect((await get(pollPath(p.pair_code), creds(p))).data.items).toHaveLength(1);
  });

  it("lets an abandoned pairing lapse and says so on poll", async () => {
    const p = await pair();
    clock += 30 * 24 * 60 * 60 + 1;
    expect((await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "https://www.tiktok.com/music/sound-1" })).status).toBe(404);
    expect((await get(pollPath(p.pair_code), creds(p))).data).toEqual({ items: [], pairing: "unknown_or_expired" });
  });

  it("purges items older than 30 days", async () => {
    const p = await pair("active");
    const q = await pair("abandoned");
    await post("/v1/inbox/submit", { pair_code: q.pair_code, url: "https://www.tiktok.com/t/stale/" });
    clock += 15 * 24 * 60 * 60;
    await get(pollPath(p.pair_code), creds(p)); // keeps p alive
    const count = async () => (await env.DB.prepare("SELECT COUNT(*) AS n FROM inbox_items").first<{ n: number }>())!.n;
    expect(await count()).toBe(1);
    clock += 16 * 24 * 60 * 60; // q's item is now 31 days old
    await get(pollPath(p.pair_code), creds(p)); // any valid poll sweeps expired items
    expect(await count()).toBe(0);
  });
});

describe("submit hardening (SSRF, sizes, floods)", () => {
  it.each([
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "http://127.0.0.1/admin",
    "http://[::1]/",
    "http://10.0.0.5/internal",
    "http://localhost:8080/",
    "http://metadata.google.internal/",
    "http://2130706433/", // 127.0.0.1 in integer form
    "not a url",
  ])("rejects %s with 422", async (url) => {
    const p = await pair();
    const r = await post("/v1/inbox/submit", { pair_code: p.pair_code, url });
    expect(r.status).toBe(422);
    expect(r.data.detail[0].loc).toEqual(["body", "url"]);
  });

  it("uses pydantic's error types for the url field", async () => {
    const p = await pair();
    const scheme = await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "file:///etc/passwd" });
    expect(scheme.data.detail[0].type).toBe("url_scheme");
    const priv = await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "http://10.0.0.5/" });
    expect(priv.data.detail[0]).toMatchObject({ type: "value_error", msg: "Value error, url must be an http(s) link to a public host" });
  });

  it("rejects an oversized note", async () => {
    const p = await pair();
    const r = await post("/v1/inbox/submit", { pair_code: p.pair_code, url: "https://www.tiktok.com/t/abc/", note: "x".repeat(5000) });
    expect(r.status).toBe(422);
    expect(r.data.detail[0]).toMatchObject({ type: "string_too_long", loc: ["body", "note"] });
  });

  it("caps one pair code's fill rate across rotating IPs", async () => {
    overrides = { SOUND_VAULT_RELAY_RATE_LIMIT: "2" };
    const p = await pair();
    const statuses: number[] = [];
    for (let i = 0; i < 4; i++) {
      const r = await post(
        "/v1/inbox/submit",
        { pair_code: p.pair_code, url: `https://www.tiktok.com/t/${i}/` },
        { "cf-connecting-ip": `198.51.${i}.1` },
      );
      statuses.push(r.status);
    }
    expect(statuses).toContain(429);
    expect(statuses.slice(0, 2)).toEqual([200, 200]);
  });

  it("rate-limits per client IP and resets in the next window", async () => {
    overrides = { SOUND_VAULT_RELAY_RATE_LIMIT: "3" };
    const ip = { "cf-connecting-ip": "198.51.100.7" };
    const statuses = [];
    for (let i = 0; i < 4; i++) statuses.push((await get("/v1/leaderboard", ip)).status);
    expect(statuses).toEqual([200, 200, 200, 429]);
    expect((await get("/v1/leaderboard", ip)).data).toEqual({ detail: "rate limit exceeded" });
    expect((await get("/v1/leaderboard", { "cf-connecting-ip": "198.51.100.8" })).status).toBe(200); // other IP unaffected
    clock += 60;
    expect((await get("/v1/leaderboard", ip)).status).toBe(200);
  });

  it("prefers CF-Connecting-IP over a spoofable X-Forwarded-For", async () => {
    overrides = { SOUND_VAULT_RELAY_RATE_LIMIT: "1" };
    const a = await get("/v1/leaderboard", { "cf-connecting-ip": "198.51.100.9", "x-forwarded-for": "1.1.1.1" });
    const b = await get("/v1/leaderboard", { "cf-connecting-ip": "198.51.100.9", "x-forwarded-for": "2.2.2.2" });
    expect([a.status, b.status]).toEqual([200, 429]);
  });

  it("a limit of 0 disables rate limiting", async () => {
    overrides = { SOUND_VAULT_RELAY_RATE_LIMIT: "0" };
    for (let i = 0; i < 5; i++) expect((await get("/v1/leaderboard")).status).toBe(200);
  });
});

describe("POST /v1/events/save + GET /v1/leaderboard", () => {
  it("aggregates saves, keeps the best metadata and orders by saves then id", async () => {
    await post("/v1/events/save", { sound_id: "111", platform: "tiktok", title: "Song A", artist: "Artist A" });
    await post("/v1/events/save", { sound_id: "111", title: "" }); // blanks never clobber
    await post("/v1/events/save", { sound_id: "222", platform: "tiktok" });
    await post("/v1/events/save", { sound_id: "000" });
    const r = await get("/v1/leaderboard");
    expect(r.status).toBe(200);
    expect(r.data).toEqual({
      window: "all",
      limit: 50,
      entries: [
        { sound_id: "111", title: "Song A", artist: "Artist A", platform: "tiktok", saves: 2 },
        { sound_id: "000", title: "", artist: "", platform: "", saves: 1 },
        { sound_id: "222", title: "", artist: "", platform: "tiktok", saves: 1 },
      ],
    });
  });

  it("honours windows and limit", async () => {
    await post("/v1/events/save", { sound_id: "old" });
    clock += 2 * 24 * 60 * 60;
    await post("/v1/events/save", { sound_id: "new" });
    expect((await get("/v1/leaderboard?window=24h")).data.entries.map((e: any) => e.sound_id)).toEqual(["new"]);
    expect((await get("/v1/leaderboard?window=7D")).data.entries).toHaveLength(2);
    expect((await get("/v1/leaderboard?window=bogus")).data).toMatchObject({ window: "bogus" }); // unknown = all, echoed
    expect((await get("/v1/leaderboard?limit=1")).data.entries).toHaveLength(1);
  });

  it("returns recorded and strips the sound id", async () => {
    expect(await post("/v1/events/save", { sound_id: " 7209633324539693830 " })).toMatchObject({ status: 200, data: { status: "recorded" } });
    expect((await get("/v1/leaderboard")).data.entries[0].sound_id).toBe("7209633324539693830");
  });

  it.each(["a".repeat(100), "id with spaces", "drop;table", ""])("rejects junk sound_id %j", async (bad) => {
    expect((await post("/v1/events/save", { sound_id: bad })).status).toBe(422);
  });

  it("clamps the leaderboard limit", async () => {
    expect((await get("/v1/leaderboard?limit=10000000")).data.detail[0]).toMatchObject({ type: "less_than_equal", ctx: { le: 100 } });
    expect((await get("/v1/leaderboard?limit=0")).data.detail[0]).toMatchObject({ type: "greater_than_equal", ctx: { ge: 1 } });
    expect((await get("/v1/leaderboard?limit=abc")).data.detail[0].type).toBe("int_parsing");
    expect((await get("/v1/leaderboard?limit=50")).status).toBe(200);
  });

  it("caps how fast one sound id can climb", async () => {
    overrides = { SOUND_VAULT_RELAY_RATE_LIMIT: "2" };
    const statuses = [];
    for (let i = 0; i < 3; i++) statuses.push((await post("/v1/events/save", { sound_id: "999" }, { "cf-connecting-ip": `198.51.${i}.2` })).status);
    expect(statuses).toEqual([200, 200, 429]);
  });
});

describe("POST /v1/admin/forward", () => {
  const admin = { "x-admin-token": "test-admin-token" };

  async function stranded() {
    const oldPair = await pair("old");
    const newPair = await pair("new");
    await post("/v1/inbox/submit", { pair_code: oldPair.pair_code, url: "https://www.tiktok.com/t/1/", source: "ios_shortcut" });
    clock += 1;
    await post("/v1/inbox/submit", { pair_code: oldPair.pair_code, url: "https://www.tiktok.com/t/2/", source: "test" });
    return { oldPair, newPair };
  }

  it("is invisible (404) without the right token", async () => {
    const body = { source_code: "A", target_code: "B" };
    expect(await post("/v1/admin/forward", body)).toMatchObject({ status: 404, data: { detail: "Not Found" } });
    expect((await post("/v1/admin/forward", body, { "x-admin-token": "wrong" })).status).toBe(404);
    overrides = { RELAY_ADMIN_TOKEN: "" };
    expect((await post("/v1/admin/forward", body, admin)).status).toBe(404); // unset token = disabled
  });

  it("dry-runs, then moves waiting items to the new pair code", async () => {
    const { oldPair, newPair } = await stranded();
    const body = { source_code: oldPair.pair_code, target_code: newPair.pair_code, exclude_sources: ["test"] };
    const dry = await post("/v1/admin/forward", { ...body, dry_run: true }, admin);
    expect(dry.status).toBe(200);
    expect(dry.data).toEqual({ dry_run: true, would_move: 1, items: [{ url: "https://www.tiktok.com/t/1/", source: "ios_shortcut", created_at: 1000 }] });
    const moved = await post("/v1/admin/forward", body, admin);
    expect(moved.data).toMatchObject({ dry_run: false, moved: 1 });
    expect((await get(pollPath(newPair.pair_code), creds(newPair))).data.items.map((i: any) => i.url)).toEqual(["https://www.tiktok.com/t/1/"]);
    expect((await get(pollPath(oldPair.pair_code), creds(oldPair))).data.items.map((i: any) => i.url)).toEqual(["https://www.tiktok.com/t/2/"]);
  });

  it("400s an unknown target and 422s a bad body", async () => {
    const { oldPair } = await stranded();
    expect(await post("/v1/admin/forward", { source_code: oldPair.pair_code, target_code: "NOPE" }, admin)).toMatchObject({
      status: 400,
      data: { detail: "target pair code is unknown or expired" },
    });
    const bad = await post("/v1/admin/forward", { source_code: "", target_code: "B", dry_run: "maybe" }, admin);
    expect(bad.status).toBe(422);
    expect(bad.data.detail.map((d: any) => d.type)).toEqual(["string_too_short", "bool_parsing"]);
  });
});

describe("CORS", () => {
  it("allows GET from soundcache.io origins and nothing else", async () => {
    for (const ok of ["https://soundcache.io", "https://www.soundcache.io", "https://soundcache-web.vercel.app", "http://localhost:8000"]) {
      expect(CORS_ORIGIN_REGEX.test(ok)).toBe(true);
      expect((await get("/v1/leaderboard", { origin: ok })).headers.get("access-control-allow-origin")).toBe(ok);
    }
    for (const bad of ["https://evil.com", "https://soundcache.io.evil.com", "https://notsoundcache.io"]) {
      expect(CORS_ORIGIN_REGEX.test(bad)).toBe(false);
      expect((await get("/v1/leaderboard", { origin: bad })).headers.get("access-control-allow-origin")).toBeNull();
    }
  });

  it("answers preflights like Starlette's CORSMiddleware", async () => {
    const ok = await call("OPTIONS", "/v1/leaderboard", {
      headers: { origin: "https://soundcache.io", "access-control-request-method": "GET", "access-control-request-headers": "x-foo" },
    });
    expect(ok.status).toBe(200);
    expect(ok.headers.get("access-control-allow-methods")).toBe("GET");
    expect(ok.headers.get("access-control-allow-headers")).toBe("x-foo");
    const post_ = await call("OPTIONS", "/v1/inbox/submit", { headers: { origin: "https://soundcache.io", "access-control-request-method": "POST" } });
    expect(post_).toMatchObject({ status: 400, data: "Disallowed CORS method" });
    const evil = await call("OPTIONS", "/v1/leaderboard", { headers: { origin: "https://evil.com", "access-control-request-method": "GET" } });
    expect(evil).toMatchObject({ status: 400, data: "Disallowed CORS origin" });
  });
});

describe("log masking and URL safety", () => {
  it("masks pair codes, secrets, tokens, URLs and drops notes", () => {
    expect(
      maskLogFields({
        pair_code: "river-abcd-efgh",
        device_secret: "s3cret",
        admin_token: "t",
        url: "https://www.tiktok.com/t/abc/?x=1",
        note: "private",
        device_id: "dev_1",
      }),
    ).toEqual({ pair_code: "RIVE…EFGH", device_secret: "[REDACTED]", admin_token: "[REDACTED]", url: "https://www.tiktok.com", device_id: "dev_1" });
    expect(maskLogFields({ pair_code: "ab" })).toEqual({ pair_code: "…AB" });
  });

  it.each(["https://www.tiktok.com/music/x-123", "https://www.instagram.com/reel/abc/", "https://youtu.be/dQw4w9WgXcQ", "http://example.com/page", "http://100.64.0.1/", "http://[2606:4700::1111]/"])(
    "accepts public %s",
    (url) => expect(isSafePublicUrl(url)).toBe(true),
  );

  it.each([
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://127.0.0.1/admin",
    "http://localhost:8080/",
    "http://foo.localhost/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/internal",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://[fe80::1]/",
    "http://[fd00::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://224.0.0.1/",
    "http://0.0.0.0/",
    "http://metadata.google.internal/",
    "https://" + "a".repeat(3000) + ".com/",
    "",
  ])("rejects %s", (url) => expect(isSafePublicUrl(url)).toBe(false));
});
