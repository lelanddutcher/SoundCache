"""Tests for the lightweight update check (pure logic, injected fetch)."""
from __future__ import annotations

from sound_vault.update_check import UpdateInfo, check_for_update, is_newer


def test_is_newer_semantics():
    assert is_newer("0.4.0", "0.3.0")
    assert is_newer("1.0.0", "0.9.9")
    assert is_newer("0.3.1", "0.3.0")
    assert not is_newer("0.3.0", "0.3.0")
    assert not is_newer("0.2.9", "0.3.0")
    assert is_newer("v0.4.0", "0.3.0")  # leading v tolerated
    assert is_newer("0.4.0-beta", "0.3.0")  # pre-release suffix tolerated
    assert not is_newer("0.3", "0.3.0")  # 0.3 == 0.3.0


def test_check_for_update_returns_info_when_newer():
    info = check_for_update(
        "0.3.0",
        fetch=lambda _url: {"version": "0.4.0", "url": "https://soundcache.io/#download", "notes": "shiny"},
    )
    assert isinstance(info, UpdateInfo)
    assert info.version == "0.4.0"
    assert info.url == "https://soundcache.io/#download"
    assert info.notes == "shiny"


def test_check_for_update_none_when_current_or_older():
    assert check_for_update("0.4.0", fetch=lambda _u: {"version": "0.4.0"}) is None
    assert check_for_update("0.5.0", fetch=lambda _u: {"version": "0.4.0"}) is None


def test_check_for_update_swallows_errors():
    def boom(_url):
        raise OSError("network down")

    assert check_for_update("0.3.0", fetch=boom) is None
    assert check_for_update("0.3.0", fetch=lambda _u: "not a dict") is None
    assert check_for_update("0.3.0", fetch=lambda _u: {}) is None  # no version field


def test_check_for_update_rejects_non_https_download_url():
    info = check_for_update(
        "0.3.0",
        fetch=lambda _u: {"version": "0.4.0", "url": "javascript:alert(1)"},
    )
    assert info is not None
    assert info.url == "https://soundcache.io/#download"  # unsafe url replaced with fallback
