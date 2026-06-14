from __future__ import annotations

import ssl

import sound_vault.relay.client as client_module
from sound_vault.net import ssl_context
from sound_vault.relay.client import RelayClient


def test_ssl_context_uses_certifi_bundle():
    ctx = ssl_context()
    # certifi is a base/gui dependency, so a context must be returned.
    assert isinstance(ctx, ssl.SSLContext)
    # cached: same object every call.
    assert ssl_context() is ctx


def test_relay_poll_passes_ssl_context_to_urlopen(monkeypatch):
    """Regression: relay poll over HTTPS must use a CA-bearing SSL context,
    otherwise framework Python fails every call with CERTIFICATE_VERIFY_FAILED."""
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"items": []}'

    def fake_urlopen(request, timeout=None, context=None):
        seen["context"] = context
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)

    client = RelayClient(
        base_url="https://api.soundcache.io",
        device_id="dev",
        device_secret="secret",
        pair_code="ABC-1",
    )
    client.poll()

    assert isinstance(seen["context"], ssl.SSLContext), "poll must pass an SSL context"
    assert seen["context"] is ssl_context()
