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
