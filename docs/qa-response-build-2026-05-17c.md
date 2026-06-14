# QA Response Build 2026-05-17c

This build implements import-time dedupe for TikTok favorite-sound data exports.

## Fix

- Added vault matching to `sound_vault.importers.tiktok_archive`.
- Existing sounds are detected before packaging imported rows.
- Matching order:
  1. stable TikTok music ID;
  2. normalized canonical TikTok music URL;
  3. normalized mobile/share music URL;
  4. normalized source URL from catalog or per-sound `metadata.json`;
  5. ambiguous URL matches when multiple vault records share the same URL evidence.
- URL matching strips query strings, fragments, and common tracking noise, then adds canonical/mobile variants when a music ID can be extracted.
- The importer scans `catalog/sounds.jsonl`, `sounds/*/metadata.json`, and sound-folder names for durable identity evidence.
- Normalized import JSON/CSV rows now include:
  `vault_match_status`, `vault_match_reason`, `vault_match_music_id`, `vault_match_folder`, and `vault_match_url`.
- Import summaries now include:
  `already_in_vault`, `new_to_vault`, `ambiguous_matches`, and `vault_match_counts`.
- CLI and desktop import summaries now surface existing/new/ambiguous counts.

## Artifacts

Preferred tarball:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260517c.tar.gz
sha256: 4bba9806efa9ecc1ee314954dfc848e5003236a111f9b236611551bf6d7e2497
```

Fallback zip:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260517c.zip
sha256: f4b85cb4d5687c1c6f6baf92d4d5416d2d7f01848e744d190e47168142bc8386
```

Wheel:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: ef5cd700335f690273bd5d0ef22bc913cf69ddf134647aa6fdc03f2c8888152a
```

## Verification

- `pytest -q`: 167 passed.
- Targeted importer/CLI/desktop source tests: 44 passed.
- Source import-dedupe smoke:
  - 2 imported favorite-sound rows.
  - 1 existing vault match.
  - 1 new-to-vault row.
  - statuses: `already_in_vault_by_music_id`, `new_to_vault`.
- Installed-wheel import-dedupe smoke:
  - 2 imported favorite-sound rows.
  - 1 existing vault match.
  - 1 new-to-vault row.
  - statuses: `already_in_vault_by_music_id`, `new_to_vault`.
- Installed-wheel real-vault diagnostics passed.
- Installed-wheel real-vault CLI smoke loaded 2,036 records.
- Installed-wheel offscreen Qt smoke opened the app with 5 views and loaded 2,036 rows from the current index.
- Launcher zip/tar, Info.plist, executable mode, and bundled wheel verified.

## Explicit Limits

- This is metadata identity dedupe, not audio fingerprinting.
- Exact/normalized music ID and TikTok URL matches are treated as strong same-sound evidence.
- Fuzzy title/artist duplicate review remains separate because title/artist alone is too weak for import identity.
- No TikTok audio/artwork/video download worker is enabled in this build.
- Metadata-only packages are expected and valid.
- Offline transcription exists as `scripts/transcribe_audio.py` and optional `sound-vault-desktop[asr]`, but the Mac launcher installs `[gui]` only.
