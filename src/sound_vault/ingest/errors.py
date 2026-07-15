"""Turn a raw ingest/download failure into a short, honest, non-scary message.

Third-party errors (yt-dlp / TikTok) read as alarming out of context — TikTok answers
a private or removed post with "Your IP address is blocked from accessing this post",
which sounds like an account ban but just means the post isn't available to the
requester. The full raw error is always kept (inbox tooltip + right-click "Copy error")
for diagnostics; this is only the friendly one-liner shown at a glance.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FriendlyFailure:
    short: str
    likely_unavailable: bool  # the sound looks gone/private/geo — not a Sound Cache problem


# Substrings (matched case-insensitively) that mean the SOUND is unavailable — private,
# removed, or region-locked — i.e. nothing Sound Cache can fix. Kept specific so a
# transient glitch isn't mislabeled; anything unmatched gets the neutral fallback.
_UNAVAILABLE = (
    "ip address is blocked",  # TikTok's phrasing for a private/removed post
    "is private",
    "this post is private",
    "video is private",
    "item doesn't exist",
    "video unavailable",
    "video currently unavailable",
    "content isn't available",
    "no longer available",
    "not available in your",
    "region-locked",
    "region-blocked",
    "geo-block",
    "no playable audio",  # our own Playwright-fallback message (region-locked / removed / no preview)
    "10202",
    "10203",
    "10204",
    "10216",  # TikTok status codes for missing/private items
)

_TRANSIENT = (
    "timed out",
    "timeout",
    "temporarily",
    "rate limit",
    "too many requests",
    "connection reset",
    "reset by peer",
    "try again later",
    "network is unreachable",
)

_UNAVAILABLE_MSG = (
    "No longer available on TikTok — it may be private, deleted, or region-locked. "
    "Right-click ▸ Open in browser to check."
)
_TRANSIENT_MSG = "Couldn’t reach TikTok right now (temporary) — try Download & import again."
_GENERIC_MSG = "Import failed — right-click ▸ Copy error for details, or Open in browser to check the link."


def humanize_failure(raw: str) -> FriendlyFailure:
    """Map a raw failure string to a friendly one-liner + whether it looks unavailable."""
    text = (raw or "").lower()
    if not text.strip():
        return FriendlyFailure("Import failed.", False)
    if any(marker in text for marker in _UNAVAILABLE):
        return FriendlyFailure(_UNAVAILABLE_MSG, True)
    if any(marker in text for marker in _TRANSIENT):
        return FriendlyFailure(_TRANSIENT_MSG, False)
    return FriendlyFailure(_GENERIC_MSG, False)
