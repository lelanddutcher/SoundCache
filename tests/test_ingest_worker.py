from pathlib import Path

import pytest

from sound_vault.db.index_db import IndexDatabase
from sound_vault.ingest.cli import run_ingest
from sound_vault.ingest.download import DownloadResult
from sound_vault.ingest.factory import build_downloader, make_index_updater
from sound_vault.ingest.package import package_sound
from sound_vault.ingest.resolve import ResolvedSource
from sound_vault.ingest.service import IngestService
from sound_vault.ui.view_model import LibraryViewModel


def fake_tagger(src, dst, tags):
    Path(dst).write_bytes(Path(src).read_bytes())


def tiktok_music(url, music_id="123"):
    return ResolvedSource(
        input_url=url, final_url=url, platform="tiktok", kind="music",
        canonical_url=f"https://www.tiktok.com/music/X-{music_id}", source_id=music_id,
        slug="X", title_guess="X", share_music_id=None, status="ok",
    )


class FakeDownloader:
    def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        audio = Path(dest_dir) / f"{basename}.m4a"
        audio.write_bytes(b"\x00audio")
        return DownloadResult(ok=True, audio_path=audio, info={"title": "T", "uploader": "A"}, method="yt-dlp")


# ---- factory ----

def test_make_index_updater_upserts(tmp_path):
    db = IndexDatabase(tmp_path / "i.sqlite3")
    pkg = package_sound(vault_root=tmp_path, music_id="123", title="Kickoff", artist="C", audio_path=None, now_iso="t")
    make_index_updater(tmp_path, db)(pkg)
    got = db.get("123")
    assert got is not None and got.title == "Kickoff"


def test_build_downloader_no_fallback_when_unconfigured(monkeypatch):
    # No env override and nothing resolvable -> no TikTok capture fallback.
    for var in ("SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT", "SOUND_VAULT_TIKTOK_STATE", "SOUND_VAULT_TIKTOK_CAPTURE_CWD"):
        monkeypatch.delenv(var, raising=False)
    # Neutralize the bundled defaults so the result is machine-independent (on a
    # configured machine the app-data storageState would otherwise enable it).
    monkeypatch.setattr("sound_vault.ingest.factory._default_capture_script", lambda: None)
    monkeypatch.setattr("sound_vault.ingest.factory._default_storage_state", lambda: None)
    dl = build_downloader()
    assert dl.fallback is None


def test_build_downloader_auto_wires_from_defaults(monkeypatch, tmp_path):
    # When the bundled script + an auth storageState both resolve, the fallback is
    # attached automatically (no env vars / explicit args needed) — this is the
    # wiring whose absence caused TikTok sound ingestion to silently fail.
    for var in ("SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT", "SOUND_VAULT_TIKTOK_STATE", "SOUND_VAULT_TIKTOK_CAPTURE_CWD"):
        monkeypatch.delenv(var, raising=False)
    script = tmp_path / "capture_tiktok_audio.cjs"
    script.write_text("x", encoding="utf-8")
    state = tmp_path / "tiktok.storageState.json"
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("sound_vault.ingest.factory._default_capture_script", lambda: script)
    monkeypatch.setattr("sound_vault.ingest.factory._default_storage_state", lambda: state)
    dl = build_downloader()
    assert dl.fallback is not None
    assert dl.should_fallback("u", None, platform="tiktok") is True


def test_ensure_media_tools_on_path_adds_existing_dirs(monkeypatch, tmp_path):
    # A Finder/launchd-launched GUI gets a stripped PATH; the augmenter must add
    # real bin dirs (so node/ffmpeg resolve) and be idempotent.
    from sound_vault.ingest import factory

    binA = tmp_path / "binA"
    binA.mkdir()
    monkeypatch.setattr(factory, "_EXTRA_BIN_DIRS", (str(binA), "/no/such/dir/xyz"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    factory.ensure_media_tools_on_path()
    parts = __import__("os").environ["PATH"].split(":")
    assert str(binA) in parts  # real dir added
    assert "/no/such/dir/xyz" not in parts  # missing dir skipped
    factory.ensure_media_tools_on_path()  # idempotent
    assert __import__("os").environ["PATH"].split(":").count(str(binA)) == 1


def test_build_downloader_with_fallback_gates_to_tiktok(tmp_path):
    script = tmp_path / "c.cjs"
    script.write_text("x", encoding="utf-8")
    state = tmp_path / "s.json"
    state.write_text("{}", encoding="utf-8")
    dl = build_downloader(playwright_script=script, playwright_state=state)
    assert dl.fallback is not None
    assert dl.should_fallback("u", None, platform="tiktok") is True
    assert dl.should_fallback("u", None, platform="youtube") is False


# ---- run_ingest loop ----

class _FakeService:
    def __init__(self):
        self.drained = 0

    def drain_inbox(self, store, *, max_attempts=3):
        self.drained += 1
        return [("item", "outcome")]


class _FakeRelay:
    def __init__(self):
        self.polled = 0

    def poll_to_inbox(self, path):
        self.polled += 1
        return []


class _FakeStore:
    path = Path("/tmp/inbox.jsonl")


def test_run_ingest_once_polls_then_drains():
    svc = _FakeService()
    relay = _FakeRelay()
    out = run_ingest(service=svc, store=_FakeStore(), relay_client=relay, once=True)
    assert relay.polled == 1
    assert svc.drained == 1
    assert out == [("item", "outcome")]


def test_run_ingest_watch_loops_until_interrupted():
    svc = _FakeService()
    ticks = {"n": 0}

    def sleeper(_interval):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_ingest(service=svc, store=_FakeStore(), once=False, interval=0, sleep=sleeper)
    assert svc.drained == 3


# ---- view-model wiring ----

def test_view_model_import_pending(tmp_path, monkeypatch):
    vm = LibraryViewModel(vault_root=tmp_path, index_path=tmp_path / "i.sqlite3")
    vm.inbox.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_1")

    svc = IngestService(
        vault_root=tmp_path,
        downloader=FakeDownloader(),
        resolve_source=lambda url: tiktok_music(url, music_id="A"),
        tagger=fake_tagger,
        index_updater=make_index_updater(tmp_path, vm.db),
        now=lambda: "t",
    )
    monkeypatch.setattr("sound_vault.ingest.factory.build_ingest_service", lambda **kw: svc)

    outcomes = vm.import_pending()
    assert [o.status for o in outcomes] == ["ingested"]
    assert vm.inbox.pending() == []
    assert vm.db.get("A") is not None
    assert "A" in vm._records_by_id


def test_import_pending_reports_save_events(tmp_path, monkeypatch):
    vm = LibraryViewModel(vault_root=tmp_path, index_path=tmp_path / "i.sqlite3")
    vm.inbox.add_url("https://www.tiktok.com/t/a/", source="ios_shortcut", relay_id="in_1")
    svc = IngestService(
        vault_root=tmp_path,
        downloader=FakeDownloader(),
        resolve_source=lambda url: tiktok_music(url, music_id="A"),
        tagger=fake_tagger,
        index_updater=make_index_updater(tmp_path, vm.db),
        now=lambda: "t",
    )
    monkeypatch.setattr("sound_vault.ingest.factory.build_ingest_service", lambda **kw: svc)

    reported = []

    class _Reporter:
        def report_save(self, **kwargs):
            reported.append(kwargs)

    vm.import_pending(reporter=_Reporter())
    assert reported == [{"sound_id": "A", "platform": "tiktok", "title": "X", "artist": "A"}]
