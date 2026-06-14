# Build the "Save to Sound Cache" iOS Shortcut

This Shortcut appears in the **Share Sheet of every app that shares a link** —
TikTok, Instagram, YouTube, X, Reddit, anything — prompts you for an optional
**note/label**, and POSTs the shared URL (plus your note) to your relay. iOS 15+
only imports shortcuts signed through the Shortcuts app, so build it once by hand
(≈2 minutes); then you can Share → Export to distribute it. The exact structure is
also generated as a reference at `web/shortcut/SoundCache.unsigned.plist`
(`python scripts/build_ios_shortcut.py`).

## Prerequisites
- The relay URL (your `*.vercel.app` URL once deployed, or `http://<mac-ip>:43117` on LAN for testing).
- A pairing code from the desktop app: **Settings → Create pairing code** (e.g. `RIVER-7421`).

## Build it (Shortcuts app → ＋ New Shortcut)

1. **Receive what's shared.** Tap the shortcut's **ⓘ / Details** → enable **Show in Share Sheet**. Under **Share Sheet Types**, turn ON **URLs** and **Text** (turn the rest off). *This is what makes it show up in TikTok/Instagram/etc.*

2. **Add action: "Ask for Input".** *This is the comment/label prompt.*
   - **Prompt:** `Label or notes for this sound? (optional)`
   - **Input Type:** `Text`
   - Tap **Show More** → turn **Allow Multiple Lines** ON.
   - (Optional) rename its output to `Sound note` so it's easy to find in the next step.
   - Leave the answer optional — submitting blank is fine.

3. **Add action: "Get Contents of URL".**
   - URL: `https://<your-relay>/v1/inbox/submit`
   - Tap **Show More**:
     - **Method:** `POST`
     - **Headers:** add `Content-Type` = `application/json`
     - **Request Body:** `JSON`, add four fields:
       - `pair_code` (Text) = `YOUR-PAIR-CODE`
       - `url` (Text) = the **Shortcut Input** magic variable (tap the field → Select Variable → **Shortcut Input**)
       - `source` (Text) = `ios_shortcut`
       - `note` (Text) = the **Provided Input** / `Sound note` magic variable from step 2 (tap the field → Select Variable → the Ask for Input output)

4. **Add action: "Show Notification"** → text: `Saved to Sound Cache ✨`. (Optional but nice.)

5. **Name it** "Save to Sound Cache" and pick a fun glyph/color.

## What the note does
Whatever you type at share time lands in the app under **User notes** (right-hand
inspector, below the Transcript), and is **searchable** from the main search box.
You can also edit notes on the fly as you browse your vault — they autosave. Leave
the prompt blank and nothing is attached.

## Test it
- Open TikTok (or any app) → a sound/post → **Share** → **Save to Sound Cache**.
- Type an optional note when prompted, then confirm.
- You should see the notification. A successful relay submit returns `{ "id": "in_…", "status": "queued" }`.
- On the desktop, the link downloads automatically if the background agent is running
  (`sound-vault-agent install`) or when you hit **Download & import** — see
  [background-fetch.md](background-fetch.md). Your note shows up under **User notes**.

## Share / back it up
With the Shortcut open: **Share → Copy iCloud Link** (or **Export → Save to Files** for a
`.shortcut`). That link/file is how you reinstall it or share it with others.

## Why this stays account-free
The Shortcut only knows a pairing code. The desktop owns the device secret. The relay
stores only URLs + your note briefly and never media or a user library.

## Later polish
- QR pairing (scan to fill relay URL + pair code).
- A LAN fallback endpoint when phone + desktop are on the same network.
- "sent / already queued / invalid URL" branches in the Shortcut.
