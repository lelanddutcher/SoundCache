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
