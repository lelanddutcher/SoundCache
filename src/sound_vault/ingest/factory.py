"""Wire a ready-to-use IngestService for the GUI and CLI worker.

Keeps the dependency wiring (yt-dlp + optional Playwright fallback, cache upsert)
in one place so the desktop app, the headless worker, and tests share it.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ingest.download import CompositeDownloader, PlaywrightCaptureDownloader, YtDlpDownloader
from sound_vault.ingest.package import PackagedSound
from sound_vault.ingest.service import IndexUpdater, IngestService
from sound_vault.settings import user_data_dir
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


def _subprocess_runner(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=240, check=False)
    return result.returncode, result.stdout, result.stderr


def _repo_root() -> Path:
    # src/sound_vault/ingest/factory.py -> parents[3] is the repo root, which
    # holds scripts/ and node_modules/ (so `require('playwright')` resolves).
    return Path(__file__).resolve().parents[3]


def _default_capture_script() -> Path | None:
    candidate = _repo_root() / "scripts" / "capture_tiktok_audio.cjs"
    return candidate if candidate.exists() else None


def _default_storage_state() -> Path | None:
    """The TikTok auth state the capture browser logs in with. Kept in the app
    data dir (out of source control) so it isn't tied to the NAS mount; override
    with SOUND_VAULT_TIKTOK_STATE."""
    candidate = user_data_dir() / "tiktok.storageState.json"
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


def build_transcriber(settings=None):
    """Build a per-sound transcriber from settings, or None if unavailable.

    Local faster-whisper is the only locally-runnable engine (cloud needs the
    openai package + an API key), so use it when installed. Disabled by the
    SOUND_VAULT_DISABLE_TRANSCRIBE env var (set in tests/CI)."""
    if os.getenv("SOUND_VAULT_DISABLE_TRANSCRIBE"):
        return None
    from sound_vault.workers.transcription import LocalASRConfig, faster_whisper_transcriber

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
    return faster_whisper_transcriber(
        LocalASRConfig(
            model=str(cfg.get("local_model") or "base"),
            model_cache_dir=(str(cfg.get("model_cache_dir") or "") or None),
        )
    )


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
    return IngestService(
        vault_root=vault_root, downloader=downloader, index_updater=index_updater, transcriber=transcriber
    )
