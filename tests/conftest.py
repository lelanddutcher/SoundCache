"""Keep the test suite hermetic: point app-data (index default, the shortcut
inbox, events log, saved TikTok session) at a throwaway per-session directory so
tests never read from — or write into — the real ~/Library app-data, and stay
deterministic across runs."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_app_data(tmp_path_factory):
    base = tmp_path_factory.mktemp("sound-cache-appdata")
    # setdefault so an explicit override in the environment still wins.
    os.environ.setdefault("SOUND_VAULT_DATA_DIR", str(base / "data"))
    os.environ.setdefault("SOUND_VAULT_CONFIG_DIR", str(base / "config"))
    # Never pop the first-run setup wizard during headless GUI construction tests.
    os.environ.setdefault("SOUND_VAULT_DISABLE_ONBOARDING", "1")
    # Never let the update check run: it fetches soundcache.io/latest.json over the real
    # network and, when a NEWER version is published, opens a modal "Update available"
    # dialog. Under pytest nobody clicks it, so box.exec() blocks forever and each window
    # a test builds stacks another nested modal loop — the whole suite hangs. This bit for
    # real the moment 0.4.0 shipped (manifest 0.4.0 vs the editable install's 0.3.3
    # metadata), and would hit again after every release.
    os.environ.setdefault("SOUND_VAULT_DISABLE_UPDATE_CHECK", "1")
    yield


@pytest.fixture(scope="session", autouse=True)
def _tear_down_qt_before_exit():
    """Destroy every leftover window while the QApplication still exists.

    The GUI tests leave windows, dialogs and media players alive. Without this, the
    interpreter destroys them during shutdown in whatever order its garbage collector
    picks, after Qt has begun tearing down. With PySide6 6.12 that order sometimes
    deletes a shared QObject twice ("QObject: shared QObject was deleted directly")
    and the process segfaults after every test has passed, so the run exits 139."""
    yield
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        return  # no GUI test ran in this session
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    # deleteLater, not close(): close() runs each window's closeEvent, which reaches into
    # the fake workers tests swapped in. Deletion is all the teardown needs.
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
