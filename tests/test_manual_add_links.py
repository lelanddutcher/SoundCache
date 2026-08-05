"""Manual "Add sound from link" — the no-relay-required way to add a sound.

Hand-pasted links go into the SAME inbox the relay feeds, so they inherit receipts,
dedup, retries and the normal import worker. These tests pin the parsing (people paste
messy text, not tidy URLs) and the safety rules (SSRF / off-platform hosts).
"""
from __future__ import annotations

from sound_vault.settings import index_path_for_vault
from sound_vault.ui.view_model import LibraryViewModel


def _vm(tmp_path):
    vault = tmp_path / "vault"
    (vault / "sounds").mkdir(parents=True)
    return LibraryViewModel(
        vault_root=vault, index_path=index_path_for_vault(vault), load_sidecars=False, sidecar_mode="summary"
    )


def test_queues_a_single_pasted_link(tmp_path):
    vm = _vm(tmp_path)
    res = vm.add_manual_links("https://www.tiktok.com/music/sound-7362664349930556192")
    assert res["queued"] == 1 and res["rejected"] == 0
    pending = vm.inbox.pending()
    assert [i.url for i in pending] == ["https://www.tiktok.com/music/sound-7362664349930556192"]
    assert pending[0].source == "manual"  # distinguishable from relay/pack items


def test_extracts_links_from_messy_text(tmp_path):
    """Real pastes carry chat text, newlines and trailing punctuation around the link."""
    vm = _vm(tmp_path)
    text = (
        "omg listen to this (https://vm.tiktok.com/ZP8Gpb8VC/).\n"
        "and this one too: https://www.instagram.com/reel/Cxyz123/\n"
        "tiktok.com/@user/video/7661730574347848973"  # bare host, no scheme
    )
    res = vm.add_manual_links(text)
    assert res["queued"] == 3, res
    urls = {i.url for i in vm.inbox.pending()}
    assert "https://vm.tiktok.com/ZP8Gpb8VC/" in urls  # trailing ")." stripped
    assert "https://tiktok.com/@user/video/7661730574347848973" in urls  # scheme added


def test_is_idempotent(tmp_path):
    vm = _vm(tmp_path)
    url = "https://www.tiktok.com/music/sound-111"
    assert vm.add_manual_links(url)["queued"] == 1
    second = vm.add_manual_links(url)
    assert second["queued"] == 0 and second["skipped"] == 1
    assert len(vm.inbox.pending()) == 1  # no duplicate row


def test_rejects_ssrf_and_offplatform_hosts(tmp_path):
    vm = _vm(tmp_path)
    res = vm.add_manual_links(
        "http://127.0.0.1:8080/admin\n"
        "http://169.254.169.254/latest/meta-data/\n"   # cloud metadata SSRF
        "http://localhost/x\n"
        "https://evil.example.com/track.mp3"           # off-platform
    )
    assert res["queued"] == 0
    assert res["rejected"] == 4
    assert vm.inbox.pending() == []


def test_reports_when_no_links_present(tmp_path):
    vm = _vm(tmp_path)
    res = vm.add_manual_links("just some words, no links here")
    assert res["queued"] == 0 and res["reason"]
    assert vm.inbox.pending() == []


def test_mixed_batch_counts_each_outcome(tmp_path):
    vm = _vm(tmp_path)
    vm.add_manual_links("https://www.tiktok.com/music/sound-1")  # pre-existing
    res = vm.add_manual_links(
        "https://www.tiktok.com/music/sound-1\n"   # skipped (already queued)
        "https://www.tiktok.com/music/sound-2\n"   # queued
        "http://127.0.0.1/x"                       # rejected
    )
    assert (res["queued"], res["skipped"], res["rejected"]) == (1, 1, 1)
