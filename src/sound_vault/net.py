"""Shared networking helpers.

macOS framework Python (and fresh venvs built from it) ship without a usable CA
bundle — ``ssl.get_default_verify_paths().cafile`` is ``None`` — so the stdlib
default SSL context raises ``CERTIFICATE_VERIFY_FAILED: unable to get local
issuer certificate`` on *every* HTTPS request. That silently breaks the relay
pairing + poll calls (and any other outbound HTTPS). certifi ships the Mozilla
root bundle; routing every ``urlopen`` through this context fixes it.
"""
from __future__ import annotations

import functools
import ssl


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext | None:
    """An SSL context backed by certifi's CA bundle.

    Returns ``None`` if certifi is unavailable so callers fall back to the
    stdlib default context. Cached: building the context parses the bundle.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - any certifi/ssl problem falls back to default
        return None
