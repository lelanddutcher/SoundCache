from sound_vault.relay.inbox import InboxStore


def test_submitted_link_is_delivered_once_to_device():
    store = InboxStore(now=lambda: 1000.0)
    store.register_device(device_id="dev_1", device_secret="secret")

    item = store.submit_link(pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")
    delivered = store.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421")

    assert [d.url for d in delivered] == [item.url]
    assert store.poll(device_id="dev_1", device_secret="secret", pair_code="RIVER-7421") == []


def test_wrong_device_secret_cannot_poll_links():
    store = InboxStore(now=lambda: 1000.0)
    store.register_device(device_id="dev_1", device_secret="secret")
    store.submit_link(pair_code="RIVER-7421", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")

    assert store.poll(device_id="dev_1", device_secret="wrong", pair_code="RIVER-7421") == []
