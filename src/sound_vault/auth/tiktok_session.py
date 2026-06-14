from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CaptureAggressiveness(str, Enum):
    METADATA_ONLY = "metadata_only"
    ARTWORK = "artwork"
    PREVIEW_AUDIO = "preview_audio"
    FULL_AUDIO = "full_audio"
    ASSOCIATED_VIDEOS = "associated_videos"


@dataclass(frozen=True)
class TikTokSessionProbe:
    status: str
    tested_url: str
    final_url: str
    title: str
    message: str = ""


@dataclass(frozen=True)
class CaptureDecision:
    allowed: bool
    session_required: bool
    message: str
    stop_reason: str = ""


def validate_capture_request(aggressiveness: CaptureAggressiveness | str, *, session_probe: TikTokSessionProbe | None) -> CaptureDecision:
    mode = aggressiveness if isinstance(aggressiveness, CaptureAggressiveness) else CaptureAggressiveness(str(aggressiveness))
    if mode is CaptureAggressiveness.METADATA_ONLY:
        return CaptureDecision(allowed=True, session_required=False, message="Metadata-only import does not require TikTok login.")
    if mode is CaptureAggressiveness.ARTWORK:
        # Artwork can often be public, but if caller supplies an auth probe honor it.
        if session_probe is None:
            return CaptureDecision(allowed=True, session_required=False, message="Artwork-only mode may run without login; authenticated fallback can be requested later.")
    if session_probe is None:
        return CaptureDecision(
            allowed=False,
            session_required=True,
            message="TikTok login/session validation is required before audio or associated-video capture. Open browser login and validate one music URL first.",
        )
    if session_probe.status in {"captcha", "checkpoint"}:
        return CaptureDecision(
            allowed=False,
            session_required=True,
            message="TikTok reported a CAPTCHA/checkpoint. Stop; do not batch capture or retry aggressively.",
            stop_reason="checkpoint",
        )
    if session_probe.status != "ok":
        return CaptureDecision(
            allowed=False,
            session_required=True,
            message=session_probe.message or "TikTok session probe failed on the test music URL.",
            stop_reason=session_probe.status or "probe_failed",
        )
    return CaptureDecision(allowed=True, session_required=True, message="TikTok session validated on one music URL; slow resumable capture may proceed.")
