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
from sound_vault.vault.indexer import build_record


def _subprocess_runner(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=240, check=False)
    return result.returncode, result.stdout, result.stderr


def build_downloader(
    *,
    playwright_script: Path | str | None = None,
    playwright_state: Path | str | None = None,
    playwright_cwd: Path | str | None = None,
) -> CompositeDownloader:
    """yt-dlp primary; add the authenticated Playwright TikTok fallback when configured.

    Falls back to env vars so the capture can be enabled without code changes:
    SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT / SOUND_VAULT_TIKTOK_STATE / SOUND_VAULT_TIKTOK_CAPTURE_CWD.
    """
    playwright_script = playwright_script or os.getenv("SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT")
    playwright_state = playwright_state or os.getenv("SOUND_VAULT_TIKTOK_STATE")
    playwright_cwd = playwright_cwd or os.getenv("SOUND_VAULT_TIKTOK_CAPTURE_CWD")

    fallback = None
    if playwright_script and playwright_state:
        fallback = PlaywrightCaptureDownloader(
            node_script=Path(playwright_script),
            storage_state=Path(playwright_state),
            runner=_subprocess_runner,
            project_cwd=Path(playwright_cwd) if playwright_cwd else None,
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


def build_ingest_service(
    *,
    vault_root: Path,
    db: IndexDatabase | None = None,
    index_path: Path | None = None,
    playwright_script: Path | str | None = None,
    playwright_state: Path | str | None = None,
    playwright_cwd: Path | str | None = None,
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
    return IngestService(vault_root=vault_root, downloader=downloader, index_updater=index_updater)
