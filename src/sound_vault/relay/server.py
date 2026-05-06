from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, HttpUrl

from sound_vault.relay.inbox import InboxStore
from sound_vault.relay.pairing import PairingRegistry

app = FastAPI(title="Sound Vault Pairing Relay", version="0.1.0")
pairings = PairingRegistry()
inbox = InboxStore()


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
def create_pairing(request: CreatePairingRequest) -> dict[str, str | float]:
    pair = pairings.create_pairing_code(device_name=request.device_name)
    inbox.register_device(device_id=pair.device_id, device_secret=pair.device_secret)
    return {
        "pair_code": pair.code,
        "device_id": pair.device_id,
        "device_secret": pair.device_secret,
        "expires_at": pair.expires_at,
    }


@app.post("/v1/inbox/submit")
def submit_link(request: SubmitLinkRequest) -> dict[str, str]:
    item = inbox.submit_link(pair_code=request.pair_code, url=str(request.url), source=request.source)
    return {"id": item.id, "status": "queued"}


@app.get("/v1/inbox/poll")
def poll(pair_code: str, x_device_id: str = Header(default=""), x_device_secret: str = Header(default="")) -> dict[str, list[dict[str, str]]]:
    if not x_device_id or not x_device_secret:
        raise HTTPException(status_code=401, detail="missing device credentials")
    items = inbox.poll(device_id=x_device_id, device_secret=x_device_secret, pair_code=pair_code)
    return {"items": [{"id": item.id, "url": item.url, "source": item.source} for item in items]}


def main() -> None:
    import uvicorn

    uvicorn.run("sound_vault.relay.server:app", host="127.0.0.1", port=43117, reload=False)
