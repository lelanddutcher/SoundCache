from __future__ import annotations

from sound_vault.ingest.deeplink import parse_soundcache_url
from sound_vault.url_safety import is_safe_public_url


def test_parses_ingest_link_and_synthesizes_music_url():
    link = parse_soundcache_url(
        "soundcache://ingest?sound_id=7466169526166637358&title=Battle%20Sports&artist=Battle%20Sports"
    )
    assert link is not None
    assert link.sound_id == "7466169526166637358"
    assert link.title == "Battle Sports"
    assert link.music_url == "https://www.tiktok.com/music/battle-sports-7466169526166637358"
    # The synthesized URL must survive the import-side SSRF/host guard.
    assert is_safe_public_url(link.music_url)


def test_missing_title_falls_back_to_a_valid_slug():
    link = parse_soundcache_url("soundcache://ingest?sound_id=123456")
    assert link is not None
    assert link.music_url.endswith("-123456")
    assert "/music/-" not in link.music_url  # never slug-less


def test_rejects_wrong_scheme_action_and_nonnumeric_id():
    assert parse_soundcache_url("https://www.tiktok.com/music/x-1") is None
    assert parse_soundcache_url("soundcache://settings?x=1") is None
    assert parse_soundcache_url("soundcache://ingest?sound_id=not-a-number") is None
    assert parse_soundcache_url("soundcache://ingest") is None
    assert parse_soundcache_url("") is None


def test_accepts_path_form_without_authority():
    link = parse_soundcache_url("soundcache:ingest?sound_id=999")
    assert link is not None and link.sound_id == "999"
