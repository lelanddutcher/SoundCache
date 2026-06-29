"""Headless checks for the app menu bar, vault switching, and the onboarding wizard."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from sound_vault.ui.desktop import OnboardingDialog, SoundVaultWindow  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet_window(monkeypatch):
    # Per-test (auto-reverted) so these never leak into other GUI tests — notably the
    # offline-startup test, which needs auto-index ENABLED.
    for flag in (
        "SOUND_VAULT_DISABLE_AUTO_INDEX",
        "SOUND_VAULT_DISABLE_RELAY_POLL",
        "SOUND_VAULT_DISABLE_ONBOARDING",
        "SOUND_VAULT_DISABLE_TIKTOK_PROMPT",
    ):
        monkeypatch.setenv(flag, "1")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _menus(window) -> dict[str, list[str]]:
    return {
        a.menu().title(): [x.text() for x in a.menu().actions() if x.text()]
        for a in window.menuBar().actions()
        if a.menu()
    }


def test_menu_bar_has_expected_menus(app, tmp_path):
    window = SoundVaultWindow(vault_root=tmp_path / "vault")
    menus = _menus(window)
    assert set(menus) == {"&File", "&Edit", "&View", "&Vault", "&Help"}
    assert "New Vault…" in menus["&File"] and "Open Vault…" in menus["&File"]
    assert "Copy" in menus["&Edit"] and "Find / Search" in menus["&Edit"]
    assert "Library" in menus["&View"] and "Rebuild Index" in menus["&View"]
    assert "Connect TikTok…" in menus["&Vault"] and "Setup Wizard…" in menus["&Vault"]
    window.close()


def test_switch_to_vault_persists_and_rebinds(app, tmp_path):
    window = SoundVaultWindow(vault_root=tmp_path / "vault_a")
    target = tmp_path / "vault_b"
    window._switch_to_vault(target)
    assert window.vault_root == target.resolve() or str(window.vault_root).endswith("vault_b")
    assert window.settings.vault_root_is_set()
    assert str(target) in [str(Path(p)) for p in window.settings.recent_vaults()]
    window.close()


def test_onboarding_dialog_records_chosen_vault(app, tmp_path):
    chosen_target = tmp_path / "fresh_vault"
    dialog = OnboardingDialog(default_vault=tmp_path / "default", connect_tiktok=lambda: None)
    assert dialog.stack.count() == 4
    dialog._go_next()  # welcome -> vault page
    dialog.vault_edit.setText(str(chosen_target))
    dialog._go_next()  # records the choice, advances to tiktok page
    assert dialog.chosen_vault() == chosen_target
