from __future__ import annotations

from collections import defaultdict, deque
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl, field_validator

from sound_vault.relay.inbox import InboxStore
from sound_vault.relay.leaderboard import LeaderboardStore
from sound_vault.relay.pairing import PairingRegistry
from sound_vault.url_safety import is_safe_public_url

app = FastAPI(title="Sound Cache Pairing Relay", version="0.1.0")
# The only browser caller is the landing page reading the public leaderboard (GET).
# Submit/poll come from the iOS Shortcut + desktop (non-browser, CORS-exempt), so we
# only need to allow cross-origin GET — and only from our own origins. This kills
# browser-driven cross-site POSTs entirely.
_CORS_ORIGIN_REGEX = r"^https://(www\.)?soundcache\.io$|^https://[a-z0-9-]+\.vercel\.app$|^http://localhost(:\d+)?$"
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_methods=["GET"],
    allow_headers=["*"],
)
logger = logging.getLogger(__name__)
pairings = PairingRegistry()


class RateLimiter:
    def __init__(self, *, now=None) -> None:
        import time

        self._now = now or time.time
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        self._hits.clear()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return True
        now = self._now()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def build_rate_limiter():
    """Durable Postgres limiter in production (works across serverless instances);
    in-memory only when no database is configured (local dev/tests)."""
    dsn = os.getenv("DATABASE_URL") or os.getenv("SOUND_VAULT_RELAY_DATABASE_URL") or ""
    if dsn:
        try:
            from sound_vault.relay.pg_store import PostgresRateLimiter

            return PostgresRateLimiter(dsn)
        except Exception:  # noqa: BLE001 - fall back to in-memory rather than fail boot
            logger.exception("relay.rate_limiter_pg_init_failed")
    return RateLimiter()


rate_limiter = build_rate_limiter()


def _mask_pair_code(pair_code: str) -> str:
    value = pair_code.strip().upper()
    if len(value) <= 4:
        return "…" + value[-2:]
    return f"{value[:4]}…{value[-4:]}"


def _mask_value(value: str) -> str:
    if not value:
        return ""
    return "[REDACTED]"


def _mask_host(value: str) -> str:
    # keep just the scheme+host for diagnostics; never log full URLs/notes/paths.
    try:
        from urllib.parse import urlparse

        p = urlparse(value)
        return f"{p.scheme}://{p.hostname}" if p.scheme and p.hostname else "[REDACTED]"
    except ValueError:
        return "[REDACTED]"


def log_relay_event(event: str, **fields: str) -> None:
    safe_fields = {}
    for key, value in fields.items():
        if key == "pair_code":
            safe_fields[key] = _mask_pair_code(value)
        elif "secret" in key or "token" in key or "credential" in key:
            safe_fields[key] = _mask_value(value)
        elif key == "url":
            safe_fields[key] = _mask_host(value)  # host only, never the full link
        elif key == "note":
            continue  # never log free-form user text
        else:
            safe_fields[key] = value
    logger.info("relay.%s %s", event, safe_fields)


def _client_ip(request: Request) -> str:
    # On Vercel the real client IP is in x-forwarded-for (left-most) / x-real-ip;
    # request.client.host is the proxy. Fall back to the socket peer locally.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip", "")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, *, bucket: str, subject: str = "", by_host: bool = True) -> None:
    limit = int(os.getenv("SOUND_VAULT_RELAY_RATE_LIMIT", "60"))
    window = int(os.getenv("SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS", "60"))
    host = _client_ip(request)
    # by_host=True  -> per-IP cap (one client's request rate).
    # by_host=False -> subject-only cap (e.g. total fill rate of one pair code's
    #                  inbox, across ALL IPs) so a victim can't be flooded by a botnet.
    if subject and not by_host:
        key = f"{bucket}:{subject}"
    elif subject:
        key = f"{bucket}:{subject}:{host}"
    else:
        key = f"{bucket}:{host}"
    if not rate_limiter.allow(key, limit=limit, window_seconds=window):
        log_relay_event("rate_limited", bucket=bucket, client=host)
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def _database_url() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("SOUND_VAULT_RELAY_DATABASE_URL") or ""


def build_inbox_store():
    dsn = _database_url()
    if dsn:
        from sound_vault.relay.pg_store import PostgresInboxStore

        return PostgresInboxStore(dsn)
    storage_path = os.getenv("SOUND_VAULT_RELAY_STORAGE_PATH")
    return InboxStore(db_path=Path(storage_path).expanduser()) if storage_path else InboxStore()


def build_leaderboard_store():
    dsn = _database_url()
    if dsn:
        from sound_vault.relay.pg_store import PostgresLeaderboardStore

        return PostgresLeaderboardStore(dsn)
    storage_path = os.getenv("SOUND_VAULT_LEADERBOARD_STORAGE_PATH")
    return LeaderboardStore(db_path=Path(storage_path).expanduser()) if storage_path else LeaderboardStore()


inbox = build_inbox_store()
leaderboard_store = build_leaderboard_store()


class CreatePairingRequest(BaseModel):
    device_name: str = Field(max_length=128)


class SubmitLinkRequest(BaseModel):
    pair_code: str = Field(max_length=64)
    url: HttpUrl
    source: str = Field(default="ios_shortcut", max_length=64)
    note: str = Field(default="", max_length=2048)

    @field_validator("url")
    @classmethod
    def _reject_unsafe_url(cls, v: HttpUrl) -> HttpUrl:
        # SSRF defense: only http(s) to a public host, reasonable length. The
        # link is later fetched by the paired desktop, so block private/reserved
        # IPs + internal hostnames + non-web schemes at the door.
        if not is_safe_public_url(str(v)):
            raise ValueError("url must be an http(s) link to a public host")
        return v


class SaveEventRequest(BaseModel):
    sound_id: str = Field(max_length=64)
    platform: str = Field(default="", max_length=32)
    title: str = Field(default="", max_length=512)
    artist: str = Field(default="", max_length=256)

    @field_validator("sound_id")
    @classmethod
    def _plausible_sound_id(cls, v: str) -> str:
        # Real platform ids are short alphanumerics (TikTok numeric, YouTube/IG
        # alnum). Reject junk so the public leaderboard can't be polluted with
        # arbitrary strings, and cap length to bound abuse.
        sid = v.strip()
        if not sid or len(sid) > 64 or not all(ch.isalnum() or ch in "-_" for ch in sid):
            raise ValueError("sound_id must be a short alphanumeric id")
        return sid


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/pairing/create")
def create_pairing(request: CreatePairingRequest, http_request: Request) -> dict[str, str | float]:
    enforce_rate_limit(http_request, bucket="pairing_create")
    pair = pairings.create_pairing_code(device_name=request.device_name)
    inbox.register_device(device_id=pair.device_id, device_secret=pair.device_secret)
    inbox.register_pair_code(pair.code, device_id=pair.device_id)
    log_relay_event("pairing_created", pair_code=pair.code, device_id=pair.device_id)
    return {
        "pair_code": pair.code,
        "device_id": pair.device_id,
        "device_secret": pair.device_secret,
        "expires_at": pair.expires_at,
    }


@app.post("/v1/inbox/submit")
def submit_link(request: SubmitLinkRequest, http_request: Request) -> dict[str, str]:
    enforce_rate_limit(http_request, bucket="inbox_submit")
    # second cap keyed on the target pair code: caps how fast any one inbox can be
    # filled, even from a botnet of IPs (anti-flood for the victim desktop).
    enforce_rate_limit(http_request, bucket="inbox_submit_pair", subject=request.pair_code.strip().upper(), by_host=False)
    if not inbox.can_accept_pair_code(request.pair_code):
        log_relay_event("submit_rejected", pair_code=request.pair_code, url=str(request.url))
        raise HTTPException(status_code=404, detail="unknown or expired pairing code")
    item = inbox.submit_link(
        pair_code=request.pair_code, url=str(request.url), source=request.source, note=request.note
    )
    log_relay_event("submit_queued", pair_code=request.pair_code, url=str(request.url), source=request.source)
    return {"id": item.id, "status": "queued"}


@app.get("/v1/inbox/poll")
def poll(
    request: Request,
    pair_code: str = Query(max_length=64),
    x_device_id: str = Header(default=""),
    x_device_secret: str = Header(default=""),
) -> dict[str, list[dict[str, str]]]:
    enforce_rate_limit(request, bucket="inbox_poll")
    if not x_device_id or not x_device_secret:
        raise HTTPException(status_code=401, detail="missing device credentials")
    items = inbox.poll(device_id=x_device_id, device_secret=x_device_secret, pair_code=pair_code)
    # Never pass the device secret into the log call — even though log_relay_event
    # redacts "*secret*" keys, the raw value would sit in this frame for a debugger.
    log_relay_event("poll", pair_code=pair_code, device_id=x_device_id)
    return {"items": [{"id": item.id, "url": item.url, "source": item.source, "note": item.note} for item in items]}


@app.post("/v1/events/save")
def record_save_event(request: SaveEventRequest, http_request: Request) -> dict[str, str]:
    enforce_rate_limit(http_request, bucket="events_save")
    sound_id = request.sound_id.strip()
    if not sound_id:
        raise HTTPException(status_code=422, detail="sound_id is required")
    # Save events are anonymous by design (no device secret — privacy), so cap how
    # fast any single sound_id can be incremented regardless of source IP. Bounds
    # leaderboard poisoning without breaking the anonymized model.
    enforce_rate_limit(http_request, bucket="events_save_sound", subject=sound_id, by_host=False)
    leaderboard_store.record_save(
        sound_id=sound_id, platform=request.platform, title=request.title, artist=request.artist
    )
    log_relay_event("save_event", sound_id=sound_id, platform=request.platform)
    return {"status": "recorded"}


@app.get("/v1/leaderboard")
def get_leaderboard(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    window: str = Query(default="all", max_length=16),
) -> dict[str, object]:
    enforce_rate_limit(request, bucket="leaderboard")
    entries = leaderboard_store.leaderboard(limit=limit, window=window)
    return {
        "window": window,
        "limit": limit,
        "entries": [
            {
                "sound_id": entry.sound_id,
                "title": entry.title,
                "artist": entry.artist,
                "platform": entry.platform,
                "saves": entry.saves,
            }
            for entry in entries
        ],
    }


def main() -> None:
    import os

    import uvicorn

    host = os.getenv("SOUND_VAULT_RELAY_HOST", "127.0.0.1")
    port = int(os.getenv("SOUND_VAULT_RELAY_PORT", "43117"))
    uvicorn.run("sound_vault.relay.server:app", host=host, port=port, reload=False)
