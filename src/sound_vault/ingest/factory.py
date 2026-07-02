"""Wire a ready-to-use IngestService for the GUI and CLI worker.

Keeps the dependency wiring (yt-dlp + optional Playwright fallback, cache upsert)
in one place so the desktop app, the headless worker, and tests share it.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ingest.download import CompositeDownloader, PlaywrightCaptureDownloader, YtDlpDownloader
from sound_vault.ingest.package import PackagedSound
from sound_vault.ingest.service import IndexUpdater, IngestService
from sound_vault.vault.indexer import build_record

# Where Homebrew / MacPorts / /usr/local put node + ffmpeg. A GUI app launched
# from Finder or a launchd agent inherits a minimal PATH (often just
# /usr/bin:/bin), so node (the TikTok Playwright capture) and ffmpeg (yt-dlp's
# audio post-processor) silently fail to resolve — which is exactly why some
# inbox items failed with "ffprobe and ffmpeg not found". Augment PATH before any
# ingest so both the in-process yt-dlp ffmpeg call and the node subprocess find them.
_EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")


def ensure_media_tools_on_path() -> None:
    """Idempotently add common bin dirs to PATH so node/ffmpeg resolve under a
    Finder/launchd-launched GUI (which gets a stripped PATH)."""
    parts = os.environ.get("PATH", "").split(os.pathsep) if os.environ.get("PATH") else []
    added = [d for d in _EXTRA_BIN_DIRS if d not in parts and os.path.isdir(d)]
    if added:
        os.environ["PATH"] = os.pathsep.join(parts + added)


def _subprocess_runner(cmd, cwd=None, cancel=None):
    """Run a capture subprocess, killable mid-flight.

    Polls an optional ``cancel()`` predicate (e.g. the import worker's
    ``QThread.isInterruptionRequested``) so quitting during a long bulk import
    terminates the node/Playwright child promptly instead of blocking the worker
    thread for up to the full timeout. A thread still stuck in a subprocess at app
    teardown is exactly what left a QThread running and crashed Qt with SIGABRT.
    Keeps the original 240s ceiling as a hard backstop.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 240
    while True:
        try:
            out, err = proc.communicate(timeout=0.25)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            pass
        cancelled = cancel is not None and cancel()
        if cancelled or time.monotonic() > deadline:
            proc.kill()
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - kill didn't take
                out, err = "", ""
            reason = "cancelled" if cancelled else "timed out after 240s"
            return (proc.returncode if proc.returncode is not None else -1), out, f"capture {reason}"


def _repo_root() -> Path:
    # Where scripts/ + node_modules/ live (so `require('playwright')` resolves).
    # In a PyInstaller bundle they're collected next to the app under
    # sys._MEIPASS; in a dev checkout it's the repo root (parents[3]).
    # Overridable with SOUND_VAULT_NODE_ROOT.
    override = os.getenv("SOUND_VAULT_NODE_ROOT")
    if override:
        return Path(override).expanduser()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and getattr(sys, "frozen", False):
        return Path(meipass)
    return Path(__file__).resolve().parents[3]


def _default_capture_script() -> Path | None:
    candidate = _repo_root() / "scripts" / "capture_tiktok_audio.cjs"
    return candidate if candidate.exists() else None


def _default_storage_state() -> Path | None:
    """The TikTok auth state the capture browser logs in with — written by the
    in-app "Connect TikTok" onboarding (see ingest.tiktok_auth). Kept in the app
    data dir (out of source control); override with SOUND_VAULT_TIKTOK_STATE."""
    from sound_vault.ingest import tiktok_auth

    candidate = tiktok_auth.state_path()
    return candidate if candidate.exists() else None


def build_downloader(
    *,
    playwright_script: Path | str | None = None,
    playwright_state: Path | str | None = None,
    playwright_cwd: Path | str | None = None,
) -> CompositeDownloader:
    """yt-dlp primary; add the authenticated Playwright TikTok fallback.

    yt-dlp cannot fetch a TikTok ``/music/`` sound's own audio (its sound
    extractor only enumerates videos and is upstream-broken), so the Playwright
    capture is the *only* path that works for shared sounds. Resolution order for
    each input: explicit arg -> env var
    (SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT / _STATE / _CAPTURE_CWD) -> bundled default
    (the in-repo capture script + the app-data storageState). The fallback is only
    attached when both a script and an auth-state file actually resolve, so it
    stays a clean no-op on machines without the auth state.
    """
    ensure_media_tools_on_path()
    script = playwright_script or os.getenv("SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT") or _default_capture_script()
    state = playwright_state or os.getenv("SOUND_VAULT_TIKTOK_STATE") or _default_storage_state()
    cwd = playwright_cwd or os.getenv("SOUND_VAULT_TIKTOK_CAPTURE_CWD") or _repo_root()

    fallback = None
    if script and state:
        fallback = PlaywrightCaptureDownloader(
            node_script=Path(script),
            storage_state=Path(state),
            runner=_subprocess_runner,
            project_cwd=Path(cwd) if cwd else None,
        )
    return CompositeDownloader(
        primary=YtDlpDownloader(),
        fallback=fallback,
        should_fallback=lambda url, result, **kwargs: kwargs.get("platform") == "tiktok",
    )


def make_index_updater(vault_root: Path, db: IndexDatabase) -> IndexUpdater:
    def update(packaged: PackagedSound) -> None:
        record = build_record(Path(vault_root), packaged.metadata)
        if record is not None:
            db.upsert(record)

    return update


_AUTO = object()


def running_translated() -> bool:
    """True if this process is running under Rosetta (x86_64) on Apple-Silicon
    hardware. In that state the arm64-only native ASR wheels (mlx, ctranslate2/av)
    can't load, so local transcription has no backend — the launcher forces arm64 to
    avoid it, and this lets us report it clearly if it ever happens anyway."""
    import platform

    if platform.system() != "Darwin":
        return False
    try:
        out = subprocess.run(
            ["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True, timeout=3, check=False
        )
        return out.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def build_transcriber(settings=None):
    """Build a per-sound transcriber, GPU-accelerated when possible, or None.

    Backend selection (override with SOUND_VAULT_ASR_BACKEND=mlx|faster-whisper):
    'auto' (default) prefers MLX — Apple's on-device framework that runs whisper on
    the Apple-Silicon GPU — when available, otherwise faster-whisper (which itself
    auto-uses CUDA on an NVIDIA GPU, else CPU). Disabled by SOUND_VAULT_DISABLE_TRANSCRIBE."""
    from sound_vault.diagnostics import write_event

    if os.getenv("SOUND_VAULT_DISABLE_TRANSCRIBE"):
        write_event("asr.build_transcriber", result="none", reason="disabled_env")
        return None
    from sound_vault.workers.transcription import (
        LocalASRConfig,
        faster_whisper_transcriber,
        mlx_whisper_transcriber,
    )

    cfg = {}
    if settings is None:
        try:
            from sound_vault.settings import AppSettings

            settings = AppSettings()
        except Exception:  # noqa: BLE001
            settings = None
    if settings is not None:
        try:
            cfg = settings.transcription_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    model = str(cfg.get("local_model") or "base")
    asr_cfg = LocalASRConfig(model=model, model_cache_dir=(str(cfg.get("model_cache_dir") or "") or None))

    backend = (os.getenv("SOUND_VAULT_ASR_BACKEND") or "auto").strip().lower()
    if backend in ("faster-whisper", "faster_whisper", "cpu", "cuda"):
        order = [("faster-whisper", faster_whisper_transcriber), ("mlx-whisper", mlx_whisper_transcriber)]
    else:  # auto / mlx → GPU-first (MLX on Apple Silicon), CPU/CUDA faster-whisper fallback
        order = [("mlx-whisper", mlx_whisper_transcriber), ("faster-whisper", faster_whisper_transcriber)]

    transcriber, chosen = None, ""
    for name, builder in order:
        built = builder(asr_cfg)
        if built is not None:
            transcriber, chosen = built, name
            break
    reason = ""
    if not transcriber:
        reason = "rosetta_x86_no_arm64_wheels" if running_translated() else "no_backend_available"
    write_event(
        "asr.build_transcriber", result="ok" if transcriber else "none",
        engine=chosen, backend=backend, model=model, reason=reason,
    )
    return transcriber


def build_ingest_service(
    *,
    vault_root: Path,
    db: IndexDatabase | None = None,
    index_path: Path | None = None,
    playwright_script: Path | str | None = None,
    playwright_state: Path | str | None = None,
    playwright_cwd: Path | str | None = None,
    transcriber=_AUTO,
) -> IngestService:
    vault_root = Path(vault_root)
    if db is None and index_path is not None:
        db = IndexDatabase(Path(index_path))
    index_updater = make_index_updater(vault_root, db) if db is not None else None
    downloader = build_downloader(
        playwright_script=playwright_script,
        playwright_state=playwright_state,
        playwright_cwd=playwright_cwd,
    )
    if transcriber is _AUTO:
        transcriber = build_transcriber()
    # Resolve TikTok video/photo shares to their underlying sound (via oEmbed) so a
    # submitted video link captures the clean /music/ sound, not the trimmed clip.
    import functools

    from sound_vault.ingest.resolve import resolve, tiktok_music_url_via_oembed

    resolve_source = functools.partial(resolve, music_resolver=tiktok_music_url_via_oembed)
    return IngestService(
        vault_root=vault_root,
        downloader=downloader,
        index_updater=index_updater,
        transcriber=transcriber,
        resolve_source=resolve_source,
    )
