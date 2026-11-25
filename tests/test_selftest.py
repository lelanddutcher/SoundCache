"""`Sound Cache --self-test` passes from a source checkout (CI also runs it frozen)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_self_test_passes_from_source(tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "sound_vault.app", "--self-test"],
        capture_output=True, text=True, timeout=180, env=env, check=False,
    )
    report = json.loads(proc.stdout)
    failed = {k: v.get("error") for k, v in report.items() if isinstance(v, dict) and not v.get("ok")}
    assert proc.returncode == 0 and report["ok"], failed
    assert abs(report["analysis"]["detail"]["bpm"] - 120) <= 2
    assert report["window"]["detail"]["view"] == "list"
