from __future__ import annotations

from dataclasses import dataclass
import secrets
import time

_WORDS = [
    "RIVER", "ANCHOR", "MIXER", "PIXEL", "VAULT", "FRAME", "SIGNAL", "NOVA",
    "ECHO", "FINDER", "CLIP", "ROUTE", "BEACON", "FADER", "INDEX", "MARKER",
]

_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_GROUPS = 2
_CODE_GROUP_SIZE = 4
# 30 days — matches the inbox submission TTL so the create response's
# advertised expires_at reflects how long the code actually works.
DEFAULT_PAIRING_CODE_TTL_SECONDS = 30 * 24 * 60 * 60


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

    def __init__(self, *, now=time.time, code_ttl_seconds: int = DEFAULT_PAIRING_CODE_TTL_SECONDS) -> None:
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

    def is_pairing_code_active(self, code: str) -> bool:
        normalized = code.strip().upper()
        pair = self._codes.get(normalized)
        if pair is None:
            return False
        if pair.expires_at < self._now():
            self._codes.pop(normalized, None)
            return False
        return True

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
            groups = [
                "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_SIZE))
                for _ in range(_CODE_GROUPS)
            ]
            code = "-".join([secrets.choice(_WORDS), *groups])
            if code not in self._codes:
                return code
        raise RuntimeError("unable to allocate unique pairing code")
