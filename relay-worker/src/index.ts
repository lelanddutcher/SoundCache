// SoundCache phone relay on Cloudflare Workers + D1.
//
// Wire-compatible port of the FastAPI app in src/sound_vault/relay/server.py: same
// paths, methods, JSON bodies, status codes, `{"detail": ...}` error shapes, secret
// hashing, rate limiting and log masking. The Mac app, the iOS Shortcut and the
// soundcache.io pages talk to it unchanged.

import { ForwardError, RelayStore, pyStrip } from "./store";
import { Checker, RequestValidationError, isPlausibleSoundId, readJsonBody } from "./validation";

export interface Env {
  DB: D1Database;
  RELAY_ADMIN_TOKEN?: string;
  SOUND_VAULT_RELAY_RATE_LIMIT?: string;
  SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS?: string;
}

export interface HandleOptions {
  now?: () => number;
}

class HttpError extends Error {
  constructor(public readonly status: number, public readonly detail: string, public readonly headers: Record<string, string> = {}) {
    super(detail);
  }
}

// ---------------------------------------------------------------------------- CORS
// Same origins as server._CORS_ORIGIN_REGEX; only cross-origin GET is allowed.
export const CORS_ORIGIN_REGEX = /^https:\/\/(www\.)?soundcache\.io$|^https:\/\/[a-z0-9-]+\.vercel\.app$|^http:\/\/localhost(:\d+)?$/;

function corsPreflight(request: Request): Response {
  const origin = request.headers.get("origin") || "";
  const method = request.headers.get("access-control-request-method") || "";
  const headers = new Headers({
    vary: "Origin",
    "access-control-allow-methods": "GET",
    "access-control-max-age": "600",
    "content-type": "text/plain; charset=utf-8",
  });
  const requested = request.headers.get("access-control-request-headers");
  if (requested) headers.set("access-control-allow-headers", requested);
  const failures: string[] = [];
  if (CORS_ORIGIN_REGEX.test(origin)) headers.set("access-control-allow-origin", origin);
  else failures.push("origin");
  if (method !== "GET") failures.push("method");
  if (failures.length) return new Response(`Disallowed CORS ${failures.join(", ")}`, { status: 400, headers });
  return new Response("OK", { status: 200, headers });
}

function withCors(request: Request, response: Response): Response {
  const origin = request.headers.get("origin");
  if (!origin || !CORS_ORIGIN_REGEX.test(origin)) return response;
  const out = new Response(response.body, response);
  out.headers.set("access-control-allow-origin", origin);
  out.headers.append("vary", "Origin");
  return out;
}

// ------------------------------------------------------------------------- logging
function maskPairCode(code: string): string {
  const value = pyStrip(code).toUpperCase();
  if (value.length <= 4) return "…" + value.slice(-2);
  return `${value.slice(0, 4)}…${value.slice(-4)}`;
}

function maskHost(value: string): string {
  try {
    const u = new URL(value);
    return u.protocol && u.hostname ? `${u.protocol}//${u.hostname}` : "[REDACTED]";
  } catch {
    return "[REDACTED]";
  }
}

/** server.log_relay_event: never logs secrets, tokens, full URLs or user notes. */
export function maskLogFields(fields: Record<string, string>): Record<string, string> {
  const safe: Record<string, string> = {};
  for (const [key, value] of Object.entries(fields)) {
    if (key === "pair_code") safe[key] = maskPairCode(value);
    else if (key.includes("secret") || key.includes("token") || key.includes("credential")) safe[key] = value ? "[REDACTED]" : "";
    else if (key === "url") safe[key] = maskHost(value);
    else if (key === "note") continue;
    else safe[key] = value;
  }
  return safe;
}

export function logRelayEvent(event: string, fields: Record<string, string>): void {
  console.log(`relay.${event} ${JSON.stringify(maskLogFields(fields))}`);
}

// --------------------------------------------------------------------- rate limits
export function clientIp(request: Request): string {
  // On Cloudflare the connecting IP is authoritative and cannot be forged by the
  // client (a left-most X-Forwarded-For can). Fall back to the headers the Python
  // relay used so local/dev callers still get distinct buckets.
  const cf = request.headers.get("cf-connecting-ip");
  if (cf) return cf.trim();
  const xff = request.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  const real = request.headers.get("x-real-ip");
  if (real) return real.trim();
  return "unknown";
}

function intVar(value: string | undefined, fallback: number): number {
  const n = Number.parseInt(value ?? "", 10);
  return Number.isFinite(n) ? n : fallback;
}

async function enforceRateLimit(
  ctx: Ctx,
  bucket: string,
  opts: { subject?: string; byHost?: boolean } = {},
): Promise<void> {
  const limit = intVar(ctx.env.SOUND_VAULT_RELAY_RATE_LIMIT, 60);
  const window = intVar(ctx.env.SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS, 60);
  const host = clientIp(ctx.request);
  const subject = opts.subject ?? "";
  const byHost = opts.byHost ?? true;
  let key: string;
  if (subject && !byHost) key = `${bucket}:${subject}`;
  else if (subject) key = `${bucket}:${subject}:${host}`;
  else key = `${bucket}:${host}`;
  if (!(await ctx.store.allow(key, limit, window))) {
    logRelayEvent("rate_limited", { bucket, client: host });
    throw new HttpError(429, "rate limit exceeded");
  }
}

// ------------------------------------------------------------------------ handlers
interface Ctx {
  request: Request;
  env: Env;
  url: URL;
  store: RelayStore;
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

async function health(): Promise<Response> {
  return json({ status: "ok" });
}

async function createPairing(ctx: Ctx): Promise<Response> {
  const body = await readJsonBody(ctx.request);
  const c = new Checker();
  const deviceName = c.str(body, "device_name", ["body"], { required: true, max: 128 });
  c.throwIfAny();
  await enforceRateLimit(ctx, "pairing_create");
  const pair = await ctx.store.createPairing(deviceName!);
  logRelayEvent("pairing_created", { pair_code: pair.code, device_id: pair.deviceId });
  return json({ pair_code: pair.code, device_id: pair.deviceId, device_secret: pair.deviceSecret, expires_at: pair.expiresAt });
}

async function submitLink(ctx: Ctx): Promise<Response> {
  const body = await readJsonBody(ctx.request);
  const c = new Checker();
  const pairCode = c.str(body, "pair_code", ["body"], { required: true, max: 64 });
  const url = c.httpUrl(body, "url", ["body"]);
  const source = c.str(body, "source", ["body"], { default: "ios_shortcut", max: 64 });
  const note = c.str(body, "note", ["body"], { default: "", max: 2048 });
  c.throwIfAny();
  await enforceRateLimit(ctx, "inbox_submit");
  // Second cap keyed on the target pair code: bounds how fast any one inbox fills,
  // even from many IPs (anti-flood for the victim desktop).
  await enforceRateLimit(ctx, "inbox_submit_pair", { subject: pyStrip(pairCode!).toUpperCase(), byHost: false });
  if (!(await ctx.store.canAcceptPairCode(pairCode!))) {
    logRelayEvent("submit_rejected", { pair_code: pairCode!, url: url! });
    throw new HttpError(404, "unknown or expired pairing code");
  }
  const id = await ctx.store.submitLink(pairCode!, url!, source!, note!);
  logRelayEvent("submit_queued", { pair_code: pairCode!, url: url!, source: source! });
  return json({ id, status: "queued" });
}

async function poll(ctx: Ctx): Promise<Response> {
  const c = new Checker();
  const pairCode = c.queryStr(ctx.url.searchParams, "pair_code", { required: true, max: 64 });
  c.throwIfAny();
  const deviceId = ctx.request.headers.get("x-device-id") ?? "";
  const deviceSecret = ctx.request.headers.get("x-device-secret") ?? "";
  await enforceRateLimit(ctx, "inbox_poll");
  if (!deviceId || !deviceSecret) throw new HttpError(401, "missing device credentials");
  const items = await ctx.store.poll(deviceId, deviceSecret, pairCode!);
  // Report pairing state so a lapsed pairing is distinguishable from an empty queue.
  const pairingOk = await ctx.store.canAcceptPairCode(pairCode!);
  logRelayEvent("poll", { pair_code: pairCode!, device_id: deviceId, pairing_ok: pairingOk ? "True" : "False" });
  return json({
    items: items.map((i) => ({ id: i.id, url: i.url, source: i.source, note: i.note })),
    pairing: pairingOk ? "ok" : "unknown_or_expired",
  });
}

async function recordSaveEvent(ctx: Ctx): Promise<Response> {
  const body = await readJsonBody(ctx.request);
  const c = new Checker();
  let soundId = c.str(body, "sound_id", ["body"], { required: true, max: 64 });
  if (soundId !== undefined) {
    const stripped = pyStrip(soundId);
    if (!isPlausibleSoundId(stripped)) {
      c.issue("value_error", ["body", "sound_id"], "Value error, sound_id must be a short alphanumeric id", soundId, { error: {} });
      soundId = undefined;
    } else soundId = stripped;
  }
  const platform = c.str(body, "platform", ["body"], { default: "", max: 32 });
  const title = c.str(body, "title", ["body"], { default: "", max: 512 });
  const artist = c.str(body, "artist", ["body"], { default: "", max: 256 });
  c.throwIfAny();
  await enforceRateLimit(ctx, "events_save");
  // Save events are anonymous, so cap how fast any one sound_id can climb regardless of IP.
  await enforceRateLimit(ctx, "events_save_sound", { subject: soundId!, byHost: false });
  await ctx.store.recordSave(soundId!, platform!, title!, artist!);
  logRelayEvent("save_event", { sound_id: soundId!, platform: platform! });
  return json({ status: "recorded" });
}

async function getLeaderboard(ctx: Ctx): Promise<Response> {
  const c = new Checker();
  const limit = c.queryInt(ctx.url.searchParams, "limit", 50, 1, 100);
  const window = c.queryStr(ctx.url.searchParams, "window", { default: "all", max: 16 });
  c.throwIfAny();
  await enforceRateLimit(ctx, "leaderboard");
  const entries = await ctx.store.leaderboard(limit!, window!);
  return json({ window, limit, entries });
}

async function adminForward(ctx: Ctx): Promise<Response> {
  const body = await readJsonBody(ctx.request);
  const c = new Checker();
  const sourceCode = c.str(body, "source_code", ["body"], { required: true, min: 1, max: 64 });
  const targetCode = c.str(body, "target_code", ["body"], { required: true, min: 1, max: 64 });
  const excludeSources = c.strList(body, "exclude_sources", ["body"]);
  const dryRun = c.bool(body, "dry_run", ["body"], false);
  c.throwIfAny();
  // Ops-only. 404 (not 401/403) on a missing/wrong token keeps it invisible to scanners.
  const expected = ctx.env.RELAY_ADMIN_TOKEN || "";
  const provided = ctx.request.headers.get("x-admin-token") ?? "";
  if (!expected || !(await constantTimeEquals(provided, expected))) throw new HttpError(404, "Not Found");
  let result: Record<string, unknown>;
  try {
    result = await ctx.store.forwardPending({
      sourceCode: pyStrip(sourceCode!),
      targetCode: pyStrip(targetCode!),
      excludeSources: excludeSources!,
      dryRun: dryRun!,
    });
  } catch (err) {
    if (err instanceof ForwardError) throw new HttpError(400, err.message);
    throw err;
  }
  logRelayEvent("admin_forward", {
    source: sourceCode!,
    target: targetCode!,
    moved: String(result.moved ?? result.would_move ?? 0),
    dry_run: dryRun ? "True" : "False",
  });
  return json(result);
}

async function constantTimeEquals(a: string, b: string): Promise<boolean> {
  // Hash both sides first so the comparison is fixed-length and length-oblivious.
  const enc = new TextEncoder();
  const [ha, hb] = await Promise.all([crypto.subtle.digest("SHA-256", enc.encode(a)), crypto.subtle.digest("SHA-256", enc.encode(b))]);
  return crypto.subtle.timingSafeEqual(ha, hb) && a.length === b.length;
}

// -------------------------------------------------------------------------- router
type Handler = (ctx: Ctx) => Promise<Response>;
const ROUTES: Record<string, Record<string, Handler>> = {
  "/v1/health": { GET: health },
  "/v1/pairing/create": { POST: createPairing },
  "/v1/inbox/submit": { POST: submitLink },
  "/v1/inbox/poll": { GET: poll },
  "/v1/events/save": { POST: recordSaveEvent },
  "/v1/leaderboard": { GET: getLeaderboard },
  "/v1/admin/forward": { POST: adminForward },
};

// Old serverless entrypoint prefixes (server._ENTRYPOINT_PREFIXES), stripped exactly.
const ENTRYPOINT_PREFIXES = ["/api/index.py", "/api/index"];

function normalisePath(path: string): string {
  for (const prefix of ENTRYPOINT_PREFIXES) {
    if (path === prefix || path.startsWith(prefix + "/")) return path.slice(prefix.length) || "/";
  }
  return path;
}

export async function handle(request: Request, env: Env, options: HandleOptions = {}): Promise<Response> {
  const now = options.now ?? (() => Date.now() / 1000);
  const url = new URL(request.url);
  if (request.method === "OPTIONS" && request.headers.has("origin") && request.headers.has("access-control-request-method")) {
    return corsPreflight(request);
  }
  const path = normalisePath(url.pathname);
  let response: Response;
  try {
    let methods = ROUTES[path];
    if (!methods && path.endsWith("/") && ROUTES[path.slice(0, -1)]) {
      // Starlette redirect_slashes behaviour.
      const target = new URL(url);
      target.pathname = path.slice(0, -1);
      return withCors(request, new Response(null, { status: 307, headers: { location: target.toString() } }));
    }
    if (!methods) throw new HttpError(404, "Not Found");
    const handler = methods[request.method];
    if (!handler) throw new HttpError(405, "Method Not Allowed", { allow: Object.keys(methods).join(", ") });
    response = await handler({ request, env, url, store: new RelayStore(env.DB, now) });
  } catch (err) {
    if (err instanceof RequestValidationError) response = json({ detail: err.issues }, 422);
    else if (err instanceof HttpError) response = json({ detail: err.detail }, err.status, err.headers);
    else {
      console.error("relay.unhandled_error", err instanceof Error ? err.stack || err.message : String(err));
      response = new Response("Internal Server Error", { status: 500, headers: { "content-type": "text/plain; charset=utf-8" } });
    }
  }
  return withCors(request, response);
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handle(request, env);
  },
} satisfies ExportedHandler<Env>;
