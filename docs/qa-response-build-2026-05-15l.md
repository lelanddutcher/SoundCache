# Sound Vault QA response build - 2026-05-15l

This note records the transport update for the requested continuous-play and random-play controls.

## fixed in build 20260515l

- Added a `CONT` transport toggle in the top jukebox chrome.
- When enabled, continuous playback advances on media end through the current visible Library order.
- Continuous playback skips rows without playable audio or preview URLs and stops cleanly at the end of the current view.
- Added an `RND` transport button that chooses a random playable row from the current filtered/sorted Library and starts playback immediately.
- Added offscreen Qt GUI regression coverage for both transport workflows using a fake audio backend.
- Kept the prior numeric popularity sorting fix from `20260515k`.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515l.tar.gz
sha256: 86372f59378391e8b2f03d4c91032872d4c1f6937893dc852662686afe75a7bf
size: 60,196 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515l.zip
sha256: 205367e8b43c271a22a2b385485c58772d2d4767c4b616dceeb62a2649e17e29
size: 72,017 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: cd0a9d95fa516b8dc0ff8c369ef7a9a8e0f5766973675303ca4ea1700b0c0d8b
size: 57,018 bytes
```

## verification

Targeted transport tests:

```text
pytest -q tests/test_desktop_gui_workflows.py::test_continuous_play_advances_through_visible_playable_rows tests/test_desktop_gui_workflows.py::test_random_transport_selects_and_plays_a_random_playable_row tests/test_desktop_ui_source.py::test_desktop_chrome_controls_are_wired_to_real_behavior
3 passed
```

Full suite:

```text
pytest -q
152 passed
```

Source real-vault transport smoke:

```text
indexed: 2036
visible rows: 2036
playable rows: 2036
continuous checked: true
selected playback and random playback both fired
```

Installed-wheel transport smoke:

```text
indexed: 2036
visible rows: 2036
playable rows: 2036
selected playback and random playback both fired
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel;
- launcher scripts are executable mode `700`;
- app bundle version remains `0.3.0`.
