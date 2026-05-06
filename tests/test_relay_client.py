import json

from sound_vault.relay.client import RelayClient, RelayInboxItem


def test_relay_client_writes_polled_links_to_local_inbox(tmp_path):
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return {"items": [{"id": "in_1", "url": "https://www.tiktok.com/t/abc/", "source": "ios_shortcut"}]}

    client = RelayClient(
        base_url="https://relay.example",
        device_id="dev_1",
        device_secret="secret",
        pair_code="RIVER-7421",
        get_json=fake_get,
    )

    items = client.poll_to_inbox(tmp_path / "inbox" / "shortcut-inbox.jsonl")

    assert items == [RelayInboxItem(id="in_1", url="https://www.tiktok.com/t/abc/", source="ios_shortcut")]
    assert calls[0]["headers"]["x-device-id"] == "dev_1"
    written = (tmp_path / "inbox" / "shortcut-inbox.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(written)["url"] == "https://www.tiktok.com/t/abc/"
