"""Black-box compatibility check for the Worker relay, from the Python side.

Two parts:

1. Round trip with the real clients' code paths: create a pairing the way the Mac
   app does (ui/desktop.py), submit a link the way the iOS Shortcut does (JSON POST
   to /v1/inbox/submit), then poll with sound_vault.relay.client.RelayClient into a
   temporary inbox file.
2. --parity: replay the same requests against the original FastAPI app (in process,
   via TestClient) and against BASE_URL, and diff status codes, JSON bodies (random
   ids/secrets/timestamps masked) and CORS headers.

Run from the repository root:

    PYTHONPATH=src python relay-worker/test/compat/check_compat.py http://127.0.0.1:8787 --parity
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from sound_vault.relay.client import RelayClient
from sound_vault.net import ssl_context

UA = {"User-Agent": "soundcache-relay-compat-check/1.0"}


def _http(base: str, method: str, path: str, body=None, headers=None):
    data = None
    hdrs = dict(UA)
    hdrs.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl_context()) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


def round_trip(base: str) -> None:
    status, text, _ = _http(base, "GET", "/v1/health")
    assert status == 200 and json.loads(text) == {"status": "ok"}, (status, text)
    print(f"  health: {status} {text}")

    # Mac app: Settings -> Create pairing code.
    status, text, _ = _http(base, "POST", "/v1/pairing/create", {"device_name": "Sound Cache Desktop"})
    assert status == 200, (status, text)
    pairing = json.loads(text)
    assert set(pairing) == {"pair_code", "device_id", "device_secret", "expires_at"}, pairing
    print(f"  pairing/create: {status}, code {pairing['pair_code'][:4]}…, keys {sorted(pairing)}")

    client = RelayClient(
        base_url=base,
        device_id=pairing["device_id"],
        device_secret=pairing["device_secret"],
        pair_code=pairing["pair_code"],
    )
    assert client.poll() == [] and client.pairing_state == "ok"
    print("  empty poll: [] pairing=ok")

    # iOS Shortcut: share sheet -> POST /v1/inbox/submit.
    url = "https://www.tiktok.com/t/compat-check/"
    status, text, _ = _http(
        base, "POST", "/v1/inbox/submit",
        {"pair_code": pairing["pair_code"], "url": url, "source": "ios_shortcut", "note": "compat check"},
    )
    assert status == 200 and json.loads(text)["status"] == "queued", (status, text)
    print(f"  inbox/submit: {status} {text}")

    with tempfile.TemporaryDirectory() as tmp:
        inbox_path = Path(tmp) / "inbox" / "shortcut-inbox.jsonl"
        items = client.poll_to_inbox(inbox_path)
        assert [(i.url, i.source, i.note) for i in items] == [(url, "ios_shortcut", "compat check")], items
        written = [json.loads(line) for line in inbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert written and written[0]["url"] == url, written
        print(f"  RelayClient.poll_to_inbox: {len(items)} item -> {inbox_path.name} ({written[0]['url']})")
    assert client.poll() == [], "poll must be destructive"
    print("  second poll: [] (delivered once)")

    status, _, _ = _http(base, "POST", "/v1/inbox/submit", {"pair_code": "NOPE-0000-0000", "url": url})
    assert status == 404, status
    print("  unknown pair code: 404")


# ---------------------------------------------------------------------------- parity

_RANDOM_KEYS = {"pair_code", "device_id", "device_secret", "expires_at", "id"}


def _mask(value):
    if isinstance(value, dict):
        return {k: ("<random>" if k in _RANDOM_KEYS and not isinstance(v, (dict, list)) else _mask(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def _norm_detail(body):
    """Compare 422s on type + loc (+ msg/ctx where both are deterministic)."""
    if isinstance(body, dict) and isinstance(body.get("detail"), list):
        out = []
        for d in body["detail"]:
            entry = {"type": d.get("type"), "loc": d.get("loc")}
            if d.get("type") not in ("json_invalid",):
                entry["msg"] = d.get("msg")
                entry["ctx"] = d.get("ctx")
            out.append(entry)
        return {"detail": out}
    return body


class FastAPISide:
    name = "fastapi"

    def __init__(self):
        from fastapi.testclient import TestClient

        import sound_vault.relay.server as srv
        from sound_vault.relay.inbox import InboxStore
        from sound_vault.relay.leaderboard import LeaderboardStore
        from sound_vault.relay.pairing import PairingRegistry

        srv.pairings = PairingRegistry()
        srv.inbox = InboxStore()
        srv.leaderboard_store = LeaderboardStore()
        srv.rate_limiter.reset()
        self.client = TestClient(srv.app)

    def request(self, method, path, body=None, headers=None, raw=None):
        kwargs = {"headers": headers or {}}
        if raw is not None:
            kwargs["content"] = raw
        elif body is not None:
            kwargs["json"] = body
        r = self.client.request(method, path, follow_redirects=False, **kwargs)
        return r.status_code, r.text, {k.lower(): v for k, v in r.headers.items()}


class HttpSide:
    name = "worker"

    def __init__(self, base):
        self.base = base

    def request(self, method, path, body=None, headers=None, raw=None):
        hdrs = dict(UA)
        hdrs.update(headers or {})
        data = None
        if raw is not None:
            data = raw.encode("utf-8")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("content-type", "application/json")
        req = urllib.request.Request(self.base.rstrip("/") + path, data=data, headers=hdrs, method=method)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl_context()))
        try:
            with opener.open(req, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8"), {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8"), {k.lower(): v for k, v in exc.headers.items()}


def _scenario(side):
    """Yield (label, response) for one side. Stateful steps use that side's own pair."""
    def call(label, method, path, **kw):
        status, text, headers = side.request(method, path, **kw)
        try:
            body = json.loads(text)
        except ValueError:
            body = text
        cors = {k: headers.get(k) for k in ("access-control-allow-origin", "access-control-allow-methods") if headers.get(k)}
        return label, status, body, cors, headers

    out = []
    out.append(call("health", "GET", "/v1/health"))
    out.append(call("health via /api/index", "GET", "/api/index/v1/health"))
    out.append(call("unknown route", "GET", "/v1/nope"))
    out.append(call("wrong method", "GET", "/v1/inbox/submit"))
    out.append(call("pairing/create", "POST", "/v1/pairing/create", body={"device_name": "Studio Mac"}))
    pair = out[-1][2]
    other = call("pairing/create #2", "POST", "/v1/pairing/create", body={"device_name": "Other Mac"})
    out.append(other)
    other = other[2]
    out.append(call("pairing missing name", "POST", "/v1/pairing/create", body={}))
    out.append(call("pairing name too long", "POST", "/v1/pairing/create", body={"device_name": "x" * 129}))
    out.append(call("pairing name not str", "POST", "/v1/pairing/create", body={"device_name": 5}))
    out.append(call("pairing empty body", "POST", "/v1/pairing/create", headers={"content-type": "application/json"}, raw=""))
    out.append(call("pairing bad json", "POST", "/v1/pairing/create", headers={"content-type": "application/json"}, raw="{nope"))
    out.append(call("pairing list body", "POST", "/v1/pairing/create", body=[1]))
    code = pair["pair_code"]
    cred = {"x-device-id": pair["device_id"], "x-device-secret": pair["device_secret"]}
    poll = f"/v1/inbox/poll?pair_code={code}"
    out.append(call("empty poll", "GET", poll, headers=cred))
    for url in ["https://www.tiktok.com/t/abc/", "HTTPS://WWW.TikTok.com/t/Case", "https://youtu.be/dQw4w9WgXcQ?t=1"]:
        out.append(call(f"submit {url}", "POST", "/v1/inbox/submit", body={"pair_code": code, "url": url, "note": " hi "}))
    out.append(call("submit lowercase code", "POST", "/v1/inbox/submit",
                    body={"pair_code": code.lower(), "url": "https://example.com", "source": "share"}))
    for url in ["http://169.254.169.254/latest/meta-data/", "file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/",
                "http://localhost:8080/", "http://10.0.0.5/", "not a url", "", "ftp://example.com/x", 42,
                "http://2130706433/", "https://" + "a" * 3000 + ".com/"]:
        out.append(call(f"submit bad url {str(url)[:40]!r}", "POST", "/v1/inbox/submit", body={"pair_code": code, "url": url}))
    out.append(call("submit note too long", "POST", "/v1/inbox/submit",
                    body={"pair_code": code, "url": "https://www.tiktok.com/t/abc/", "note": "x" * 5000}))
    out.append(call("submit missing fields", "POST", "/v1/inbox/submit", body={}))
    out.append(call("submit unknown code", "POST", "/v1/inbox/submit", body={"pair_code": "NOPE-0000", "url": "https://www.tiktok.com/"}))
    out.append(call("poll other device", "GET", poll,
                    headers={"x-device-id": other["device_id"], "x-device-secret": other["device_secret"]}))
    out.append(call("poll wrong secret", "GET", poll, headers={"x-device-id": pair["device_id"], "x-device-secret": "nope"}))
    out.append(call("poll no creds", "GET", poll))
    out.append(call("poll missing pair_code", "GET", "/v1/inbox/poll", headers=cred))
    out.append(call("poll long pair_code", "GET", "/v1/inbox/poll?pair_code=" + "A" * 5000, headers=cred))
    out.append(call("poll delivers", "GET", poll, headers=cred))
    out.append(call("poll again", "GET", poll, headers=cred))
    out.append(call("poll unknown pairing", "GET", "/v1/inbox/poll?pair_code=NOPE-0000", headers=cred))
    for sid in ["111", "111", " 222 ", "a" * 100, "id with spaces", "drop;table", "", "ünï-cødé_1"]:
        out.append(call(f"save {sid[:20]!r}", "POST", "/v1/events/save", body={"sound_id": sid, "title": "T" if sid == "111" else ""}))
    out.append(call("save missing", "POST", "/v1/events/save", body={"platform": "tiktok"}))
    out.append(call("save long platform", "POST", "/v1/events/save", body={"sound_id": "1", "platform": "p" * 33}))
    for q in ["", "?limit=1", "?window=24h", "?window=BOGUS", "?limit=0", "?limit=101", "?limit=abc", "?limit=5.0",
              "?window=" + "w" * 17]:
        out.append(call(f"leaderboard {q!r}", "GET", "/v1/leaderboard" + q))
    out.append(call("admin no token", "POST", "/v1/admin/forward", body={"source_code": "A", "target_code": "B"}))
    out.append(call("admin bad body no token", "POST", "/v1/admin/forward", body={"source_code": ""}))
    for origin in ["https://soundcache.io", "https://www.soundcache.io", "https://evil.com"]:
        out.append(call(f"CORS GET {origin}", "GET", "/v1/leaderboard", headers={"origin": origin}))
        out.append(call(f"CORS preflight {origin}", "OPTIONS", "/v1/leaderboard",
                        headers={"origin": origin, "access-control-request-method": "GET"}))
    out.append(call("CORS preflight POST", "OPTIONS", "/v1/inbox/submit",
                    headers={"origin": "https://soundcache.io", "access-control-request-method": "POST"}))
    out.append(call("trailing slash", "GET", "/v1/health/"))
    return out


def parity(base: str) -> int:
    ref = _scenario(FastAPISide())
    got = _scenario(HttpSide(base))
    failures = 0
    for (label, s1, b1, c1, h1), (_, s2, b2, c2, h2) in zip(ref, got):
        exp = _norm_detail(_mask(b1))
        act = _norm_detail(_mask(b2))
        loc1 = re.sub(r"^https?://[^/]+", "", h1.get("location", ""))
        loc2 = re.sub(r"^https?://[^/]+", "", h2.get("location", ""))
        ok = s1 == s2 and exp == act and c1 == c2 and loc1 == loc2
        if not ok:
            failures += 1
            print(f"  MISMATCH {label}:\n    fastapi {s1} {json.dumps(exp)[:400]} {c1} {loc1}\n    worker  {s2} {json.dumps(act)[:400]} {c2} {loc2}")
    print(f"  parity: {len(ref) - failures}/{len(ref)} requests identical (status, body, CORS)")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--parity", action="store_true", help="also diff every response against the FastAPI app")
    args = ap.parse_args()
    print(f"round trip against {args.base_url}")
    round_trip(args.base_url)
    if args.parity:
        print("parity against the FastAPI relay")
        if parity(args.base_url):
            return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
