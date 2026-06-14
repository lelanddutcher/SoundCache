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


def test_build_downloader_no_fallback_by_default(monkeypatch):
    for var in ("SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT", "SOUND_VAULT_TIKTOK_STATE", "SOUND_VAULT_TIKTOK_CAPTURE_CWD"):
        monkeypatch.delenv(var, raising=False)
    dl = build_downloader()
    assert dl.fallback is None


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
