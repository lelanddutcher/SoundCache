# Sound Vault QA response build - 2026-05-15j

This note records the right-inspector TikTok sound-page button added after Leland asked for a direct way to open the original TikTok sound URL and inspect current trending videos under that sound.

## metadata verification

The real vault already contains sound-page URL metadata:

```text
catalog canonical_url: 2,063 rows
catalog mobile_music_url: 2,063 rows
metadata canonical_url: 2,036 packaged sounds
metadata mobile_music_url: 2,036 packaged sounds
```

Example resolved URL from the GUI smoke:

```text
https://www.tiktok.com/music/-7623228538451200782
```

## fixed in build 20260515j

- Added `Open TikTok sound` to the right-side inspector.
- The button enables when the selected record has a TikTok sound/music URL.
- URL resolution prefers:
  - `canonical_url`;
  - `source_music_url`;
  - raw `canonical_url`;
  - raw `mobile_music_url`;
  - raw `source_music_url`;
  - raw `music_url`, `tiktok_music_url`, `share_url`, or `url`.
- The button opens the URL through `QDesktopServices.openUrl`.
- `Copy canonical URL` and the library context menu now use the same best-sound-URL resolver.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515j.tar.gz
sha256: ffce84677535aaf11998243a7c0aea3bd28fd4445237982cae3458babd57004c
size: 59,175 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515j.zip
sha256: 295654fd164ec6d45eb79c8b319ac1b52d03bea4b711e193af3fc879753028a3
size: 70,965 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 386e4e47732109784a2a93732417a984cd34ad2a3cf97b0433024da4c99b575e
size: 55,966 bytes
```

## verification

Targeted UI/GUI tests:

```text
pytest -q tests/test_desktop_ui_source.py tests/test_desktop_gui_workflows.py tests/test_redesign_archive_model.py tests/test_index_db.py
53 passed
```

Full suite:

```text
pytest -q
149 passed
```

Additional checks:

```text
git diff --check
passed

py_compile desktop.py / desktop GUI tests / desktop UI source tests
passed
```

Source real-vault TikTok URL smoke:

```text
2036 indexed records
Open TikTok sound enabled: true
resolved URL: https://www.tiktok.com/music/-7623228538451200782
```

Installed-wheel TikTok URL smoke:

```text
2036 indexed records
Open TikTok sound enabled: true
resolved URL: https://www.tiktok.com/music/-7623228538451200782
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel.

## remaining follow-up

- Add an explicit `Open TikTok sound` icon/button to duplicate candidate rows or the duplicate inspector header.
- Add a small URL provenance label showing which field supplied the URL.
