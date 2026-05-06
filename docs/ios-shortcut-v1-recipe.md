# Sound Vault iOS Shortcut — V1 Recipe

This is the first manual Shortcut shape for testing the pairing-code relay.

## Inputs

- Share Sheet input: URL
- User-provided setting: `PAIR_CODE`, e.g. `RIVER-7421`
- Relay URL: placeholder until deployed, e.g. `https://sound-vault-relay.example.com`

## Shortcut actions

1. **Receive URLs from Share Sheet**
   - Types: URLs

2. **Get URLs from Input**
   - Use the first URL for V1.

3. **Text**

```json
{
  "pair_code": "RIVER-7421",
  "url": "Shortcut Input URL here",
  "source": "ios_shortcut"
}
```

4. **Get Contents of URL**
   - URL: `https://relay-host.example.com/v1/inbox/submit`
   - Method: `POST`
   - Headers:
     - `Content-Type: application/json`
   - Request Body: JSON
     - `pair_code`: `RIVER-7421`
     - `url`: shared URL
     - `source`: `ios_shortcut`

5. **Show Notification**

```text
Sent to Sound Vault
```

## Test success

A successful relay submit returns:

```json
{ "id": "in_...", "status": "queued" }
```

Then the desktop app polls:

```http
GET /v1/inbox/poll?pair_code=RIVER-7421
X-Device-Id: dev_...
X-Device-Secret: ...
```

The desktop writes the pulled URL into:

```text
{vault}/inbox/urls/shortcut-inbox.jsonl
```

## Why this avoids accounts

The Shortcut only knows a pairing code. The desktop owns the device secret. The relay stores only URLs temporarily and never stores media files or a user library.

## Later polish

- Generate QR code for pairing code + relay host.
- Shortcut imports config from QR/deep link.
- Add local LAN endpoint fallback when phone and desktop are on the same network.
- Add “sent / already queued / invalid URL” user messages.
