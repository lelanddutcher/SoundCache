from __future__ import annotations

from pathlib import Path

from sound_vault.settings import AppSettings


def _settings(tmp_path) -> AppSettings:
    return AppSettings(tmp_path / "settings.json")


def test_vault_root_is_set_distinguishes_fresh_install(tmp_path):
    s = _settings(tmp_path)
    assert s.vault_root_is_set() is False  # brand-new: falls back to the default
    s.set_vault_root(tmp_path / "vault")
    assert s.vault_root_is_set() is True


def test_onboarding_flag_round_trips(tmp_path):
    s = _settings(tmp_path)
    assert s.onboarding_complete() is False
    s.set_onboarding_complete()
    assert AppSettings(tmp_path / "settings.json").onboarding_complete() is True


def test_recent_vaults_dedupe_and_cap(tmp_path):
    s = _settings(tmp_path)
    for i in range(10):
        s.add_recent_vault(tmp_path / f"v{i}")
    s.add_recent_vault(tmp_path / "v0")  # re-add moves it to front + dedupes
    recents = s.recent_vaults()
    assert len(recents) == 8  # capped
    assert recents[0] == str((tmp_path / "v0").expanduser())
    assert len(set(recents)) == len(recents)  # no duplicates
