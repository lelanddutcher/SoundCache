from fastapi.testclient import TestClient

import sound_vault.relay.server as relay_server
from sound_vault.relay.inbox import InboxStore
from sound_vault.relay.pairing import PairingRegistry


def _fresh_client(now=None, *, pair_code_ttl_seconds=None):
    clock = now or (lambda: 1000.0)
    relay_server.pairings = PairingRegistry(now=clock, code_ttl_seconds=10)
    inbox_kwargs = {"now": clock}
    if pair_code_ttl_seconds is not None:
        inbox_kwargs["pair_code_ttl_seconds"] = pair_code_ttl_seconds
    relay_server.inbox = InboxStore(**inbox_kwargs)
    return TestClient(relay_server.app)


def test_submit_rejects_unknown_pair_code():
    client = _fresh_client()

    response = client.post(
        "/v1/inbox/submit",
        json={
            "pair_code": "NOPE-0000",
            "url": "https://www.tiktok.com/t/abc/",
            "source": "ios_shortcut",
        },
    )

    assert response.status_code == 404


def test_submit_still_works_after_short_pairing_window_expires():
    now = {"value": 1000.0}
    client = _fresh_client(lambda: now["value"])
    pair = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()
    now["value"] = 1011.0

    response = client.post(
        "/v1/inbox/submit",
        json={
            "pair_code": pair["pair_code"],
            "url": "https://www.tiktok.com/t/abc/",
            "source": "ios_shortcut",
        },
    )

    assert response.status_code == 200


def test_submit_rejects_expired_submission_pair_code():
    now = {"value": 1000.0}
    client = _fresh_client(lambda: now["value"], pair_code_ttl_seconds=10)
    pair = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()
    now["value"] = 1011.0

    response = client.post(
        "/v1/inbox/submit",
        json={
            "pair_code": pair["pair_code"],
            "url": "https://www.tiktok.com/t/abc/",
            "source": "ios_shortcut",
        },
    )

    assert response.status_code == 404


def test_valid_pair_code_submit_and_poll_round_trip():
    client = _fresh_client()
    pair = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()
    submit = client.post(
        "/v1/inbox/submit",
        json={
            "pair_code": pair["pair_code"],
            "url": "https://www.tiktok.com/t/abc/",
            "source": "ios_shortcut",
        },
    )

    poll = client.get(
        "/v1/inbox/poll",
        params={"pair_code": pair["pair_code"]},
        headers={"x-device-id": pair["device_id"], "x-device-secret": pair["device_secret"]},
    )

    assert submit.status_code == 200
    assert poll.status_code == 200
    assert poll.json()["items"][0]["url"] == "https://www.tiktok.com/t/abc/"


def test_submit_and_poll_carry_user_note():
    client = _fresh_client()
    pair = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()
    submit = client.post(
        "/v1/inbox/submit",
        json={
            "pair_code": pair["pair_code"],
            "url": "https://www.tiktok.com/t/abc/",
            "source": "ios_shortcut",
            "note": "use for gym intros",
        },
    )
    poll = client.get(
        "/v1/inbox/poll",
        params={"pair_code": pair["pair_code"]},
        headers={"x-device-id": pair["device_id"], "x-device-secret": pair["device_secret"]},
    )

    assert submit.status_code == 200
    assert poll.status_code == 200
    assert poll.json()["items"][0]["note"] == "use for gym intros"


def test_submit_without_note_defaults_to_empty_string():
    client = _fresh_client()
    pair = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()
    client.post(
        "/v1/inbox/submit",
        json={"pair_code": pair["pair_code"], "url": "https://www.tiktok.com/t/abc/", "source": "ios_shortcut"},
    )
    poll = client.get(
        "/v1/inbox/poll",
        params={"pair_code": pair["pair_code"]},
        headers={"x-device-id": pair["device_id"], "x-device-secret": pair["device_secret"]},
    )
    assert poll.json()["items"][0]["note"] == ""


def test_device_b_cannot_poll_pair_code_created_for_device_a():
    client = _fresh_client()
    pair_a = client.post("/v1/pairing/create", json={"device_name": "Studio Mac"}).json()
    pair_b = client.post("/v1/pairing/create", json={"device_name": "Attacker Mac"}).json()
    submit = client.post(
        "/v1/inbox/submit",
        json={
            "pair_code": pair_a["pair_code"],
            "url": "https://www.tiktok.com/t/abc/",
            "source": "ios_shortcut",
        },
    )

    attacker_poll = client.get(
        "/v1/inbox/poll",
        params={"pair_code": pair_a["pair_code"]},
        headers={"x-device-id": pair_b["device_id"], "x-device-secret": pair_b["device_secret"]},
    )
    owner_poll = client.get(
        "/v1/inbox/poll",
        params={"pair_code": pair_a["pair_code"]},
        headers={"x-device-id": pair_a["device_id"], "x-device-secret": pair_a["device_secret"]},
    )

    assert submit.status_code == 200
    assert attacker_poll.status_code == 200
    assert attacker_poll.json()["items"] == []
    assert owner_poll.status_code == 200
    assert owner_poll.json()["items"][0]["url"] == "https://www.tiktok.com/t/abc/"
