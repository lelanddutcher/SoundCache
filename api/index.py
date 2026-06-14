"""Vercel Python entrypoint for the Sound Vault relay.

Vercel serves the exported ASGI ``app``. The package lives under ``src/`` (bundled
via vercel.json includeFiles), so put it on the path before importing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sound_vault.relay.server import app  # noqa: E402

__all__ = ["app"]
