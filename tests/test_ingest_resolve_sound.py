import json

from sound_vault.ingest.resolve import (
    ResolvedSource,
    _music_id_from_ssr_html,
    resolve,
    tiktok_sound_url_resolver,
)
from sound_vault.ingest.service import IngestService

from test_ingest_service import FakeDownloader, make_service


# ---- A: SSR parse (pure) ----------------------------------------------------

def _ssr_html(music_id):
    payload = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {"itemStruct": {
        "music": {"id": str(music_id), "title": "Song", "authorName": "Artist"}}}}}}
    return f'<html><body><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(payload)}</script></body></html>'


def test_ssr_parse_extracts_music_id():
    assert _music_id_from_ssr_html(_ssr_html(7355555555555555555)) == "7355555555555555555"


def test_ssr_parse_none_when_no_rehydration_script():
    # /photo/ slideshows + JS shells have no SSR payload.
    assert _music_id_from_ssr_html("<html><body>no script here</body></html>") is None


def test_ssr_parse_none_when_music_id_missing():
    assert _music_id_from_ssr_html('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{"__DEFAULT_SCOPE__":{}}</script>') is None


def test_sound_url_resolver_prefers_ssr_over_oembed(monkeypatch):
    import sound_vault.ingest.resolve as R
    monkeypatch.setattr(R, "tiktok_music_id_via_ssr", lambda u: "999")
    monkeypatch.setattr(R, "tiktok_music_url_via_oembed", lambda u: "https://www.tiktok.com/music/from-oembed-111")
    assert tiktok_sound_url_resolver("https://x/video/1") == "https://www.tiktok.com/music/sound-999"


def test_sound_url_resolver_falls_back_to_oembed(monkeypatch):
    import sound_vault.ingest.resolve as R
    monkeypatch.setattr(R, "tiktok_music_id_via_ssr", lambda u: None)
    monkeypatch.setattr(R, "tiktok_music_url_via_oembed", lambda u: "https://www.tiktok.com/music/x-222")
    assert tiktok_sound_url_resolver("https://x/video/1") == "https://www.tiktok.com/music/x-222"


def test_resolve_video_uses_ssr_resolver_to_reach_the_sound():
    r = resolve(
        "https://www.tiktok.com/@u/video/123",
        resolver=lambda u: (u, None),
        music_resolver=lambda u: "https://www.tiktok.com/music/cool-999",
    )
    assert r.kind == "music" and r.music_id == "999"
    assert r.canonical_url == "https://www.tiktok.com/music/cool-999"


# ---- B2: the service adopts the capture-resolved sound id -------------------

def _tt_video(url, video_id="POST123"):
    return ResolvedSource(
        input_url=url, final_url=url, platform="tiktok", kind="video",
        canonical_url=url, source_id=video_id, slug=None, title_guess=None,
        share_music_id=None, status="ok",
    )


def test_submitted_video_is_recatalogued_under_the_sound(tmp_path):
    # A user submits a VIDEO; the capture navigates to the sound and returns sound_id.
    # The ingest must key/name/catalog under the SOUND, not the post.
    url = "https://www.tiktok.com/@u/video/POST123"
    dl = FakeDownloader(info={"id": "POST123", "title": "Real Sound", "uploader": "Real Artist", "sound_id": "SND999"})
    svc = make_service(tmp_path, dl, resolve_map={url: _tt_video(url)})

    out = svc.ingest_url(url)

    assert out.status == "ingested"
    assert out.music_id == "SND999"                      # keyed to the SOUND
    assert out.folder is not None and out.folder.name.startswith("SND999 -")
    rows = [json.loads(l) for l in (tmp_path / "catalog" / "sounds.jsonl").read_text().splitlines() if l.strip()]
    assert rows[0]["tiktok_music_id"] == "SND999"
    assert rows[0]["tiktok_visible_title"] == "Real Sound"
    assert rows[0]["canonical_url"] == "https://www.tiktok.com/music/sound-SND999"


def test_adopt_is_noop_for_direct_music_share(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    music = ResolvedSource(
        input_url="u", final_url="u", platform="tiktok", kind="music",
        canonical_url="https://www.tiktok.com/music/x-555", source_id="555", slug="x",
        title_guess="x", share_music_id=None, status="ok",
    )

    class _Dl:  # download.info carries a sound_id equal to the id -> no re-key
        info = {"sound_id": "555"}

    assert svc._adopt_resolved_sound(music, _Dl(), "555") is None


def test_adopt_is_noop_for_non_tiktok(tmp_path):
    svc = make_service(tmp_path, FakeDownloader())
    ig = ResolvedSource(
        input_url="u", final_url="u", platform="instagram", kind="video",
        canonical_url="https://www.instagram.com/reel/ABC", source_id="ABC", slug=None,
        title_guess=None, share_music_id=None, status="ok",
    )

    class _Dl:
        info = {"sound_id": "999"}

    assert svc._adopt_resolved_sound(ig, _Dl(), "ABC") is None


# ---- C+D: prefer the platform-identified sound's title/artist --------------

def test_title_artist_prefers_track_over_caption_for_instagram():
    ig = ResolvedSource(
        input_url="u", final_url="u", platform="instagram", kind="video",
        canonical_url="u", source_id="ABC", slug=None, title_guess=None,
        share_music_id=None, status="ok",
    )
    info = {"title": "check out my reel", "uploader": "some_creator", "track": "Original Sound", "artist": "the_musician"}
    title, artist = IngestService._title_artist(ig, info)
    assert title == "Original Sound"     # the SOUND, not the reel caption
    assert artist == "the_musician"      # the sound's artist, not the poster
