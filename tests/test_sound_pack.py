from __future__ import annotations

import json

import pytest

from sound_vault.settings import index_path_for_vault
from sound_vault.ui.view_model import LibraryViewModel


def _vm(tmp_path):
    vault = tmp_path / "vault"
    (vault / "sounds").mkdir(parents=True)
    return LibraryViewModel(
        vault_root=vault, index_path=index_path_for_vault(vault), load_sidecars=False, sidecar_mode="summary"
    )


def _pack(tmp_path, packs):
    p = tmp_path / "pack.json"
    p.write_text(json.dumps({"sound_cache_pack_version": 1, "packs": packs}), encoding="utf-8")
    return p


def test_import_sound_pack_queues_and_tags(tmp_path):
    vm = _vm(tmp_path)
    pack = _pack(
        tmp_path,
        [
            {"name": "Meme", "slug": "meme", "sounds": [
                {"music_id": "1", "url": "https://www.tiktok.com/music/x-1", "title": "A", "artist": "a"},
                {"music_id": "2", "url": "https://www.tiktok.com/music/y-2", "title": "B", "artist": "b"},
            ]},
        ],
    )
    summary = vm.import_sound_pack(pack)
    assert summary["queued"] == 2 and summary["rejected"] == 0
    pending = vm.inbox.pending()
    assert {i.url for i in pending} == {"https://www.tiktok.com/music/x-1", "https://www.tiktok.com/music/y-2"}
    assert all(i.source == "pack:meme" for i in pending)  # tagged with the pack


def test_import_sound_pack_is_idempotent(tmp_path):
    vm = _vm(tmp_path)
    pack = _pack(tmp_path, [{"name": "Vibes", "slug": "vibes", "sounds": [
        {"music_id": "9", "url": "https://www.tiktok.com/music/z-9", "title": "T", "artist": "a"}]}])
    assert vm.import_sound_pack(pack)["queued"] == 1
    second = vm.import_sound_pack(pack)
    assert second["queued"] == 0 and second["skipped"] == 1  # no duplicate


def test_import_sound_pack_rejects_unsafe_and_offplatform_urls(tmp_path):
    vm = _vm(tmp_path)
    pack = _pack(
        tmp_path,
        [{"name": "Bad", "slug": "bad", "sounds": [
            {"music_id": "1", "url": "file:///etc/passwd"},
            {"music_id": "2", "url": "http://169.254.169.254/latest/meta-data/"},  # cloud metadata SSRF
            {"music_id": "3", "url": "javascript:alert(1)"},
            {"music_id": "4", "url": "https://evil.example.com/music/x-4"},  # off-platform host
            {"music_id": "5", "url": "https://www.tiktok.com/music/ok-5"},  # the only valid one
        ]}],
    )
    summary = vm.import_sound_pack(pack)
    assert summary["queued"] == 1
    assert summary["rejected"] == 4
    assert [i.url for i in vm.inbox.pending()] == ["https://www.tiktok.com/music/ok-5"]


def test_import_sound_pack_rejects_non_pack_json(tmp_path):
    vm = _vm(tmp_path)
    bad = tmp_path / "nope.json"
    bad.write_text(json.dumps({"not": "a pack"}), encoding="utf-8")
    with pytest.raises(ValueError):
        vm.import_sound_pack(bad)
