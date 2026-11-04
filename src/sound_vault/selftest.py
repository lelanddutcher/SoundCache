"""`Sound Cache --self-test`: prove a build works without a person clicking around.

Runs inside whatever is executing (a source checkout, or the frozen .app in CI) and
checks what a release needs:

- the GUI stack imports (Qt widgets + multimedia), plus numpy, yt-dlp, faster-whisper
  and certifi's CA bundle;
- in a frozen app: the bundled node / ffmpeg / ffprobe exist and run, and the bundled
  Chromium is where Playwright will look;
- waveform + BPM analysis decodes a synthetic 120 BPM click track through ffmpeg;
- the main window builds, shows and closes on a throwaway vault (offscreen).

Prints one JSON report and exits 0 only if every check passed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path
from typing import Any, Callable


def _check(report: dict[str, Any], name: str, fn: Callable[[], Any]) -> None:
    started = time.monotonic()
    try:
        detail = fn()
        report[name] = {"ok": True, "detail": detail, "seconds": round(time.monotonic() - started, 2)}
    except Exception as exc:  # noqa: BLE001 - the report is the point
        report[name] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
        }


def _imports() -> dict[str, str]:
    import certifi
    import numpy
    import yt_dlp.version
    from PySide6 import QtCore, QtMultimedia, QtWidgets  # noqa: F401

    out = {
        "qt": QtCore.qVersion(),
        "numpy": numpy.__version__,
        "yt_dlp": yt_dlp.version.__version__,
        "certifi": certifi.__version__,
    }
    if not Path(certifi.where()).exists():
        raise RuntimeError(f"certifi CA bundle missing at {certifi.where()}")
    try:
        import faster_whisper

        out["faster_whisper"] = getattr(faster_whisper, "__version__", "present")
    except ImportError:
        out["faster_whisper"] = "not installed"
    return out


def _bundled_tools() -> dict[str, str]:
    from sound_vault.ingest.factory import bundled_bin_dir, bundled_browsers_dir, ensure_media_tools_on_path

    ensure_media_tools_on_path()
    frozen = bool(getattr(sys, "frozen", False))
    out: dict[str, str] = {"frozen": str(frozen)}
    bin_dir = bundled_bin_dir()
    if frozen and bin_dir is None:
        raise RuntimeError("frozen app has no bundled bin/ (node, ffmpeg, ffprobe)")
    for tool, args in (("node", ["--version"]), ("ffmpeg", ["-version"]), ("ffprobe", ["-version"])):
        exe = Path(bin_dir) / tool if bin_dir else None
        if exe is not None and not exe.exists():
            raise RuntimeError(f"bundled {tool} missing at {exe}")
        cmd = [str(exe) if exe is not None else tool, *args]
        try:
            first = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True).stdout.splitlines()[0]
        except FileNotFoundError:
            if frozen:
                raise
            first = "not on PATH"
        out[tool] = first[:80]
    browsers = bundled_browsers_dir()
    if frozen:
        if browsers is None:
            raise RuntimeError("frozen app has no bundled Chromium (run packaging/add_browser.sh)")
        exes = list(Path(browsers).glob("chromium-*/chrome-mac-arm64/Google Chrome for Testing.app"))
        if not exes:
            raise RuntimeError(f"no Chrome for Testing.app under {browsers}")
        out["chromium"] = str(exes[0].relative_to(browsers))
    return out


def _analysis() -> dict[str, Any]:
    import numpy as np

    from sound_vault.vault.analysis import analyze_audio

    rate, bpm, seconds = 16_000, 120.0, 10.0
    n = int(rate * seconds)
    out = np.zeros(n)
    i = np.arange(int(0.06 * rate))
    hit = np.exp(-i / (0.012 * rate)) * np.sin(2 * np.pi * 90 * i / rate)
    for t in np.arange(0, seconds, 60 / bpm):
        s = int(t * rate)
        e = min(n, s + hit.size)
        out[s:e] += hit[: e - s]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "click.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(out, -1, 1) * 30000).astype("<i2").tobytes())
        result = analyze_audio(path)
    if result.bpm is None or abs(result.bpm - bpm) > 2:
        raise RuntimeError(f"expected ~{bpm} BPM, got {result.bpm} (confidence {result.bpm_confidence})")
    return {"bpm": result.bpm, "peaks": len(result.peaks)}


def _window() -> dict[str, Any]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for flag in (
        "SOUND_VAULT_DISABLE_AUTO_INDEX", "SOUND_VAULT_DISABLE_RELAY_POLL", "SOUND_VAULT_DISABLE_ONBOARDING",
        "SOUND_VAULT_DISABLE_TIKTOK_PROMPT", "SOUND_VAULT_DISABLE_UPDATE_CHECK", "SOUND_VAULT_DISABLE_ANALYSIS",
        "SOUND_VAULT_DISABLE_TRANSCRIBE", "SOUND_VAULT_DISABLE_AUDIO",
    ):
        os.environ.setdefault(flag, "1")
    from PySide6.QtWidgets import QApplication

    from sound_vault.ui import desktop

    app = QApplication.instance() or QApplication([])
    desktop._load_brand_fonts()
    with tempfile.TemporaryDirectory() as tmp:
        window = desktop.SoundVaultWindow(vault_root=Path(tmp) / "vault")
        window.resize(1280, 800)
        window.show()
        app.processEvents()
        detail = {
            "view": "list" if window.library_stack.currentWidget() is window.track_list else "table",
            "title": window.windowTitle(),
        }
        window.close()
        app.processEvents()
    return detail


def run_self_test() -> int:
    # Keep the user's real settings and app data untouched.
    scratch = Path(tempfile.mkdtemp(prefix="sound-cache-selftest-"))
    os.environ["SOUND_VAULT_CONFIG_DIR"] = str(scratch / "config")
    os.environ["SOUND_VAULT_DATA_DIR"] = str(scratch / "data")
    from sound_vault import __version__

    report: dict[str, Any] = {"version": __version__, "python": sys.version.split()[0]}
    _check(report, "imports", _imports)
    _check(report, "bundled_tools", _bundled_tools)
    _check(report, "analysis", _analysis)
    _check(report, "window", _window)
    ok = all(v.get("ok") for v in report.values() if isinstance(v, dict))
    report["ok"] = ok
    print(json.dumps(report, indent=2, default=str))
    sys.stdout.flush()
    return 0 if ok else 1
