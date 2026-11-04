"""Background waveform/BPM backfill for sounds imported before analysis existed."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from fixture_vault import build_fixture_vault  # noqa: E402

from sound_vault.ui.view_model import LibraryViewModel  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _vm(tmp_path, count=8):
    sounds = build_fixture_vault(tmp_path / "vault", count=count)
    vm = LibraryViewModel(vault_root=tmp_path / "vault", index_path=tmp_path / "index.sqlite3")
    vm.rebuild_index()
    return vm, sounds


def test_targets_are_sounds_with_audio_and_no_waveform(tmp_path):
    vm, sounds = _vm(tmp_path)
    targets = {mid for mid, _folder, _audio in vm.analysis_targets()}
    assert targets == {s.music_id for s in sounds if s.has_audio}


def test_analyze_one_updates_the_index_row_and_drops_it_from_targets(tmp_path):
    vm, sounds = _vm(tmp_path)
    sound = next(s for s in sounds if s.has_audio and s.bpm)
    assert vm.analyze_one(sound.music_id, sound.folder, sound.folder / "audio.wav")

    record = vm.db.get(sound.music_id)
    assert record.waveform_path == sound.folder / "waveform.json"
    assert abs(record.bpm - sound.bpm) <= 2
    assert sound.music_id not in {mid for mid, *_ in vm.analysis_targets()}
    # Already analysed: a second call is a no-op.
    assert vm.analyze_one(sound.music_id, sound.folder, sound.folder / "audio.wav") is False


def test_analyze_one_survives_undecodable_audio(tmp_path):
    vm, sounds = _vm(tmp_path)
    sound = next(s for s in sounds if s.has_audio)
    (sound.folder / "audio.wav").write_bytes(b"garbage")
    assert vm.analyze_one(sound.music_id, sound.folder, sound.folder / "audio.wav") is False
    assert vm.db.get(sound.music_id).waveform_path is None


def test_worker_drains_targets_and_reports_progress(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from sound_vault.ui.desktop import _AnalysisWorker

    QApplication.instance() or QApplication([])
    vm, sounds = _vm(tmp_path, count=6)
    targets = vm.analysis_targets()
    worker = _AnalysisWorker(vm, targets)
    progress, analyzed = [], []
    worker.progress.connect(lambda d, t: progress.append((d, t)))
    worker.analyzed.connect(analyzed.append)
    worker.run()  # synchronously, on this thread
    assert progress[-1] == (len(targets), len(targets))
    assert sorted(analyzed) == sorted(mid for mid, *_ in targets)
    assert vm.analysis_targets() == []


def test_worker_stops_between_items_when_interrupted(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from sound_vault.ui.desktop import _AnalysisWorker

    QApplication.instance() or QApplication([])
    vm, _ = _vm(tmp_path, count=6)
    worker = _AnalysisWorker(vm, vm.analysis_targets())
    worker.isInterruptionRequested = lambda: True  # as if Quit was pressed
    analyzed = []
    worker.analyzed.connect(analyzed.append)
    worker.run()
    assert analyzed == []
