# Save to Sound Cache — iOS Shortcut

`SoundCache.unsigned.plist` is a **reference** of the exact WorkflowKit structure
(Share Sheet action → **Ask for Input** note prompt → `POST /v1/inbox/submit`).
Regenerate it with your relay URL + pair code:

```bash
python scripts/build_ios_shortcut.py --relay https://your-relay.vercel.app --pair-code RIVER-7421
```

iOS 15+ only imports shortcuts signed through the Shortcuts app, so this plist is not
directly importable. **Build the Shortcut by hand** with the recipe in
[docs/ios-shortcut-v1-recipe.md](../../docs/ios-shortcut-v1-recipe.md) (≈2 minutes),
then **Share → Copy iCloud Link** to get a one-tap install link you can put on the
landing page or send to friends.

The Shortcut prompts for an optional **note/label** at share time, which lands in the
app's searchable **User notes** field. The key setting that makes it appear in
TikTok/Instagram/YouTube/etc.: in the Shortcut's details, enable **Show in Share
Sheet** with **URLs** + **Text** accepted.
