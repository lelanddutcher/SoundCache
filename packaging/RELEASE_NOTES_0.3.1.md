# Sound Cache 0.3.1 — import reliability hotfix

**This release fixes the import pipeline end to end. If your TikTok sounds were failing to download or quietly not showing up in your vault, this is the fix.**

## The headline fix

**TikTok imports now actually land in your vault.** On a network-mounted vault (NFS/SMB — e.g. a NAS), the app captured a sound correctly but then validated it *on the mount*, where filesystem caching handed back a truncated stub — so a perfectly good download was rejected as an "unplayable file" and nothing imported. Capture and validation now happen on local disk; only the finished audio is written to the vault. This was the root cause behind "it says it imported but the sound isn't there."

## Fixed

- **No more silently-lost sounds.** A shared sound is now recorded to a durable, append-only receipt ledger the moment the relay delivers it — before anything else — so a crash or a hiccup can never lose it. The queue keeps a real chain of custody.
- **Failed imports stay visible and retryable.** Nothing is dropped from the queue on failure; each failed item shows its error and can be retried.
- **No false "duplicate" skips.** A half-finished or empty vault folder is no longer mistaken for an already-imported sound (which previously consumed the retry with nothing saved).
- **Moved/renamed vaults work.** Sounds are recognized by the audio actually on disk, so a vault that moved (e.g. an old absolute path) no longer reads as "not imported."
- **Reliable capture.** The TikTok capture retries with backoff instead of one-shot failing, and a capture that yields no real audio fails cleanly instead of writing a bad file.

## New

- **"Refresh inbox" now reconciles your relay against your vault.** It pulls anything still waiting on the relay, verifies every delivered sound actually landed, and re-queues whatever's missing — with a clear summary of what it found and recovered.
- **Recovery of stranded and phantom sounds.** The reconcile pass finds sounds that were delivered but never landed (including older losses) and re-queues them for you.

## Notes

- Sounds that were **deleted from TikTok** before you imported them can't be captured (there's nothing left to fetch); they now stay clearly failed rather than retrying forever.
- yt-dlp is bundled at the current release; note that TikTok's own extractor is upstream-broken, so TikTok sounds always use the built-in authenticated capture path (this is expected).

---

_Requires macOS 12+ (Apple Silicon). Signed with Developer ID, notarized, and stapled._
