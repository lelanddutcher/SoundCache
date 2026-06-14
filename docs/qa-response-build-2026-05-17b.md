# QA Response Build 2026-05-17b

This build supersedes `20260517a` after a live public oEmbed smoke found an HTTPS certificate validation failure on the Python.org runtime.

## Fix

- Added `certifi` as a runtime dependency.
- Updated `sound_vault.workers.oembed` to use the packaged CA bundle for HTTPS requests.
- Rebuilt the Mac launcher so live public oEmbed enrichment works in the installed wheel environment.

## Artifacts

Preferred tarball:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260517b.tar.gz
sha256: 7d9641a3c3dd7b567a91f6a9dd7a3203e316db36d2d9ff5307988d15c9a85a24
```

Fallback zip:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260517b.zip
sha256: d0a61f04d8eadcbf5b1989d1438bd8c144b0eed6d98c912fad582ad3e449b9a9
```

Wheel:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: fb38f8f512e69d912c6cdd7513086a1a2b095be2fe36114705974b71651a9565
```

## Verification

- `pytest -q`: 165 passed.
- `git diff --check`: passed.
- Targeted import/oEmbed/package tests: 12 passed.
- Source live oEmbed smoke:
  - 1 imported favorite-sound row.
  - 1 oEmbed OK.
  - Returned title: `♬  - SuzuhaYumi`.
- Installed-wheel live oEmbed smoke:
  - 1 imported favorite-sound row.
  - 1 oEmbed OK.
  - Returned title: `♬  - SuzuhaYumi`.
- Installed-wheel real-vault diagnostics passed.
- Installed-wheel real-vault CLI smoke loaded 2,036 records.
- Installed-wheel offscreen Qt smoke opened the app with 5 views.
- Launcher zip/tar, Info.plist, executable mode, and bundled wheel verified.

## Explicit Limits

- No TikTok audio/artwork/video download worker is enabled in this build.
- Metadata-only packages are expected and valid.
- Offline transcription exists as `scripts/transcribe_audio.py` and optional `sound-vault-desktop[asr]`, but the Mac launcher installs `[gui]` only.
- Authenticated enrichment, local ASR bundling, OCR recovery, source recognition, and full phase-by-phase QA are still future work.
