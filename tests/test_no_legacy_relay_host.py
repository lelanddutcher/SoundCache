"""Guard: nothing that ships or builds the app may point at the deleted Vercel relay.

The public relay moved from sound-vault-relay.vercel.app to https://api.soundcache.io
(a Cloudflare Worker). A build that still carried the old host paired phones against a
relay that no longer exists. settings.py may name the suffix, because it is what
recognizes and migrates old saved URLs; everything else must not.
"""
from __future__ import annotations

import re
from pathlib import Path

from sound_vault.settings import DEFAULT_RELAY_BASE_URL

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("src", "scripts", "packaging", "relay-worker/src", "relay-worker/wrangler.jsonc", "Dockerfile.relay")
ALLOWED = {ROOT / "src/sound_vault/settings.py"}
VERCEL_HOST = re.compile(r"[a-z0-9-]*\.?vercel\.app", re.IGNORECASE)


def _files():
    for entry in SCANNED:
        path = ROOT / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix not in {".pyc", ".png", ".icns", ".ttf", ".otf"}:
                    yield f


def test_no_shipped_file_names_a_vercel_relay_host():
    hits = []
    for f in _files():
        if f in ALLOWED:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if VERCEL_HOST.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "old Vercel relay host found:\n" + "\n".join(hits)


def test_default_relay_is_the_public_domain():
    assert DEFAULT_RELAY_BASE_URL == "https://api.soundcache.io"
