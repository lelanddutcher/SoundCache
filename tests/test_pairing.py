import re

from sound_vault.relay.pairing import PairingRegistry

_PAIR_CODE_PATTERN = re.compile(
    r"^[A-Z]+-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-"
    r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}$"
)


def test_pairing_code_can_be_claimed_once():
    registry = PairingRegistry(now=lambda: 1000.0)
    pair = registry.create_pairing_code(device_name="Studio Mac")

    claimed = registry.claim_pairing_code(pair.code)

    assert claimed.device_id == pair.device_id
    assert claimed.device_secret == pair.device_secret
    assert registry.claim_pairing_code(pair.code) is None


def test_pairing_code_uses_high_entropy_human_readable_format():
    registry = PairingRegistry(now=lambda: 1000.0)

    pair = registry.create_pairing_code(device_name="Studio Mac")

    assert _PAIR_CODE_PATTERN.fullmatch(pair.code)
    assert len(pair.code.split("-")) == 3


def test_pairing_codes_are_unique_across_large_batch():
    registry = PairingRegistry(now=lambda: 1000.0)

    codes = {
        registry.create_pairing_code(device_name=f"Device {idx}").code for idx in range(1000)
    }

    assert len(codes) == 1000


def test_expired_pairing_code_cannot_be_claimed():
    now = {"value": 1000.0}
    registry = PairingRegistry(now=lambda: now["value"], code_ttl_seconds=10)
    pair = registry.create_pairing_code(device_name="Studio Mac")

    now["value"] = 1011.0

    assert registry.claim_pairing_code(pair.code) is None
