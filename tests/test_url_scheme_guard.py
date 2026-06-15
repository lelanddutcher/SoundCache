"""External-link opening is restricted to http/https.

Sound/video URLs originate from third-party page scraping + redirect-following,
so a malicious page could plant a file://, custom app:, or javascript: scheme
that QDesktopServices.openUrl would dispatch to the OS. Both web-URL openers
funnel through _open_web_url, which refuses non-web schemes.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import sound_vault.ui.desktop as desktop_module
from sound_vault.ui.desktop import SoundVaultWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/music/Hot-Hook-hot",
    "http://example.com/x",
    "HTTPS://Example.com",          # scheme is case-insensitive
    "  https://example.com/y  ",    # surrounding whitespace tolerated
])
def test_is_safe_web_url_accepts_http_and_https(url):
    assert SoundVaultWindow._is_safe_web_url(url) is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file://localhost/Users/me/.ssh/id_rsa",
    "javascript:alert(1)",
    "app://evil/launch",
    "soundcache://ingest?x=1",
    "smb://share/x",
    "//protocol-relative.example.com",
    "ftp://example.com/x",
    "",
    "not a url",
    None,
])
def test_is_safe_web_url_rejects_non_web(url):
    assert SoundVaultWindow._is_safe_web_url(url) is False


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")
    vault = tmp_path / "vault"
    (vault / "catalog").mkdir(parents=True)
    (vault / "catalog" / "sounds.jsonl").write_text("", encoding="utf-8")
    _app()
    return SoundVaultWindow(vault_root=vault)


def test_open_web_url_dispatches_https_but_refuses_other_schemes(tmp_path, monkeypatch):
    window = _window(tmp_path, monkeypatch)
    try:
        opened = []
        monkeypatch.setattr(desktop_module.QDesktopServices, "openUrl", lambda u: opened.append(u.toString()))

        # safe → dispatched
        assert window._open_web_url("https://www.tiktok.com/music/abc") is True
        assert opened == ["https://www.tiktok.com/music/abc"]

        # malicious schemes → refused, never reach the OS handler
        for bad in ("file:///etc/passwd", "app://evil", "javascript:alert(1)"):
            assert window._open_web_url(bad) is False
        assert opened == ["https://www.tiktok.com/music/abc"]  # unchanged
    finally:
        window.close()
