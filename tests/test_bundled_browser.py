"""The app must use its OWN Chromium, not the shared Playwright cache.

Playwright reads ~/Library/Caches/ms-playwright by default. That cache is not ours: a
fresh Mac has no browser in it, and any other tool that installs a newer Playwright
prunes the revision we need. Either way TikTok capture dies with "Executable doesn't
exist" — which is exactly what happened on 2026-09-07, when an unrelated
chromium-1234 install removed the chromium-1223 this app requires.
"""
import os

from sound_vault.ingest import factory


def test_source_run_leaves_playwright_alone(monkeypatch):
    """Unfrozen, we must not set the variable — a dev run uses the normal shared cache."""
    monkeypatch.delattr(factory.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(factory.sys, "frozen", False, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    factory.ensure_browsers_path()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
    assert factory.bundled_browsers_dir() is None


def test_frozen_app_points_at_its_bundled_browser(monkeypatch, tmp_path):
    (tmp_path / "ms-playwright" / "chromium-1223").mkdir(parents=True)
    monkeypatch.setattr(factory.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(factory.sys, "frozen", True, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    factory.ensure_browsers_path()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "ms-playwright")


def test_a_stale_inherited_value_is_overridden(monkeypatch, tmp_path):
    """An inherited PLAYWRIGHT_BROWSERS_PATH would send the app somewhere with no browser."""
    (tmp_path / "ms-playwright").mkdir()
    monkeypatch.setattr(factory.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(factory.sys, "frozen", True, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/somewhere/else")
    factory.ensure_browsers_path()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "ms-playwright")


def test_frozen_without_a_bundled_browser_does_not_lie(monkeypatch, tmp_path):
    """No bundled dir (e.g. an old build): leave Playwright on its default lookup."""
    monkeypatch.setattr(factory.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(factory.sys, "frozen", True, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    factory.ensure_browsers_path()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ


def test_headless_capture_uses_the_full_chromium_build():
    """channel:"chromium" keeps one bundled browser serving headed login AND headless
    capture. Without it Playwright wants chromium_headless_shell, a second ~190MB
    download that is not bundled — so capture would fail on a clean machine."""
    from pathlib import Path

    for name in ("capture_tiktok_audio.cjs", "capture_usage_count.cjs"):
        text = (Path(__file__).resolve().parent.parent / "scripts" / name).read_text()
        assert 'channel: "chromium"' in text, f"{name} would need the unbundled headless shell"


def test_nested_app_bundles_are_signed_with_entitlements():
    """Bundled Chromium's helper .app bundles must keep our entitlements.

    Its renderer and GPU helpers run V8, which JITs. Under the hardened runtime that is
    killed without allow-jit + allow-unsigned-executable-memory. Signing a nested .app
    WITHOUT --entitlements silently overwrites the entitled signature applied to the inner
    binary earlier in the script, and the headed TikTok login window then dies on open
    (headless capture kept working, which is why it wasn't obvious).
    """
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "packaging" / "sign_and_notarize.sh").read_text()
    bundle_pass = script.split("Signing nested .framework / helper .app bundles", 1)[1]
    app_branch = bundle_pass.split("*.app)", 1)[1].split(";;", 1)[0]
    assert "--entitlements" in app_branch, "nested .app bundles must be signed with entitlements"


def test_login_window_never_touches_coreaudio():
    """The TikTok login window must not initialise Chromium's speech/audio stack.

    Chromium calls +[NSSpeechSynthesizer defaultVoice] during startup, which reaches
    -[BabelFish _monitorAudioDevices] -> AudioObjectAddPropertyListener ->
    HALSystem::Initialize -> mach_msg, and on this machine that never returns. The window
    appears and the UI thread is wedged inside a run-loop callback before the login page
    can paint. A `sample` of the hung process showed 100% of 8028 stacks parked there;
    with these flags it is 0% and the thread sits idle in the AppKit event wait.
    Nothing in a login window speaks or plays audio.
    """
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "scripts" / "tiktok_login.cjs").read_text()
    for flag in ("--disable-speech-api", "--disable-speech-synthesis-api", "--mute-audio"):
        assert flag in text, f"{flag} missing — login window can wedge on CoreAudio"
