"""The import-failure dialog must show every distinct cause, not just the first.

A batch commonly fails for two unrelated reasons at once — yt-dlp's dead TikTok
extractor AND a missing Playwright browser. The old dialog printed one raw, multi-line
reason and hid the rest behind "(+N more)", so the second cause was invisible and the
untruncated text overflowed the window.
"""
from sound_vault.ui.desktop import _failure_remedy, _failure_summary

PLAYWRIGHT_MISSING = (
    "browserType.launch: Executable doesn't exist at /Users/x/Library/Caches/"
    "ms-playwright/chromium_headless_shell-1223/chrome-headless-shell\n"
    "╔════════════════════════════════════════════════════╗\n"
    "║ Looks like Playwright was just installed or updated. ║\n"
    "║ Please run the following command to download new browsers: ║\n"
    "╚════════════════════════════════════════════════════╝"
)


def test_missing_browser_is_summarised_and_has_a_fix():
    summary = _failure_summary(PLAYWRIGHT_MISSING)
    assert summary == "The capture browser is not installed"
    assert "playwright install" in _failure_remedy(summary)


def test_summary_is_one_short_line_not_a_stack():
    summary = _failure_summary(PLAYWRIGHT_MISSING)
    assert "\n" not in summary, "a multi-line summary overflows the dialog"
    assert len(summary) <= 160


def test_dead_extractor_and_missing_browser_are_different_causes():
    """Both appear in one batch; they must not collapse into a single group."""
    ytdlp = "yt-dlp: DownloadError: ERROR: [TikTok] 123: No working app info is available"
    assert _failure_summary(ytdlp) != _failure_summary(PLAYWRIGHT_MISSING)


def test_ascii_box_borders_are_never_used_as_the_summary():
    assert _failure_summary("╔═══════╗\n║ real message here ║") == "real message here"


def test_empty_and_unknown_reasons_degrade_cleanly():
    assert _failure_summary("") == "Unknown error"
    assert _failure_summary("   ") == "Unknown error"
    assert _failure_remedy("Unknown error") == ""
