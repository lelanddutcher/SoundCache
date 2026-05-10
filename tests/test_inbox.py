from sound_vault.relay.inbox import InboxStore


def test_submitted_link_is_delivered_once_to_device():
    store = InboxStore(now=lambda: 1000.0)
    store.register_device(device_id="dev_1", device_secret="secret")
    store.register_pair_code("RIVER-7421", device_id="dev_1")

    item = store.submit_link(
        pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut"
    )
    delivered = store.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421")

    assert [d.url for d in delivered] == [item.url]
    assert store.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421") == []


def test_wrong_device_secret_cannot_poll_links():
    store = InboxStore(now=lambda: 1000.0)
    store.register_device(device_id="dev_1", device_secret="secret")
    store.register_pair_code("RIVER-7421", device_id="dev_1")
    store.submit_link(pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")

    assert store.poll(device_id="dev_1", device_secret="wrong", pair_code="RIVER-7421") == []


def test_pair_code_acceptance_default_ttl_is_one_day():
    now = {"value": 1000.0}
    store = InboxStore(now=lambda: now["value"])
    store.register_pair_code("RIVER-2345-6789", device_id="dev_1")

    now["value"] = 1000.0 + (24 * 60 * 60)
    assert store.can_accept_pair_code("RIVER-2345-6789") is True

    now["value"] += 1
    assert store.can_accept_pair_code("RIVER-2345-6789") is False


def test_pair_code_acceptance_expires_independently_of_items():
    now = {"value": 1000.0}
    store = InboxStore(now=lambda: now["value"], pair_code_ttl_seconds=10)
    store.register_pair_code("RIVER-7421", device_id="dev_1")

    assert store.can_accept_pair_code("river-7421") is True

    now["value"] = 1011.0

    assert store.can_accept_pair_code("RIVER-7421") is False
