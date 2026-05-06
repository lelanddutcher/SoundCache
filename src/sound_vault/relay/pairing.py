from __future__ import annotations

from dataclasses import dataclass
import secrets
import string
import time

_WORDS = [
    "RIVER", "ANCHOR", "MIXER", "PIXEL", "VAULT", "FRAME", "SIGNAL", "NOVA",
    "ECHO", "FINDER", "CLIP", "ROUTE", "BEACON", "FADER", "INDEX", "MARKER",
]


@dataclass(frozen=True)
class PairingCode:
    code: str
    device_id: str
    device_secret: str
    device_name: str
    expires_at: float


class PairingRegistry:
    """In-memory pairing registry for the v1 relay.

    Production deployment should back this with SQLite/Postgres/Redis, but the semantics stay
    the same: short-lived code, one-time claim, device secret after pairing.
    """

    def __init__(self, *, now=time.time, code_ttl_seconds: int = 600) -> None:
        self._now = now
        self._code_ttl_seconds = code_ttl_seconds
        self._codes: dict[str, PairingCode] = {}

    def create_pairing_code(self, *, device_name: str) -> PairingCode:
        code = self._new_code()
        pair = PairingCode(
            code=code,
            device_id=f"dev_{secrets.token_urlsafe(18)}",
            device_secret=secrets.token_urlsafe(32),
            device_name=device_name,
            expires_at=self._now() + self._code_ttl_seconds,
        )
        self._codes[code] = pair
        return pair

    def claim_pairing_code(self, code: str) -> PairingCode | None:
        normalized = code.strip().upper()
        pair = self._codes.pop(normalized, None)
        if pair is None:
            return None
        if pair.expires_at < self._now():
            return None
        return pair

    def _new_code(self) -> str:
        for _ in range(20):
            code = f"{secrets.choice(_WORDS)}-{''.join(secrets.choice(string.digits) for _ in range(4))}"
            if code not in self._codes:
                return code
        raise RuntimeError("unable to allocate unique pairing code")
