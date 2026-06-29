"""Tests for the concurrent-transcription pipeline + global import progress metric.

Covers the non-Qt layers: inbox counts, the per-item drain callback, import_pending
forwarding, single-item transcribe+reindex, and should_stop threading into the ASR
engine. The Qt pieces (queue worker, progress bar, ETA) live in
test_desktop_gui_workflows.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from sound_vault.ingest.download import DownloadResult
from sound_vault.ingest.package import package_sound
from sound_vault.ingest.resolve import ResolvedSource
from sound_vault.ingest.service import IngestOutcome, IngestService
from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore
from sound_vault.settings import index_path_for_vault
from sound_vault.ui.view_model import LibraryViewModel
from sound_vault.workers.transcription import transcribe_sound_folder


# --- small fakes (kept local so this file doesn't depend on another test module) ---

def _tiktok_music(url: str, music_id: str) -> ResolvedSource:
    return ResolvedSource(
        input_url=url, final_url=url, platform="tiktok", kind="music",
        canonical_url=f"https://www.tiktok.com/music/x-{music_id}", source_id=music_id,
        slug="x", title_guess="X", share_music_id=None, status="ok",
    )


class _Downloader:
    def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        audio = Path(dest_dir) / f"{basename}.m4a"
        audio.write_bytes(b"\x00audio")
        return DownloadResult(ok=True, audio_path=audio, info={"title": "T", "uploader": "A"}, method="yt-dlp")


def _tagger(src, dst, tags):
    Path(dst).write_bytes(Path(src).read_bytes())


def _service(tmp_path: Path, resolve_map: dict) -> IngestService:
    return IngestService(
        vault_root=tmp_path, downloader=_Downloader(),
        resolve_source=lambda url: resolve_map[url], tagger=_tagger,
        now=lambda: "2026-06-13T00:00:00Z",
    )


def _vm(tmp_path: Path) -> LibraryViewModel:
    vault = tmp_path / "vault"
    (vault / "sounds").mkdir(parents=True)
    return LibraryViewModel(
        vault_root=vault, index_path=index_path_for_vault(vault),
        load_sidecars=False, sidecar_mode="summary",
    )


# --- inbox counts (progress metric) ---

def test_inbox_counts_tallies_each_status(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    a = store.add_url("https://t/a", source="s", relay_id="in_a")
    b = store.add_url("https://t/b", source="s", relay_id="in_b")
    store.add_url("https://t/c", source="s", relay_id="in_c")  # stays pending
    store.mark_imported(a.id)
    store.mark_failed(b.id, "boom")
    assert store.counts() == {"total": 3, "pending": 1, "imported": 1, "failed": 1, "other": 0}


def test_inbox_counts_empty(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    assert store.counts() == {"total": 0, "pending": 0, "imported": 0, "failed": 0, "other": 0}


def test_inbox_counts_unknown_status_goes_to_other_and_sums_to_total(tmp_path):
    """A hand-edited/corrupt row with a non-standard status must still be counted
    (in 'other') so processed+pending == total and the progress bar can reach 100%."""
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    a = store.add_url("https://t/a", source="s", relay_id="in_a")
    store.add_url("https://t/b", source="s", relay_id="in_b")  # pending
    store.mark_imported(a.id)
    # Inject a row with an unknown status directly (simulates corruption / future status).
    with open(store.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "url_z", "url": "https://t/z", "source": "s", "status": "weird", "created_at": "t"}) + "\n")

    counts = store.counts()
    assert counts["other"] == 1
    assert counts["total"] == 3
    assert counts["pending"] + counts["imported"] + counts["failed"] + counts["other"] == counts["total"]


# --- drain_inbox per-item callback (drives progress + transcription enqueue) ---

def test_drain_inbox_fires_on_item_per_processed(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    urls = [f"https://t/{i}" for i in range(3)]
    for i, u in enumerate(urls):
        store.add_url(u, source="s", relay_id=f"in_{i}")
    svc = _service(tmp_path, {u: _tiktok_music(u, str(i)) for i, u in enumerate(urls)})

    seen: list[tuple[str, str]] = []
    svc.drain_inbox(store, on_item=lambda o: seen.append((o.status, o.music_id)))

    assert len(seen) == 3
    assert all(status == "ingested" for status, _ in seen)
    assert {mid for _, mid in seen} == {"0", "1", "2"}


def test_drain_inbox_on_item_not_fired_for_interrupted_item(tmp_path):
    store = ShortcutInboxStore(tmp_path / "inbox.jsonl")
    for i in range(3):
        store.add_url(f"https://t/{i}", source="s", relay_id=f"in_{i}")
    svc = _service(tmp_path, {f"https://t/{i}": _tiktok_music(f"https://t/{i}", str(i)) for i in range(3)})

    seen: list = []
    # should_stop True up front → loop breaks before processing anything.
    svc.drain_inbox(store, should_stop=lambda: True, on_item=lambda o: seen.append(o))
    assert seen == []
    assert len(store.pending()) == 3  # all left pending


# --- import_pending forwards the per-item hook ---

def test_import_pending_forwards_on_item(tmp_path, monkeypatch):
    vm = _vm(tmp_path)
    outcomes = [
        IngestOutcome(status="ingested", url="u1", music_id="1"),
        IngestOutcome(status="failed", url="u2", reason="boom"),
        IngestOutcome(status="duplicate", url="u3", music_id="3"),
    ]

    class FakeService:
        def drain_inbox(self, store, *, max_attempts=3, should_stop=None, on_item=None):
            results = []
            for outcome in outcomes:
                if on_item is not None:
                    on_item(outcome)
                results.append((None, outcome))
            return results

    monkeypatch.setattr("sound_vault.ingest.factory.build_ingest_service", lambda **kw: FakeService())

    seen: list[str] = []
    result = vm.import_pending(on_item=lambda o: seen.append(o.status))

    assert seen == ["ingested", "failed", "duplicate"]
    assert [o.status for o in result] == ["ingested", "failed", "duplicate"]


# --- single-item transcribe + incremental reindex (the pipeline consumer's core) ---

def test_transcribe_one_writes_transcript_and_reindexes(tmp_path):
    vm = _vm(tmp_path)
    pkg = package_sound(
        vault_root=vm.vault_root, music_id="42", title="Hook", artist="Creator",
        audio_path=None, now_iso="t",
    )
    audio = pkg.folder / "clip.m4a"
    audio.write_bytes(b"\x00\x00")

    def fake_transcriber(_audio, **_kwargs):
        return {"text": "spoken words", "language": "en", "model": "base", "engine": "faster-whisper"}

    ok = vm.transcribe_one("42", pkg.folder, audio, transcriber=fake_transcriber)

    assert ok is True
    meta = json.loads((pkg.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["speech_transcript_v2"]["text"] == "spoken words"
    # Incrementally upserted into the index (no full rebuild) — queryable right away.
    rec = vm.db.get("42")
    assert rec is not None and rec.transcript_text == "spoken words"


def test_transcribe_one_skips_already_transcribed(tmp_path):
    vm = _vm(tmp_path)
    pkg = package_sound(
        vault_root=vm.vault_root, music_id="7", title="Done", artist="C",
        audio_path=None, now_iso="t",
    )
    audio = pkg.folder / "clip.m4a"
    audio.write_bytes(b"\x00")
    meta = json.loads((pkg.folder / "metadata.json").read_text(encoding="utf-8"))
    meta["speech_transcript_v2"] = {"text": "already here"}
    (pkg.folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    called = {"n": 0}

    def fake_transcriber(_audio, **_kwargs):
        called["n"] += 1
        return {"text": "new", "language": "", "model": "", "engine": "faster-whisper"}

    ok = vm.transcribe_one("7", pkg.folder, audio, transcriber=fake_transcriber)
    assert ok is False  # idempotent: skipped (status != "ok")
    assert called["n"] == 0  # engine never invoked


# --- should_stop threading into the ASR engine (prompt mid-transcribe cancel) ---

def _folder_with_audio(tmp_path: Path) -> tuple[Path, Path]:
    folder = tmp_path / "sound"
    folder.mkdir()
    audio = folder / "a.m4a"
    audio.write_bytes(b"\x00")
    (folder / "metadata.json").write_text(json.dumps({"tiktok_music_id": "X", "paths": {}}), encoding="utf-8")
    return folder, audio


def test_transcribe_sound_folder_forwards_should_stop(tmp_path):
    folder, audio = _folder_with_audio(tmp_path)
    seen = {}

    def fake(_audio, should_stop=None):
        seen["should_stop"] = should_stop
        return {"text": "hi", "language": "en", "model": "base", "engine": "faster-whisper"}

    flag = lambda: False  # noqa: E731
    res = transcribe_sound_folder(folder, audio_path=audio, transcriber=fake, should_stop=flag)
    assert res["status"] == "ok"
    assert seen["should_stop"] is flag


def test_transcribe_sound_folder_legacy_transcriber_without_should_stop(tmp_path):
    folder, audio = _folder_with_audio(tmp_path)

    def legacy(_audio):  # two-arg-incompatible: no should_stop kwarg
        return {"text": "hi", "language": "en", "model": "base", "engine": "faster-whisper"}

    # Passing should_stop must not break a transcriber that doesn't accept it.
    res = transcribe_sound_folder(folder, audio_path=audio, transcriber=legacy, should_stop=lambda: False)
    assert res["status"] == "ok"
