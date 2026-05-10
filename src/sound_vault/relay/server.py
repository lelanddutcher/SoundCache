from __future__ import annotations

from collections import defaultdict, deque
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, HttpUrl

from sound_vault.relay.inbox import InboxStore
from sound_vault.relay.pairing import PairingRegistry

app = FastAPI(title="Sound Vault Pairing Relay", version="0.1.0")
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


rate_limiter = RateLimiter()


def _mask_pair_code(pair_code: str) -> str:
    value = pair_code.strip().upper()
    if len(value) <= 4:
        return "…" + value[-2:]
    return f"{value[:4]}…{value[-4:]}"


def _mask_value(value: str) -> str:
    if not value:
        return ""
    return "[REDACTED]"


def log_relay_event(event: str, **fields: str) -> None:
    safe_fields = {}
    for key, value in fields.items():
        if key == "pair_code":
            safe_fields[key] = _mask_pair_code(value)
        elif "secret" in key or "token" in key or "credential" in key:
            safe_fields[key] = _mask_value(value)
        else:
            safe_fields[key] = value
    logger.info("relay.%s %s", event, safe_fields)


def enforce_rate_limit(request: Request, *, bucket: str) -> None:
    limit = int(os.getenv("SOUND_VAULT_RELAY_RATE_LIMIT", "60"))
    window = int(os.getenv("SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS", "60"))
    host = request.client.host if request.client else "unknown"
    key = f"{bucket}:{host}"
    if not rate_limiter.allow(key, limit=limit, window_seconds=window):
        log_relay_event("rate_limited", bucket=bucket, client=host)
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def build_inbox_store() -> InboxStore:
    storage_path = os.getenv("SOUND_VAULT_RELAY_STORAGE_PATH")
    return InboxStore(db_path=Path(storage_path).expanduser()) if storage_path else InboxStore()


inbox = build_inbox_store()


class CreatePairingRequest(BaseModel):
    device_name: str


class SubmitLinkRequest(BaseModel):
    pair_code: str
    url: HttpUrl
    source: str = "ios_shortcut"


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
    if not inbox.can_accept_pair_code(request.pair_code):
        log_relay_event("submit_rejected", pair_code=request.pair_code, url=str(request.url))
        raise HTTPException(status_code=404, detail="unknown or expired pairing code")
    item = inbox.submit_link(pair_code=request.pair_code, url=str(request.url), source=request.source)
    log_relay_event("submit_queued", pair_code=request.pair_code, url=str(request.url), source=request.source)
    return {"id": item.id, "status": "queued"}


@app.get("/v1/inbox/poll")
def poll(pair_code: str, request: Request, x_device_id: str = Header(default=""), x_device_secret: str = Header(default="")) -> dict[str, list[dict[str, str]]]:
    enforce_rate_limit(request, bucket="inbox_poll")
    if not x_device_id or not x_device_secret:
        raise HTTPException(status_code=401, detail="missing device credentials")
    items = inbox.poll(device_id=x_device_id, device_secret=x_device_secret, pair_code=pair_code)
    log_relay_event("poll", pair_code=pair_code, device_id=x_device_id, device_secret=x_device_secret)
    return {"items": [{"id": item.id, "url": item.url, "source": item.source} for item in items]}


def main() -> None:
    import os

    import uvicorn

    host = os.getenv("SOUND_VAULT_RELAY_HOST", "127.0.0.1")
    port = int(os.getenv("SOUND_VAULT_RELAY_PORT", "43117"))
    uvicorn.run("sound_vault.relay.server:app", host=host, port=port, reload=False)
