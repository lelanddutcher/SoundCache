from sound_vault.ingest.resolve import ResolvedSource, classify_platform, resolve


def _resolver(mapping):
    def fake(url):
        if url in mapping:
            return mapping[url], None
        return url, None

    return fake


def test_classify_platform():
    assert classify_platform("https://www.tiktok.com/music/x-1") == "tiktok"
    assert classify_platform("https://vm.tiktok.com/ZP9F8/") == "tiktok"
    assert classify_platform("https://m.tiktok.com/h5/share/music/1.html") == "tiktok"
    assert classify_platform("https://www.instagram.com/reel/abc/") == "instagram"
    assert classify_platform("https://www.youtube.com/watch?v=abc") == "youtube"
    assert classify_platform("https://youtu.be/abc") == "youtube"
    assert classify_platform("https://example.com/whatever") == "other"
    assert classify_platform("not a url") == "unknown"


def test_resolve_tiktok_music_canonical():
    url = "https://www.tiktok.com/music/Kendall-Toole-Get-Em-Banned-7274985708375378731"
    result = resolve(url, resolver=_resolver({}))
    assert isinstance(result, ResolvedSource)
    assert result.platform == "tiktok"
    assert result.kind == "music"
    assert result.source_id == "7274985708375378731"
    assert result.music_id == "7274985708375378731"
    assert result.slug == "Kendall-Toole-Get-Em-Banned"
    assert result.title_guess == "Kendall Toole Get Em Banned"
    assert result.canonical_url == url
    assert result.status == "ok"


def test_resolve_tiktok_short_link_redirects_to_music():
    short = "https://www.tiktok.com/t/ZP9F8oASkoMeq-4Kezl/"
    final = "https://www.tiktok.com/music/Some-Sound-7459294927097236255?lang=en"
    result = resolve(short, resolver=_resolver({short: final}))
    assert result.platform == "tiktok"
    assert result.kind == "music"
    assert result.source_id == "7459294927097236255"
    assert result.canonical_url == "https://www.tiktok.com/music/Some-Sound-7459294927097236255"
    assert result.final_url == final
    assert result.status == "ok"


def test_resolve_tiktok_slugless_music_url_from_real_catalog():
    # Real catalog rows contain slug-less canonical URLs like /music/-<id>.
    url = "https://www.tiktok.com/music/-7274985708375378731"
    result = resolve(url, resolver=_resolver({}))
    assert result.kind == "music"
    assert result.music_id == "7274985708375378731"
    assert result.slug == ""
    assert result.title_guess == ""
    assert result.status == "ok"


def test_resolve_tiktok_video_url_is_ingestible_as_video():
    url = "https://www.tiktok.com/@creator/video/7011122233344455667"
    result = resolve(url, resolver=_resolver({}))
    assert result.platform == "tiktok"
    assert result.kind == "video"
    assert result.source_id == "7011122233344455667"
    assert result.music_id is None
    assert result.status == "ok"


def test_resolve_youtube_video():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    result = resolve(url, resolver=_resolver({}))
    assert result.platform == "youtube"
    assert result.kind == "video"
    assert result.source_id == "dQw4w9WgXcQ"
    assert result.canonical_url == url
    assert result.status == "ok"


def test_resolve_youtu_be_short():
    url = "https://youtu.be/dQw4w9WgXcQ"
    result = resolve(url, resolver=_resolver({}))
    assert result.platform == "youtube"
    assert result.source_id == "dQw4w9WgXcQ"
    assert result.status == "ok"


def test_resolve_other_platform_passthrough():
    url = "https://www.instagram.com/reel/Cabc123/"
    result = resolve(url, resolver=_resolver({}))
    assert result.platform == "instagram"
    assert result.kind == "video"
    assert result.canonical_url == url
    assert result.status == "ok"


def test_resolve_resolver_error():
    def boom(url):
        return None, "URLError: timeout"

    result = resolve("https://www.tiktok.com/t/abc/", resolver=boom)
    assert result.status == "error"
    assert result.error == "URLError: timeout"
    assert result.platform == "tiktok"


def test_resolve_blank_url():
    result = resolve("   ", resolver=_resolver({}))
    assert result.status == "error"


# --- video/photo → sound resolution (the "send a video, get the sound" path) ---

def test_resolve_tiktok_video_with_share_music_id_resolves_to_sound():
    url = "https://www.tiktok.com/@u/video/7123456789012345678?share_music_id=6689804660171082501"
    result = resolve(url, resolver=_resolver({}))
    assert result.kind == "music"
    assert result.source_id == "6689804660171082501"
    assert result.music_id == "6689804660171082501"  # dedups on the sound, not the video
    assert result.canonical_url == "https://www.tiktok.com/music/sound-6689804660171082501"


def test_resolve_tiktok_video_uses_injected_music_resolver():
    url = "https://www.tiktok.com/@u/video/7123456789012345678"
    seen = {}

    def music_resolver(video_url):
        seen["url"] = video_url
        return "https://www.tiktok.com/music/espresso-7364498342501435408"

    result = resolve(url, resolver=_resolver({}), music_resolver=music_resolver)
    assert seen["url"] == url
    assert result.kind == "music"
    assert result.source_id == "7364498342501435408"
    assert result.slug == "espresso"
    assert result.canonical_url == "https://www.tiktok.com/music/espresso-7364498342501435408"


def test_resolve_tiktok_photo_slideshow_resolves_to_sound():
    url = "https://www.tiktok.com/@u/photo/7223456789012345678"
    result = resolve(url, resolver=_resolver({}),
                     music_resolver=lambda _u: "https://www.tiktok.com/music/x-555")
    assert result.kind == "music"
    assert result.source_id == "555"


def test_resolve_tiktok_video_without_resolver_stays_video():
    url = "https://www.tiktok.com/@u/video/7123456789012345678"
    result = resolve(url, resolver=_resolver({}))  # no music_resolver, no share_music_id
    assert result.kind == "video"
    assert result.source_id == "7123456789012345678"


def test_resolve_dead_short_link_to_home_is_error():
    short = "https://vm.tiktok.com/ZSexpired/"
    result = resolve(short, resolver=_resolver({short: "https://www.tiktok.com/?_r=1"}))
    assert result.status == "error"
    assert "expired" in (result.error or "").lower()


def test_resolve_music_resolver_failure_falls_back_to_video():
    url = "https://www.tiktok.com/@u/video/7123456789012345678"

    def boom(_u):
        raise RuntimeError("oembed down")

    result = resolve(url, resolver=_resolver({}), music_resolver=boom)
    assert result.kind == "video"  # never fatal — degrades to clip capture


def test_oembed_music_regex_extracts_link_from_html():
    from sound_vault.ingest.resolve import _OEMBED_MUSIC_RE, _tiktok_music_url

    html = '<blockquote ...><a href="https://www.tiktok.com/music/original-sound-6689804660171082501?x=1">♬</a>'
    m = _OEMBED_MUSIC_RE.search(html)
    assert m and m.group("music_id") == "6689804660171082501" and m.group("slug") == "original-sound"
    assert _tiktok_music_url("123", "my-slug") == "https://www.tiktok.com/music/my-slug-123"
    assert _tiktok_music_url("123") == "https://www.tiktok.com/music/sound-123"  # never slug-less
