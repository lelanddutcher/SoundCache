# QA Response Build 2026-05-18a

This build adds structured hashtag capture for associated/example videos.

## Fix

- Added shared hashtag extraction in `sound_vault.vault.hashtags`.
- Updated the associated-video capture script at:
  `/Volumes/hermes-share/Projects/Tiktok Sound Organizer/scripts/capture_associated_videos.cjs`.
- New captures write:
  - per-video `hashtags`;
  - manifest-level `hashtags`;
  - manifest-level `associated_video_hashtags`.
- Existing associated-video manifests can be repaired without downloading through:

```bash
PYTHONPATH=src python3 scripts/backfill_associated_videos.py \
  --vault "/Volumes/hermes-share/TikTok Sound Vault" \
  --metadata-only
```

- Metadata repair now promotes associated-video hashtags into each sound's `metadata.json` as:
  - `hashtags`;
  - `associated_video_hashtags`.
- Metadata repair now rebases stale `/nas/...` associated-video MP4 paths into the selected vault folder before deciding a video is missing.
- The desktop index includes hashtags in fast search text.
- The right inspector shows sound-level hashtags, and associated-video notes show per-video hashtags.

## Real-Vault Backfill

The existing vault was repaired once after the stale-path fix:

```text
folders: 2036
updated: 741
with_hashtags: 728
```

## Artifacts

Preferred tarball:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260518a.tar.gz
sha256: 9e1b74dafc846aa2b21f7fb1f8d2de4f9ca1ea638864508432da5cdf9cc45bb9
```

Fallback zip:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260518a.zip
sha256: 4839e53d4d8193a9e1495702e659db4fe74d23bec6ed3ee15337949f856106c0
```

Wheel:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 6666d9408525ee2ba9660f47758a6c4ca8bce22fc680faa80c452b32ab660d1c
```

## Verification

- `pytest -q`: 171 passed.
- Targeted hashtag/backfill/index/UI tests: 72 passed.
- `node --check` passed for the associated-video capture script.
- Source real-vault summary index smoke:
  - 2,036 records.
  - 728 records with metadata hashtags.
- Source real-vault temp SQLite search smoke:
  - searching `capcut` returned 10 rows.
- Installed-wheel diagnostics passed.
- Installed-wheel CLI smoke loaded 2,036 records.
- Installed-wheel hashtag smoke:
  - 728 records with hashtags.
  - `capcut` search returned rows.
- Installed-wheel offscreen Qt rebuild/search smoke:
  - rebuilt 2,036 records.
  - `capcut` search returned 30 rows.
  - 5 views loaded.
- Launcher zip/tar, Info.plist, executable mode, and bundled wheel verified.

## Explicit Limits

- Hashtags are extracted from captured text evidence, not from a TikTok hashtag API.
- The external Playwright capture script is not bundled inside the Mac launcher.
- Existing app caches must rebuild once before hashtag search appears everywhere.
- This build does not add new TikTok audio/artwork/video download capability.
