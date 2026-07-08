import sys
import time
from pathlib import Path

from sound_vault.ingest.download import (
    CompositeDownloader,
    DownloadResult,
    PlaywrightCaptureDownloader,
    YtDlpDownloader,
)
from sound_vault.ingest.factory import _subprocess_runner


def test_subprocess_runner_cancel_kills_long_process_fast():
    """A capture that's still running when cancel() flips must be killed promptly
    so the import worker thread can exit on quit (otherwise the QThread is
    destroyed mid-run and Qt aborts with SIGABRT)."""
    start = time.monotonic()
    rc, _out, err = _subprocess_runner(
        [sys.executable, "-c", "import time; time.sleep(30)"], cancel=lambda: True
    )
    elapsed = time.monotonic() - start
    assert elapsed < 5  # killed within a poll tick, not after the 30s sleep
    assert rc != 0
    assert "cancelled" in err


def test_subprocess_runner_runs_to_completion_when_not_cancelled():
    rc, out, _err = _subprocess_runner(
        [sys.executable, "-c", "print('done')"], cancel=lambda: False
    )
    assert rc == 0
    assert "done" in out


def _extract_writes_m4a(info):
    def _extract(url, opts):
        out = Path(opts["outtmpl"].replace("%(ext)s", "m4a"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00fake-audio-bytes")
        return info

    return _extract


def test_ytdlp_downloader_success(tmp_path):
    dl = YtDlpDownloader(
        extract=_extract_writes_m4a({"id": "vid123", "title": "Test Sound", "uploader": "Creator", "duration": 12.0}),
        probe_audio=lambda _p: True,  # bypass the real ffprobe check for the stub file
    )
    result = dl.download("https://x/y", dest_dir=tmp_path, basename="vid123")
    assert isinstance(result, DownloadResult)
    assert result.ok is True
    assert result.audio_path == tmp_path / "vid123.m4a"
    assert result.audio_path.exists()
    assert result.method == "yt-dlp"
    assert result.info["id"] == "vid123"
    assert result.info["title"] == "Test Sound"
    assert result.info["uploader"] == "Creator"


def test_ytdlp_downloader_extract_raises(tmp_path):
    def boom(url, opts):
        raise RuntimeError("network down")

    dl = YtDlpDownloader(extract=boom)
    result = dl.download("https://x/y", dest_dir=tmp_path, basename="v")
    assert result.ok is False
    assert "network down" in (result.error or "")


def test_ytdlp_downloader_no_file_produced(tmp_path):
    def extract_no_file(url, opts):
        return {"id": "v", "title": "t"}

    dl = YtDlpDownloader(extract=extract_no_file)
    result = dl.download("https://x/y", dest_dir=tmp_path, basename="v")
    assert result.ok is False
    assert "no audio" in (result.error or "").lower()


def test_ytdlp_downloader_invalid_audio_fails_so_fallback_can_run(tmp_path):
    # yt-dlp can write a file that EXISTS but isn't decodable audio (e.g. a broken
    # TikTok extractor). That must be reported as ok=False so the Composite fallback
    # engages, instead of handing junk to the packager (opaque ffmpeg exit 183).
    dl = YtDlpDownloader(
        extract=_extract_writes_m4a({"id": "v", "title": "t"}),
        probe_audio=lambda _p: False,  # ffprobe would reject this stub as non-audio
    )
    result = dl.download("https://x/y", dest_dir=tmp_path, basename="v")
    assert result.ok is False
    assert "unplayable" in (result.error or "").lower()


class _StubDownloader:
    def __init__(self, result, name):
        self._result = result
        self.name = name
        self.calls = 0

    def download(self, url, *, dest_dir, basename, source_id=None):
        self.calls += 1
        return self._result


def _ok(method):
    return DownloadResult(ok=True, audio_path=Path("/tmp/a.m4a"), info={"id": "1"}, method=method)


def _fail(method, err="boom"):
    return DownloadResult(ok=False, audio_path=None, info={}, method=method, error=err)


def test_composite_primary_succeeds_skips_fallback():
    primary = _StubDownloader(_ok("yt-dlp"), "primary")
    fallback = _StubDownloader(_ok("playwright"), "fallback")
    comp = CompositeDownloader(primary=primary, fallback=fallback)
    result = comp.download("https://x", dest_dir=Path("/tmp"), basename="b")
    assert result.method == "yt-dlp"
    assert primary.calls == 1
    assert fallback.calls == 0


def test_composite_primary_fails_fallback_succeeds():
    primary = _StubDownloader(_fail("yt-dlp"), "primary")
    fallback = _StubDownloader(_ok("playwright"), "fallback")
    comp = CompositeDownloader(primary=primary, fallback=fallback)
    result = comp.download("https://x", dest_dir=Path("/tmp"), basename="b")
    assert result.ok is True
    assert result.method == "playwright"
    assert fallback.calls == 1


def test_composite_no_fallback_returns_primary_failure():
    primary = _StubDownloader(_fail("yt-dlp"), "primary")
    comp = CompositeDownloader(primary=primary, fallback=None)
    result = comp.download("https://x", dest_dir=Path("/tmp"), basename="b")
    assert result.ok is False
    assert result.method == "yt-dlp"


def test_composite_should_fallback_gate():
    primary = _StubDownloader(_fail("yt-dlp"), "primary")
    fallback = _StubDownloader(_ok("playwright"), "fallback")
    comp = CompositeDownloader(
        primary=primary,
        fallback=fallback,
        should_fallback=lambda url, result, **kw: kw.get("platform") == "tiktok",
    )
    skipped = comp.download("https://x", dest_dir=Path("/tmp"), basename="b", platform="youtube")
    assert skipped.ok is False
    assert fallback.calls == 0
    used = comp.download("https://x", dest_dir=Path("/tmp"), basename="b", platform="tiktok")
    assert used.ok is True
    assert fallback.calls == 1


def test_playwright_capture_unavailable_without_storage_state(tmp_path):
    dl = PlaywrightCaptureDownloader(
        node_script=tmp_path / "missing.cjs",
        storage_state=tmp_path / "missing.json",
        runner=lambda cmd, cwd=None: (0, "", ""),
    )
    assert dl.available() is False
    result = dl.download("https://x", dest_dir=tmp_path, basename="b")
    assert result.ok is False
    assert "unavailable" in (result.error or "").lower()


def test_playwright_capture_success_moves_raw_to_basename(tmp_path):
    script = tmp_path / "capture.cjs"
    script.write_text("// stub", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    dest = tmp_path / "out"
    dest.mkdir()

    def runner(cmd, cwd=None):
        # emulate the cjs writing <music_id>_raw.m4a
        (dest / "999_raw.m4a").write_bytes(b"\x00audio")
        return 0, "captured", ""

    dl = PlaywrightCaptureDownloader(
        node_script=script, storage_state=state, runner=runner, probe_audio=lambda _p: True
    )
    assert dl.available() is True
    result = dl.download("https://x", dest_dir=dest, basename="999", source_id="999")
    assert result.ok is True
    assert result.method == "playwright"
    assert (dest / "999.m4a").exists()
    assert not (dest / "999_raw.m4a").exists()


def test_playwright_capture_bad_output_fails_cleanly(tmp_path):
    # A capture that produces an unplayable file must fail (ok=False), not crash later
    # at packaging -- so the item stays FAILED in the queue with a clear reason.
    script = tmp_path / "capture.cjs"
    script.write_text("// stub", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    dest = tmp_path / "out"
    dest.mkdir()

    def runner(cmd, cwd=None):
        (dest / "999_raw.m4a").write_bytes(b"\x00not-audio")
        return 0, "captured", ""

    dl = PlaywrightCaptureDownloader(
        node_script=script, storage_state=state, runner=runner, probe_audio=lambda _p: False
    )
    result = dl.download("https://x", dest_dir=dest, basename="999", source_id="999")
    assert result.ok is False
    assert "unplayable" in (result.error or "").lower()
