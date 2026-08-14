"""Guards the 2026-08-14 silent-pairing-lapse failure.

Pair codes carry a 30-day TTL and nothing refreshed it, so a working setup died exactly
30 days after pairing — with no visible signal anywhere:

  * the phone's shares were rejected at submit with 404 "unknown or expired pairing code";
  * the desktop's poll returned ``{"items": []}``, which is byte-identical to a healthy
    empty queue, so the app reported "nothing waiting" and kept doing so for 9 days.

Two defences, both asserted here:
  1. Polling with a valid device secret REFRESHES the pairing, so an in-use pairing never
     lapses (an abandoned one still does).
  2. When a pairing really is gone, the poll response says so, so the desktop can tell the
     difference between "empty" and "broken".
"""
import dataclasses

from fastapi.testclient import TestClient

import sound_vault.relay.server as srv
from sound_vault.relay.inbox import DEFAULT_PAIR_CODE_SUBMISSION_TTL_SECONDS, InboxStore, _hash_pair_code


def _pair(client):
    payload = client.post("/v1/pairing/create", json={"device_name": "test-mac"}).json()
    return payload["pair_code"], {
        "x-device-id": payload["device_id"],
        "x-device-secret": payload["device_secret"],
    }


def test_poll_reports_pairing_ok_even_when_the_queue_is_empty():
    """An empty queue must be distinguishable from a dead pairing."""
    client = TestClient(srv.app)
    code, headers = _pair(client)
    body = client.get("/v1/inbox/poll", params={"pair_code": code}, headers=headers).json()
    assert body["items"] == []
    assert body["pairing"] == "ok"


def test_poll_reports_an_expired_pairing_instead_of_looking_empty():
    client = TestClient(srv.app)
    code, headers = _pair(client)
    for key, registered in list(srv.inbox._pair_codes.items()):
        srv.inbox._pair_codes[key] = dataclasses.replace(registered, expires_at=1.0)

    # The phone's share bounces...
    submit = client.post(
        "/v1/inbox/submit",
        json={"pair_code": code, "url": "https://www.tiktok.com/music/sound-1", "source": "ios"},
    )
    assert submit.status_code == 404
    # ...and the desktop can now SEE why, instead of reading it as an empty queue.
    body = client.get("/v1/inbox/poll", params={"pair_code": code}, headers=headers).json()
    assert body["items"] == []
    assert body["pairing"] == "unknown_or_expired"


def test_polling_refreshes_the_pairing_expiry():
    """An actively-polled pairing must not lapse on the 30-day timer."""
    clock = {"now": 1_000.0}
    store = InboxStore(now=lambda: clock["now"])
    store.register_device(device_id="dev_1", device_secret="s3cret")
    store.register_pair_code("NOVA-TEST-CODE", device_id="dev_1")
    key = _hash_pair_code("NOVA-TEST-CODE")
    first_expiry = store._pair_codes[key].expires_at

    # 29 days later the desktop polls as usual.
    clock["now"] += 29 * 24 * 60 * 60
    store.poll(device_id="dev_1", device_secret="s3cret", pair_code="NOVA-TEST-CODE")
    assert store._pair_codes[key].expires_at > first_expiry, "poll must extend the pairing"

    # Two days on — previously past the original 30-day expiry — it still works.
    clock["now"] += 2 * 24 * 60 * 60
    assert store.can_accept_pair_code("NOVA-TEST-CODE") is True
    store.submit_link(pair_code="NOVA-TEST-CODE", url="https://www.tiktok.com/music/sound-2", source="ios")
    delivered = store.poll(device_id="dev_1", device_secret="s3cret", pair_code="NOVA-TEST-CODE")
    assert len(delivered) == 1


def test_an_abandoned_pairing_still_lapses():
    """The keep-alive must not make pair codes immortal — only in-use ones survive."""
    clock = {"now": 1_000.0}
    store = InboxStore(now=lambda: clock["now"])
    store.register_device(device_id="dev_1", device_secret="s3cret")
    store.register_pair_code("NOVA-TEST-CODE", device_id="dev_1")

    clock["now"] += DEFAULT_PAIR_CODE_SUBMISSION_TTL_SECONDS + 1  # nobody ever polled
    assert store.can_accept_pair_code("NOVA-TEST-CODE") is False
