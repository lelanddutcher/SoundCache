from __future__ import annotations

import os
import plistlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

import sound_vault.ui.desktop as desktop_module
from sound_vault.ui.desktop import PairPhoneDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_pair_phone_dialog_shows_values_and_qr():
    _app()
    dialog = PairPhoneDialog(relay_url="https://api.soundcache.io", pair_code="river-7421")
    try:
        # pair code surfaced upper-cased; relay verbatim
        assert dialog.code_field.text() == "RIVER-7421"
        assert dialog.relay_field.text() == "https://api.soundcache.io"
        # qrcode is a gui dependency, so the QR should render to a pixmap
        assert not dialog.qr_label.pixmap().isNull()
    finally:
        dialog.deleteLater()


def test_pair_phone_dialog_exports_prefilled_plist(tmp_path, monkeypatch):
    _app()
    dialog = PairPhoneDialog(relay_url="https://api.soundcache.io", pair_code="ABC-1")
    out = tmp_path / "Save to Sound Cache.unsigned.plist"
    monkeypatch.setattr(
        desktop_module.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "Shortcut plist (*.plist)")),
    )
    try:
        dialog.export_shortcut()
        assert out.exists()
        parsed = plistlib.loads(out.read_bytes())
        post = next(
            a for a in parsed["WFWorkflowActions"]
            if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        )
        body = post["WFWorkflowActionParameters"]
        assert body["WFURL"] == "https://api.soundcache.io/v1/inbox/submit"
        items = {
            it["WFKey"]["Value"]["string"]: it for it in body["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
        }
        assert items["pair_code"]["WFValue"]["Value"]["string"] == "ABC-1"
    finally:
        dialog.deleteLater()


def test_pair_phone_dialog_saves_qr_png(tmp_path, monkeypatch):
    _app()
    dialog = PairPhoneDialog(relay_url="https://r.example", pair_code="ZZ-9")
    out = tmp_path / "pairing.png"
    monkeypatch.setattr(
        desktop_module.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "PNG image (*.png)")),
    )
    try:
        dialog.save_qr()
        assert out.exists() and out.stat().st_size > 0
    finally:
        dialog.deleteLater()


def test_settings_dialog_never_pairs_against_the_dead_vercel_relay(tmp_path, monkeypatch):
    """A legacy Vercel relay typed (or pasted) into Settings is replaced by the current
    relay before it is saved, used to create a pairing, or put in the Pair iPhone QR."""
    from sound_vault.settings import AppSettings
    from sound_vault.ui.desktop import SettingsDialog

    _app()
    # The dialog probes the local ASR model cache; keep that off disk/network here.
    import sound_vault.workers.transcription as transcription

    monkeypatch.setattr(transcription, "asr_model_is_cached", lambda *a, **k: True)
    settings = AppSettings(tmp_path / "settings.json")
    dialog = SettingsDialog(settings=settings)
    monkeypatch.setattr(dialog, "parent", lambda: None)
    dialog.relay_url.setText("https://sound-vault-relay.vercel.app/")
    dialog.pair_code.setText("river-7421")

    opened: dict[str, str] = {}

    class _FakePair:
        def __init__(self, *, relay_url, pair_code, parent=None):
            opened["relay_url"] = relay_url

        def exec(self):
            return 0

    # Patch the globals the method really runs with: other tests reload the desktop
    # module, so the module object imported at the top of this file can be stale.
    monkeypatch.setitem(SettingsDialog.open_pair_phone.__globals__, "PairPhoneDialog", _FakePair)
    dialog.open_pair_phone()

    assert opened["relay_url"] == "https://api.soundcache.io"
    assert dialog.relay_url.text() == "https://api.soundcache.io"
    assert AppSettings(tmp_path / "settings.json").relay_base_url() == "https://api.soundcache.io"
