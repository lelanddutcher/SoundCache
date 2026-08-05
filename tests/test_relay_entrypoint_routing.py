"""Guards against the 2026-08-05 relay outage: every route 404'd because the host
rewrote requests to the serverless entrypoint path.

The relay deploys as a single Vercel function whose entrypoint is ``api/index.py``, and
``vercel.json`` used to carry a catch-all rewrite ``/(.*) -> /api/index``. Vercel then
changed internal-rewrite semantics ("Internal rewrites in backend framework projects now
route requests using the rewritten destination path"), so FastAPI began receiving the
literal path ``/api/index`` for every request. Result: submit, poll, health and the
leaderboard all returned ``{"detail":"Not Found"}`` with no code change on our side.

Two defences, both asserted here:
  1. ``vercel.json`` must not reintroduce a catch-all rewrite (the actual fix — when the
     host REPLACES the path, the original is gone and no middleware can recover it).
  2. If a host merely PREFIXES the entrypoint path, the app strips it and still routes.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from sound_vault.relay.server import app

client = TestClient(app)


def test_vercel_json_has_no_catch_all_rewrite():
    """A catch-all rewrite to the entrypoint takes the whole relay down on modern Vercel."""
    config = json.loads((Path(__file__).resolve().parent.parent / "vercel.json").read_text(encoding="utf-8"))
    for rewrite in config.get("rewrites") or []:
        source = str(rewrite.get("source", ""))
        destination = str(rewrite.get("destination", ""))
        assert not (source.startswith("/(") and "api/index" in destination), (
            f"catch-all rewrite {source!r} -> {destination!r} makes FastAPI see the entrypoint "
            "path for every request and 404 the entire relay"
        )


def test_real_routes_still_resolve_normally():
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/health").json() == {"status": "ok"}
    assert client.get("/openapi.json").status_code == 200


def test_entrypoint_prefixed_paths_are_normalized():
    """A host that prefixes (rather than replaces) the path must still reach the route."""
    for prefix in ("/api/index", "/api/index.py"):
        response = client.get(f"{prefix}/v1/health")
        assert response.status_code == 200, f"{prefix}/v1/health should route to /v1/health"
        assert response.json() == {"status": "ok"}


def test_poll_route_exists_and_validates_instead_of_404ing():
    """The desktop app polls /v1/inbox/poll. Missing credentials must be a validation
    error (422), never a 404 — a 404 here is the signature of the routing outage."""
    response = client.get("/v1/inbox/poll")
    assert response.status_code == 422, f"expected validation error, got {response.status_code}"


def test_unrelated_api_paths_are_left_alone():
    """Only the exact entrypoint prefixes are stripped, so a future /api/... route is safe."""
    assert client.get("/api/something").status_code == 404
    assert client.get("/apifoo").status_code == 404
