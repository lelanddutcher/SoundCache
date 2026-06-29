"""Relay security hardening: SSRF/URL safety, length limits, per-pair flood caps."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import sound_vault.relay.server as relay_server
from sound_vault.relay.inbox import InboxStore
from sound_vault.relay.pairing import PairingRegistry
from sound_vault.url_safety import is_safe_public_url, is_safe_to_fetch


# --- url_safety unit checks -------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/music/x-123",
    "https://www.instagram.com/reel/abc/",
    "https://youtu.be/dQw4w9WgXcQ",
    "http://example.com/page",
])
def test_is_safe_public_url_accepts_public_web(url):
    assert is_safe_public_url(url) is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "http://127.0.0.1/admin",
    "http://localhost:8080/",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://10.0.0.5/internal",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://metadata.google.internal/",
    "https://" + "a" * 3000 + ".com/",             # over length
    "",
])
def test_is_safe_public_url_rejects_unsafe(url):
    assert is_safe_public_url(url) is False


def test_is_safe_to_fetch_rejects_literal_private_without_dns():
    # literal private IP fails the cheap check before any DNS work
    assert is_safe_to_fetch("http://127.0.0.1/") is False


# --- relay endpoint hardening ----------------------------------------------


def _client():
    relay_server.pairings = PairingRegistry(now=lambda: 1000.0, code_ttl_seconds=3600)
    relay_server.inbox = InboxStore(now=lambda: 1000.0)
    relay_server.rate_limiter.reset()
    return TestClient(relay_server.app)


def _pair(client):
    return client.post("/v1/pairing/create", json={"device_name": "Mac"}).json()["pair_code"]


def test_submit_rejects_private_ip_url():
    client = _client()
    code = _pair(client)
    r = client.post("/v1/inbox/submit", json={"pair_code": code, "url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 422  # validation rejects before it can be queued


def test_submit_rejects_non_web_scheme():
    client = _client()
    code = _pair(client)
    r = client.post("/v1/inbox/submit", json={"pair_code": code, "url": "file:///etc/passwd"})
    assert r.status_code == 422


def test_submit_rejects_oversized_note():
    client = _client()
    code = _pair(client)
    r = client.post("/v1/inbox/submit", json={
        "pair_code": code, "url": "https://www.tiktok.com/t/abc/", "note": "x" * 5000,
    })
    assert r.status_code == 422


def test_submit_accepts_normal_tiktok_url():
    client = _client()
    code = _pair(client)
    r = client.post("/v1/inbox/submit", json={"pair_code": code, "url": "https://www.tiktok.com/t/abc/", "note": "gym intro"})
    assert r.status_code == 200


def test_per_pair_code_flood_cap_across_ips(monkeypatch):
    # Low limit; submit from DIFFERENT IPs but the SAME pair code -> the
    # per-pair-code bucket still trips, protecting the victim's inbox.
    monkeypatch.setenv("SOUND_VAULT_RELAY_RATE_LIMIT", "2")
    client = _client()
    code = _pair(client)
    codes = []
    for i in range(4):
        r = client.post(
            "/v1/inbox/submit",
            json={"pair_code": code, "url": f"https://www.tiktok.com/t/{i}/"},
            headers={"x-forwarded-for": f"203.0.113.{i}"},  # distinct client IPs
        )
        codes.append(r.status_code)
    assert 429 in codes  # flood capped despite IP rotation


def test_cors_not_wildcard():
    src = (relay_server.__file__)
    import pathlib
    text = pathlib.Path(src).read_text(encoding="utf-8")
    assert 'allow_origins=["*"]' not in text
    assert "allow_origin_regex" in text


def test_poll_rejects_overlong_pair_code():
    client = _client()
    r = client.get(
        "/v1/inbox/poll",
        params={"pair_code": "A" * 5000},
        headers={"X-Device-Id": "d", "X-Device-Secret": "s"},
    )
    assert r.status_code == 422  # bounded before any hashing/logging work


def test_events_save_rejects_junk_sound_id():
    client = _client()
    for bad in ["a" * 100, "id with spaces", "drop;table", ""]:
        r = client.post("/v1/events/save", json={"sound_id": bad})
        assert r.status_code == 422, bad
    # a plausible id is accepted
    ok = client.post("/v1/events/save", json={"sound_id": "7209633324539693830"})
    assert ok.status_code == 200


def test_leaderboard_limit_is_clamped():
    client = _client()
    assert client.get("/v1/leaderboard", params={"limit": 10_000_000}).status_code == 422
    assert client.get("/v1/leaderboard", params={"limit": 0}).status_code == 422
    assert client.get("/v1/leaderboard", params={"limit": 50}).status_code == 200


def test_cors_regex_allows_www_and_apex_but_not_lookalikes():
    """The hits page leaderboard fetch failed ('can't reach the hive mind') because
    www.soundcache.io wasn't an allowed CORS origin. Both apex and www must pass; any
    other host must be rejected."""
    import re

    rx = relay_server._CORS_ORIGIN_REGEX
    for allowed in ("https://soundcache.io", "https://www.soundcache.io",
                    "https://soundcache-web.vercel.app", "http://localhost:8000"):
        assert re.match(rx, allowed), allowed
    for blocked in ("https://evil.com", "https://soundcache.io.evil.com", "https://notsoundcache.io"):
        assert re.match(rx, blocked) is None, blocked
