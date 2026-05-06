from sound_vault.relay.pairing import PairingRegistry


def test_pairing_code_can_be_claimed_once():
    registry = PairingRegistry(now=lambda: 1000.0)
    pair = registry.create_pairing_code(device_name="Studio Mac")

    claimed = registry.claim_pairing_code(pair.code)

    assert claimed.device_id == pair.device_id
    assert claimed.device_secret == pair.device_secret
    assert registry.claim_pairing_code(pair.code) is None


def test_expired_pairing_code_cannot_be_claimed():
    now = {"value": 1000.0}
    registry = PairingRegistry(now=lambda: now["value"], code_ttl_seconds=10)
    pair = registry.create_pairing_code(device_name="Studio Mac")

    now["value"] = 1011.0

    assert registry.claim_pairing_code(pair.code) is None
