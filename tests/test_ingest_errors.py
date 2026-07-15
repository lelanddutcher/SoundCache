from sound_vault.ingest.errors import humanize_failure


def test_ip_blocked_reads_as_unavailable_not_scary():
    # The exact real-world error: TikTok's phrasing for a private/removed post.
    raw = (
        "primary: DownloadError: ERROR: [TikTok] 7661730574347848973: Your IP address is "
        "blocked from accessing this post; fallback: no playable audio after retries "
        "(sound may be region-locked, removed, or serve no preview)"
    )
    result = humanize_failure(raw)
    assert result.likely_unavailable is True
    assert "no longer available" in result.short.lower()
    assert "ip address" not in result.short.lower()  # the scary phrase is gone
    assert "open in browser" in result.short.lower()  # points the user to verify


def test_private_video_is_unavailable():
    assert humanize_failure("ERROR: This video is private").likely_unavailable is True


def test_our_playwright_fallback_message_is_unavailable():
    assert humanize_failure("no playable audio after retries").likely_unavailable is True


def test_tiktok_status_code_10204_is_unavailable():
    assert humanize_failure("music detail statusCode 10204").likely_unavailable is True


def test_transient_network_error_is_not_unavailable():
    result = humanize_failure("Connection reset by peer; try again later")
    assert result.likely_unavailable is False
    assert "temporary" in result.short.lower()


def test_unknown_error_gets_neutral_actionable_message():
    result = humanize_failure("something weird happened at line 42")
    assert result.likely_unavailable is False
    assert "copy error" in result.short.lower() or "open in browser" in result.short.lower()


def test_empty_error_is_safe():
    assert humanize_failure("").short == "Import failed."
    assert humanize_failure(None).likely_unavailable is False  # type: ignore[arg-type]
