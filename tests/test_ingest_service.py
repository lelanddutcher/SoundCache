import json
from pathlib import Path

from sound_vault.ingest.download import DownloadResult
from sound_vault.ingest.resolve import ResolvedSource
from sound_vault.ingest.service import IngestOutcome, IngestService
from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore


def fake_tagger(src, dst, tags):
    Path(dst).write_bytes(Path(src).read_bytes())


def tiktok_music(url, music_id="123", title_guess="Kickoff"):
    return ResolvedSource(
        input_url=url,
        final_url=url,
        platform="tiktok",
        kind="music",
        canonical_url=f"https://www.tiktok.com/music/Kickoff-{music_id}",
        source_id=music_id,
        slug="Kickoff",
        title_guess=title_guess,
        share_music_id=None,
        status="ok",
    )


class FakeDownloader:
    def __init__(self, *, ok=True, audio=True, info=None, error=None):
        self.ok = ok
        self.audio = audio
        self.info = info or {"id": "x", "title": "DL Title", "uploader": "DL Artist", "duration": 10}
        self.error = error
        self.calls = 0

    def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
        self.calls += 1
        if not self.ok:
            return DownloadResult(ok=False, audio_path=None, info={}, method="yt-dlp", error=self.error or "download failed")
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        audio_path = None
        if self.audio:
            audio_path = Path(dest_dir) / f"{basename}.m4a"
            audio_path.write_bytes(b"\x00audio")
        return DownloadResult(ok=True, audio_path=audio_path, info=self.info, method="yt-dlp")


def make_service(tmp_path, downloader, *, resolve_map=None, index_sink=None):
    resolve_map = resolve_map or {}

    def resolve_source(url):
        return resolve_map.get(url, tiktok_music(url))

    return IngestService(
        vault_root=tmp_path,
        downloader=downloader,
        resolve_source=resolve_source,
        tagger=fake_tagger,
        index_updater=index_sink,
        now=lambda: "2026-06-13T00:00:00Z",
    )


def test_work_dir_defaults_to_local_not_the_vault(tmp_path):
    # The transient capture/transcode/probe dir must NOT live on the vault: on an NFS/SMB
    # mount, close-to-open consistency truncates the moved audio and a good capture gets
    # rejected as "unplayable" (no TikTok sound could ever land). It must be local.
    import tempfile

    svc = IngestService(vault_root=tmp_path, downloader=FakeDownloader())
    assert str(tmp_path) not in str(svc._work_dir)
    assert str(svc._work_dir).startswith(tempfile.gettempdir())


def test_ingest_url_success(tmp_path):
    indexed = []
    dl = FakeDownloader()
    svc = make_service(tmp_path, dl, index_sink=indexed.append)
    out = svc.ingest_url("https://www.tiktok.com/t/abc/", source="ios_shortcut")

    assert isinstance(out, IngestOutcome)
    assert out.status == "ingested"
    assert out.music_id == "123"
    assert out.folder is not None and out.folder.is_dir()
    assert out.audio_path is not None and out.audio_path.exists()
    catalog = tmp_path / "catalog" / "sounds.jsonl"
    rows = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1 and rows[0]["tiktok_music_id"] == "123"
    assert rows[0]["tiktok_visible_title"] == "Kickoff"  # tiktok music prefers slug title_guess
    assert len(indexed) == 1
    # working dir cleaned up
    assert not (tmp_path / "inbox" / "working" / "123").exists()


def test_ingest_url_is_idempotent(tmp_path):
    dl = FakeDownloader()
    svc = make_service(tmp_path, dl)
    first = svc.ingest_url("https://www.tiktok.com/t/abc/")
    second = svc.ingest_url("https://www.tiktok.com/t/abc/")
    assert first.status == "ingested"
    assert second.status == "duplicate"
    assert dl.calls == 1  # no re-download


def test_empty_partial_folder_is_not_a_duplicate_so_retry_reingests(tmp_path):
    # A prior FAILED attempt can leave a bare vault folder (mkdir before the audio).
    # That must NOT be seen as an "already ingested" duplicate, or the retry gets
    # consumed with no sound -> silent data loss (the reported bug).
    (tmp_path / "sounds" / "123 - stale-partial").mkdir(parents=True)
    dl = FakeDownloader()
    svc = make_service(tmp_path, dl)
    out = svc.ingest_url("https://www.tiktok.com/t/abc/")
    assert out.status == "ingested"   # re-ingested, NOT falsely "duplicate"
    assert dl.calls == 1              # actually downloaded (did not short-circuit)
    assert out.audio_path is not None and out.audio_path.exists()


def test_metadata_without_audio_folder_is_not_a_duplicate(tmp_path):
    # A folder whose metadata.json CLAIMS audio but whose file is gone is a corrupt
    # partial, not a real duplicate -> a retry must re-ingest it.
    import json as _json
    f = tmp_path / "sounds" / "123 - phantom"
    f.mkdir(parents=True)
    (f / "metadata.json").write_text(_json.dumps({"paths": {"audio": "sounds/123 - phantom/gone.m4a"}}), encoding="utf-8")
    dl = FakeDownloader()
    svc = make_service(tmp_path, dl)
    out = svc.ingest_url("https://www.tiktok.com/t/abc/")
    assert out.status == "ingested"
    assert dl.calls == 1


class _PlaywrightFallback:
    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
        self.calls += 1
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        p = Path(dest_dir) / f"{basename}.m4a"
        p.write_bytes(b"\x00GOOD")
        return DownloadResult(ok=True, audio_path=p, info={"title": "Captured"}, method="playwright")


class _PrimaryWithFallback(FakeDownloader):
    """A yt-dlp primary that downloads a file which passes the download step but can't be
    packaged, exposing a .fallback like the real CompositeDownloader."""

    def __init__(self, fallback):
        super().__init__()
        self.fallback = fallback

    def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
        self.calls += 1
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        p = Path(dest_dir) / f"{basename}.m4a"
        p.write_bytes(b"\x00BAD")
        return DownloadResult(ok=True, audio_path=p, info={"title": "DL"}, method="yt-dlp")


def _tagger_fails_on_bad(src, dst, tags):
    if Path(src).read_bytes() == b"\x00BAD":
        raise RuntimeError("ffmpeg failed (exit 183) tagging: moov atom not found")
    Path(dst).write_bytes(Path(src).read_bytes())


def test_package_failure_falls_back_to_playwright(tmp_path):
    fb = _PlaywrightFallback()
    dl = _PrimaryWithFallback(fb)
    svc = IngestService(
        vault_root=tmp_path, downloader=dl, resolve_source=tiktok_music,
        tagger=_tagger_fails_on_bad, now=lambda: "2026-06-13T00:00:00Z",
    )
    out = svc.ingest_url("https://www.tiktok.com/t/abc/")
    assert out.status == "ingested"      # recovered via the fallback rather than dead-ending
    assert fb.calls == 1                 # the Playwright fallback WAS invoked on the package failure
    assert out.audio_path is not None and out.audio_path.exists()


def test_package_failure_without_fallback_stays_failed_in_queue(tmp_path):
    dl = FakeDownloader()  # ok=True, writes a file; no .fallback attribute
    svc = IngestService(
        vault_root=tmp_path, downloader=dl, resolve_source=tiktok_music,
        tagger=_tagger_fails_on_bad, now=lambda: "2026-06-13T00:00:00Z",
    )
    # write a file the tagger will reject, by making FakeDownloader's file the "bad" bytes
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/xyz/", source="ios_shortcut")

    def bad_downloader(url, *, dest_dir, basename, source_id=None, **kwargs):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        p = Path(dest_dir) / f"{basename}.m4a"
        p.write_bytes(b"\x00BAD")
        return DownloadResult(ok=True, audio_path=p, info={"title": "DL"}, method="yt-dlp")

    dl.download = bad_downloader
    svc.drain_inbox(store, max_attempts=1)
    failed = store.failed()
    assert len(failed) == 1                       # item stays in the queue, not dropped
    assert "ffmpeg failed" in (failed[0].error or "")  # with the real error reported


def test_ingest_url_resolve_failure(tmp_path):
    def bad_resolve(url):
        return ResolvedSource(
            input_url=url, final_url=None, platform="tiktok", kind="unknown",
            canonical_url=None, source_id=None, slug=None, title_guess=None,
            share_music_id=None, status="error", error="URLError: timeout",
        )

    dl = FakeDownloader()
    svc = IngestService(vault_root=tmp_path, downloader=dl, resolve_source=bad_resolve, tagger=fake_tagger)
    out = svc.ingest_url("https://bad/")
    assert out.status == "failed"
    assert "timeout" in (out.reason or "")
    assert dl.calls == 0


def test_ingest_url_download_failure_cleans_up(tmp_path):
    dl = FakeDownloader(ok=False, error="HTTP 403")
    svc = make_service(tmp_path, dl)
    out = svc.ingest_url("https://www.tiktok.com/t/abc/")
    assert out.status == "failed"
    assert "403" in (out.reason or "")
    catalog = tmp_path / "catalog" / "sounds.jsonl"
    assert not catalog.exists() or catalog.read_text(encoding="utf-8").strip() == ""
    assert not (tmp_path / "inbox" / "working" / "123").exists()


def test_drain_inbox_marks_imported(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_1")
    store.add_url("https://www.tiktok.com/t/b/", source="ios_shortcut", relay_id="in_2")
    resolve_map = {
        "https://www.tiktok.com/t/a/": tiktok_music("https://www.tiktok.com/t/a/", music_id="A"),
        "https://www.tiktok.com/t/b/": tiktok_music("https://www.tiktok.com/t/b/", music_id="B"),
    }
    svc = make_service(tmp_path, FakeDownloader(), resolve_map=resolve_map)
    outcomes = svc.drain_inbox(store)
    assert {o.status for _, o in outcomes} == {"ingested"}
    assert store.pending() == []
    assert all(item.status == "imported" for item in store.all_items())


def test_drain_inbox_records_failure_and_retries_until_exhausted(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/x/", source="ios_shortcut", relay_id="in_1")
    svc = make_service(tmp_path, FakeDownloader(ok=False, error="boom"))
    svc.drain_inbox(store, max_attempts=2)
    assert store.pending()[0].attempts == 1  # still retriable
    svc.drain_inbox(store, max_attempts=2)
    assert store.pending() == []
    failed = store.all_items()[0]
    assert failed.status == "failed"
    assert failed.attempts == 2
    assert "boom" in (failed.error or "")


def test_drain_inbox_should_stop_halts_between_items_leaving_rest_pending(tmp_path):
    """A quit (should_stop) during a bulk import must stop promptly and leave the
    not-yet-processed items fully pending — no failure attempt burned — so they
    retry cleanly next run. This is the cooperative-cancel path that lets the
    import worker thread exit instead of crashing Qt at teardown."""
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    for i in range(4):
        store.add_url(f"https://www.tiktok.com/t/{i}/", source="ios_shortcut", relay_id=f"in_{i}")
    resolve_map = {
        f"https://www.tiktok.com/t/{i}/": tiktok_music(f"https://www.tiktok.com/t/{i}/", music_id=str(i))
        for i in range(4)
    }
    svc = make_service(tmp_path, FakeDownloader(), resolve_map=resolve_map)

    # Stop after the first item has been ingested.
    state = {"n": 0}

    def should_stop():
        return state["n"] >= 1

    original = svc.ingest_url

    def counting_ingest(url, **kwargs):
        out = original(url, **kwargs)
        state["n"] += 1
        return out

    svc.ingest_url = counting_ingest  # type: ignore[method-assign]
    outcomes = svc.drain_inbox(store, should_stop=should_stop)

    assert len(outcomes) == 1  # only the first item was processed+recorded
    assert outcomes[0][1].status == "ingested"
    pending = store.pending()
    assert len(pending) == 3  # the rest are untouched
    assert all(item.attempts == 0 for item in pending)  # no failure attempt burned


def test_drain_inbox_should_stop_forwards_cancel_into_download(tmp_path):
    """should_stop is forwarded into the active download so a long capture can be
    killed mid-flight, not just checked between items."""
    seen = {}

    class CancelAwareDownloader(FakeDownloader):
        def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
            seen["should_stop"] = kwargs.get("should_stop")
            return super().download(url, dest_dir=dest_dir, basename=basename, source_id=source_id, **kwargs)

    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_1")
    svc = make_service(tmp_path, CancelAwareDownloader())
    flag = lambda: False  # noqa: E731 - tiny predicate
    svc.drain_inbox(store, should_stop=flag)
    assert seen["should_stop"] is flag


def test_ingest_url_writes_user_note_to_metadata(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    out = svc.ingest_url("https://www.tiktok.com/t/abc/", source="ios_shortcut", note="  use for gym intros  ")
    assert out.status == "ingested"
    meta = json.loads((out.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["user_notes"] == "use for gym intros"


def test_drain_inbox_carries_note_into_metadata(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    store.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_1", note="wedding vibes")
    resolve_map = {"https://www.tiktok.com/t/a/": tiktok_music("https://www.tiktok.com/t/a/", music_id="A")}
    svc = make_service(tmp_path, FakeDownloader(), resolve_map=resolve_map)
    [(_, outcome)] = svc.drain_inbox(store)
    assert outcome.status == "ingested"
    meta = json.loads((outcome.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["user_notes"] == "wedding vibes"


def test_ingest_non_tiktok_uses_download_title(tmp_path):
    yt = ResolvedSource(
        input_url="https://youtu.be/dQw4w9WgXcQ", final_url="https://youtu.be/dQw4w9WgXcQ",
        platform="youtube", kind="video", canonical_url="https://youtu.be/dQw4w9WgXcQ",
        source_id="dQw4w9WgXcQ", slug=None, title_guess=None, share_music_id=None, status="ok",
    )
    dl = FakeDownloader(info={"id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up", "uploader": "Rick Astley"})
    svc = make_service(tmp_path, dl, resolve_map={"https://youtu.be/dQw4w9WgXcQ": yt})
    out = svc.ingest_url("https://youtu.be/dQw4w9WgXcQ")
    assert out.status == "ingested"
    assert out.music_id == "dQw4w9WgXcQ"
    assert out.folder.name.startswith("dQw4w9WgXcQ - Never Gonna Give You Up")


def test_ingest_url_captures_from_music_page_for_resolved_video(tmp_path):
    """A video resolved to its sound must capture from the /music/ page (clean,
    full sound), not the original video URL (the trimmed clip)."""
    seen = {}

    class RecordingDownloader(FakeDownloader):
        def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
            seen["url"] = url
            return super().download(url, dest_dir=dest_dir, basename=basename, source_id=source_id, **kwargs)

    video_url = "https://www.tiktok.com/@u/video/7123456789012345678"
    resolved = ResolvedSource(
        input_url=video_url,
        final_url=video_url,  # the video
        platform="tiktok",
        kind="music",
        canonical_url="https://www.tiktok.com/music/espresso-999",  # the resolved sound
        source_id="999",
        slug="espresso",
        title_guess="espresso",
        share_music_id=None,
        status="ok",
    )
    svc = make_service(tmp_path, RecordingDownloader(), resolve_map={video_url: resolved})
    out = svc.ingest_url(video_url)
    assert out.status == "ingested" and out.music_id == "999"
    assert seen["url"] == "https://www.tiktok.com/music/espresso-999"  # NOT the video URL


def test_title_artist_ignores_placeholder_slug_uses_real_title():
    from sound_vault.ingest.service import IngestService
    # A favorites/pack/deeplink URL we synthesized as /music/sound-<id> yields
    # title_guess="sound" — must NOT become the title; the captured title wins.
    resolved = tiktok_music("u", music_id="999", title_guess="sound")
    title, artist = IngestService._title_artist(resolved, {"title": "Espresso", "uploader": "dj"})
    assert title == "Espresso" and artist == "dj"
    # With no captured title, a placeholder slug falls through to Unknown (not "sound")
    title2, _ = IngestService._title_artist(resolved, {"title": "", "uploader": "dj"})
    assert title2 == "Unknown"
    # A REAL slug from a direct /music/<name>-<id> share is still used as the title
    real = tiktok_music("u", music_id="123", title_guess="Kickoff")
    title3, _ = IngestService._title_artist(real, {"title": "DL Title", "uploader": "x"})
    assert title3 == "Kickoff"
