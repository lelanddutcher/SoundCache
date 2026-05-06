# iOS Shortcut Contract — V1

## Share flow

Shortcut receives a TikTok URL from the iOS share sheet and POSTs it to the relay.

```http
POST /v1/inbox/submit
Content-Type: application/json

{
  "pair_code": "RIVER-7421",
  "url": "https://www.tiktok.com/t/abc123/",
  "source": "ios_shortcut"
}
```

Response:

```json
{ "id": "in_xxx", "status": "queued" }
```

## Desktop polling

```http
GET /v1/inbox/poll?pair_code=RIVER-7421
X-Device-Id: dev_xxx
X-Device-Secret: [local secret]
```

Response:

```json
{
  "items": [
    {
      "id": "in_xxx",
      "url": "https://www.tiktok.com/t/abc123/",
      "source": "ios_shortcut"
    }
  ]
}
```

## Shortcut UX

Start simple:

1. Ask for pairing code on first run.
2. Store pairing code in Shortcut variable/config.
3. Send current share URL to relay.
4. Show notification: `Sent to Sound Vault`.

Later:

- QR pairing
- multiple vaults/devices
- local LAN fallback
