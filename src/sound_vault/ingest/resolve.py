"""Resolve and classify shared social-media URLs before ingest.

Ported and generalized from the organizer's normalize_tiktok_sound_url.py.
Pure stdlib: it follows redirects to a canonical URL, classifies the platform,
and extracts a best-effort source id (TikTok music id, video id, etc.). The
downloader/packager finalize the rest.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from sound_vault.url_safety import is_safe_public_url, is_safe_to_fetch


def _build_ssl_context() -> ssl.SSLContext | None:
    """Use certifi's CA bundle when available.

    The macOS framework Python ships without a usable CA store, so stdlib
    urllib raises CERTIFICATE_VERIFY_FAILED without this. Falls back to the
    default context elsewhere.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - any certifi/ssl problem falls back to default
        return None


_SSL_CONTEXT = _build_ssl_context()

_MUSIC_RE = re.compile(r"/music/(?P<slug>.*?)-(?P<music_id>\d+)(?:$|[/?#])")
_TT_VIDEO_RE = re.compile(r"/video/(?P<video_id>\d+)")
_YT_WATCH_RE = re.compile(r"[?&]v=(?P<id>[A-Za-z0-9_-]{6,})")
_YT_SHORT_RE = re.compile(r"youtu\.be/(?P<id>[A-Za-z0-9_-]{6,})")
_YT_SHORTS_RE = re.compile(r"/shorts/(?P<id>[A-Za-z0-9_-]{6,})")
_IG_RE = re.compile(r"/(?:reel|reels|p|tv)/(?P<id>[A-Za-z0-9_-]+)")

Resolver = Callable[[str], "tuple[str | None, str | None]"]


@dataclass(frozen=True)
class ResolvedSource:
    input_url: str
    final_url: str | None
    platform: str  # tiktok | instagram | youtube | other | unknown
    kind: str  # music | video | unknown
    canonical_url: str | None
    source_id: str | None
    slug: str | None
    title_guess: str | None
    share_music_id: str | None
    status: str  # ok | error
    error: str | None = None

    @property
    def music_id(self) -> str | None:
        if self.platform == "tiktok" and self.kind == "music":
            return self.source_id
        return None


def classify_platform(url: str) -> str:
    netloc = urllib.parse.urlparse((url or "").strip()).netloc.lower()
    if not netloc:
        return "unknown"
    host = netloc.split("@")[-1].split(":")[0]
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
        return "youtube"
    return "other"


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects to non-web schemes or private/internal hosts (SSRF)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        if not is_safe_public_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "refused unsafe redirect target", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener():
    handlers = [_SafeRedirectHandler()]
    if _SSL_CONTEXT is not None:
        handlers.append(urllib.request.HTTPSHandler(context=_SSL_CONTEXT))
    return urllib.request.build_opener(*handlers)


_OPENER = _build_opener()


def resolve_url(url: str) -> tuple[str | None, str | None]:
    # SSRF defense-in-depth: the relay already gates submissions, but anything
    # reaching here (incl. local/manual imports) is checked before we fetch.
    if not is_safe_to_fetch(url):
        return None, "URLError: refused unsafe URL (non-web scheme or private/internal host)"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _OPENER.open(request, timeout=25) as response:  # nosec B310 - validated http(s), public host, safe-redirect handler
            final = response.geturl()
        if final and not is_safe_public_url(final):
            return None, "URLError: refused unsafe redirect target"
        return final, None
    except Exception as exc:  # noqa: BLE001 - report any resolution failure verbatim
        return None, f"{type(exc).__name__}: {exc}"


def _strip_query(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _error(input_url: str, platform: str, final: str | None, message: str | None) -> ResolvedSource:
    return ResolvedSource(
        input_url=input_url,
        final_url=final,
        platform=platform,
        kind="unknown",
        canonical_url=None,
        source_id=None,
        slug=None,
        title_guess=None,
        share_music_id=None,
        status="error",
        error=message,
    )


def resolve(url: str, *, resolver: Resolver = resolve_url) -> ResolvedSource:
    raw = (url or "").strip()
    platform = classify_platform(raw)
    if not raw:
        return _error(url or "", platform, None, "empty url")

    final, err = resolver(raw)
    if err or not final:
        return _error(raw, platform, final, err or "no final url")

    final_platform = classify_platform(final)
    if final_platform != "unknown":
        platform = final_platform

    parsed = urllib.parse.urlparse(final)
    share_music_id = (urllib.parse.parse_qs(parsed.query).get("share_music_id") or [None])[0]

    if platform == "tiktok":
        music = _MUSIC_RE.search(parsed.path.rstrip("/") + "/")
        if music:
            slug = urllib.parse.unquote(music.group("slug"))
            return ResolvedSource(
                input_url=raw,
                final_url=final,
                platform="tiktok",
                kind="music",
                canonical_url=_strip_query(final),
                source_id=music.group("music_id"),
                slug=slug,
                title_guess=slug.replace("-", " ").strip(),
                share_music_id=share_music_id,
                status="ok",
            )
        video = _TT_VIDEO_RE.search(parsed.path)
        return ResolvedSource(
            input_url=raw,
            final_url=final,
            platform="tiktok",
            kind="video",
            canonical_url=_strip_query(final),
            source_id=video.group("video_id") if video else None,
            slug=None,
            title_guess=None,
            share_music_id=share_music_id,
            status="ok",
        )

    if platform == "youtube":
        source_id = None
        for pattern in (_YT_WATCH_RE, _YT_SHORT_RE, _YT_SHORTS_RE):
            match = pattern.search(final)
            if match:
                source_id = match.group("id")
                break
        return ResolvedSource(
            input_url=raw,
            final_url=final,
            platform="youtube",
            kind="video",
            canonical_url=final,
            source_id=source_id,
            slug=None,
            title_guess=None,
            share_music_id=None,
            status="ok",
        )

    if platform == "instagram":
        match = _IG_RE.search(parsed.path)
        return ResolvedSource(
            input_url=raw,
            final_url=final,
            platform="instagram",
            kind="video",
            canonical_url=final,
            source_id=match.group("id") if match else None,
            slug=None,
            title_guess=None,
            share_music_id=None,
            status="ok",
        )

    return ResolvedSource(
        input_url=raw,
        final_url=final,
        platform=platform,
        kind="video",
        canonical_url=final,
        source_id=None,
        slug=None,
        title_guess=None,
        share_music_id=None,
        status="ok",
    )
