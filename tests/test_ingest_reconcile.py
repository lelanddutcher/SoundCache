import json

from sound_vault.ingest.receipts import ReceiptLedger
from sound_vault.ingest.service import IngestService
from sound_vault.ingest.shortcut_inbox import ShortcutInboxStore

from test_ingest_service import FakeDownloader, make_service


def _store(tmp_path):
    return ShortcutInboxStore(tmp_path / "inbox" / "inbox.jsonl")


def _landed_folder(vault, music_id, title="Song", artist="Artist"):
    """A healthy packaged sound: metadata claims audio AND the file is really there."""
    folder = vault / "sounds" / f"{music_id} - {title} - {artist}"
    folder.mkdir(parents=True)
    rel = f"sounds/{folder.name}/audio.m4a"
    (vault / rel).write_bytes(b"\x00audio-bytes")
    (folder / "metadata.json").write_text(
        json.dumps(
            {
                "tiktok_music_id": music_id,
                "source_url": f"https://www.tiktok.com/t/{music_id}/",
                "canonical_url": f"https://www.tiktok.com/music/sound-{music_id}",
                "paths": {"folder": f"sounds/{folder.name}", "audio": rel},
            }
        ),
        encoding="utf-8",
    )
    return folder


def _phantom_meta_folder(vault, music_id, source_url):
    """A phantom: metadata CLAIMS audio but the file was never written (the bug)."""
    folder = vault / "sounds" / f"{music_id} - Phantom - X"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps(
            {"tiktok_music_id": music_id, "source_url": source_url, "paths": {"audio": f"sounds/{folder.name}/gone.m4a"}}
        ),
        encoding="utf-8",
    )
    return folder


# ---- reconstruct helper ---------------------------------------------------


def test_reconstruct_music_url():
    assert IngestService._reconstruct_music_url("123") == "https://www.tiktok.com/music/sound-123"
    assert IngestService._reconstruct_music_url("src_abc") == ""  # non-numeric id: unrecoverable
    assert IngestService._reconstruct_music_url("") == ""


# ---- reconcile: relay deliveries vs the vault -----------------------------


def test_reconcile_delivery_that_landed_is_verified(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    ReceiptLedger.beside(store.path).record_received_many([{"relay_id": "in_1", "url": "https://t/a"}])
    item = store.add_url("https://t/a", source="ios", relay_id="in_1")
    store.mark_imported(item.id, music_id="A")
    _landed_folder(tmp_path, "A")

    report = svc.reconcile(store)

    assert report.received == 1
    assert report.landed == 1
    assert report.requeued == 0
    assert store.pending() == []  # nothing to recover


def test_reconcile_imported_with_missing_audio_is_requeued(tmp_path):
    # The exact reported failure: an item marked imported whose audio never landed
    # (phantom) must be detected and reset to pending so it re-ingests.
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    ReceiptLedger.beside(store.path).record_received_many([{"relay_id": "in_2", "url": "https://t/b"}])
    item = store.add_url("https://t/b", source="ios", relay_id="in_2")
    store.mark_imported(item.id, music_id="B")  # marked imported, but no vault folder for B

    report = svc.reconcile(store)

    assert report.landed == 0
    assert report.requeued == 1
    assert [i.status for i in store.all_items()] == ["pending"]  # reset for recovery


def test_reconcile_stranded_delivery_never_queued_is_requeued(tmp_path):
    # A crash between the receipt append and the inbox add leaves a delivery on record
    # with no queue row. Reconciliation must re-queue it from the retained URL.
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    ReceiptLedger.beside(store.path).record_received_many(
        [{"relay_id": "in_3", "url": "https://www.tiktok.com/t/c/", "source": "ios_shortcut"}]
    )

    report = svc.reconcile(store)

    assert report.requeued == 1
    pending = store.pending()
    assert len(pending) == 1 and pending[0].url == "https://www.tiktok.com/t/c/"


# ---- reconcile: vault-integrity sweep (recovers pre-ledger phantoms) -------


def test_reconcile_vault_phantom_recovered_from_source_url(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    _phantom_meta_folder(tmp_path, "7362664349930556192", "https://www.tiktok.com/t/dirty/")

    report = svc.reconcile(store)

    assert report.phantom_folders == 1
    assert report.requeued == 1
    assert store.pending()[0].url == "https://www.tiktok.com/t/dirty/"


def test_reconcile_bare_phantom_folder_reconstructs_music_url(tmp_path):
    # A bare mkdir-before-audio shell with no metadata: recover by rebuilding the
    # /music/ URL from the numeric id embedded in the folder name.
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    (tmp_path / "sounds" / "7649828127887117088 - son original - x").mkdir(parents=True)

    report = svc.reconcile(store)

    assert report.phantom_folders == 1
    assert report.requeued == 1
    assert store.pending()[0].url == "https://www.tiktok.com/music/sound-7649828127887117088"


def test_reconcile_url_only_folder_is_not_phantom(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    folder = tmp_path / "sounds" / "URLONLY - a - b"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps({"tiktok_music_id": "URLONLY", "paths": {"audio": None}}), encoding="utf-8"
    )

    report = svc.reconcile(store)

    assert report.phantom_folders == 0  # intentionally audio-less -> healthy
    assert report.requeued == 0


def test_reconcile_healthy_folder_not_flagged(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    _landed_folder(tmp_path, "HEALTHY")

    report = svc.reconcile(store)

    assert report.phantom_folders == 0
    assert report.requeued == 0


def test_folder_for_trusts_real_audio_despite_stale_absolute_path(tmp_path):
    # Real-vault case: metadata.paths.audio is a stale ABSOLUTE /nas/... path that no
    # longer resolves after a vault move, but the audio is physically in the folder.
    # _folder_for must still recognize it as complete (so it counts as a duplicate and
    # is NOT re-imported, and NOT flagged as a phantom loss).
    svc = make_service(tmp_path, FakeDownloader())
    folder = tmp_path / "sounds" / "555 - Song - Artist"
    folder.mkdir(parents=True)
    (folder / "audio.m4a").write_bytes(b"\x00realaudio")
    (folder / "metadata.json").write_text(
        json.dumps({"tiktok_music_id": "555", "paths": {"audio": "/nas/old/gone.m4a"}}), encoding="utf-8"
    )
    assert svc._folder_for("555") == folder


def test_reconcile_stale_absolute_audio_path_is_not_phantom(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    folder = tmp_path / "sounds" / "555 - Song - Artist"
    folder.mkdir(parents=True)
    (folder / "audio.m4a").write_bytes(b"\x00realaudio")
    (folder / "metadata.json").write_text(
        json.dumps(
            {"tiktok_music_id": "555", "paths": {"audio": "/nas/TikTok Sound Vault/sounds/555 - Song - Artist/audio.m4a"}}
        ),
        encoding="utf-8",
    )
    report = svc.reconcile(store)
    assert report.phantom_folders == 0  # real audio on disk -> healthy despite stale path
    assert report.requeued == 0


def test_apple_double_shadow_is_not_counted_as_audio(tmp_path):
    # A ._ AppleDouble shadow beside a (missing) real file must NOT rescue a phantom.
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    folder = tmp_path / "sounds" / "666 - Phantom - X"
    folder.mkdir(parents=True)
    (folder / "._ghost.m4a").write_bytes(b"\x00\x00applesauce")  # AppleDouble metadata, not audio
    (folder / "metadata.json").write_text(
        json.dumps(
            {"tiktok_music_id": "666", "source_url": "https://t/ghost", "paths": {"audio": "sounds/666 - Phantom - X/ghost.m4a"}}
        ),
        encoding="utf-8",
    )
    report = svc.reconcile(store)
    assert report.phantom_folders == 1
    assert store.pending()[0].url == "https://t/ghost"


def test_reconcile_ignores_non_sound_dirs(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    (tmp_path / "sounds" / "reports").mkdir(parents=True)  # a stray non-sound directory
    report = svc.reconcile(store)
    assert report.phantom_folders == 0
    assert report.requeued == 0


def test_reconcile_dedups_phantom_item_and_its_folder(tmp_path):
    # An imported item whose audio is missing AND a phantom folder for the same id must
    # recover ONCE, not twice.
    svc = make_service(tmp_path, FakeDownloader())
    store = _store(tmp_path)
    item = store.add_url("https://t/dup", source="ios", relay_id="in_dup")
    store.mark_imported(item.id, music_id="777")
    _phantom_meta_folder(tmp_path, "777", "https://t/dup")

    report = svc.reconcile(store)

    assert report.phantom_folders == 1
    assert report.requeued == 1  # deduped: the folder recovery is skipped for id 777
