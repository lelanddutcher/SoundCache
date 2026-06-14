# Sound Vault QA response build - 2026-05-15g

This note records the app changes made in response to the 2026-05-15 UI and duplicate-review feedback.

## user reports addressed

- The search box lost focus while typing. After one character, focus could jump into the library table and require clicking back into search.
- Duplicate Review was a dead end: candidates could not be played back, and there was no practical dedupe action.
- The UI needed to move further toward an intricate brushed-metal, graphite, retro hardware workstation instead of a lightly styled table app.

## fixed in build 20260515g

- Search refresh now preserves search-box focus and cursor position after table updates.
- Library row playback is verified as a real button control.
- Duplicate Review candidate playback now resolves audio from:
  - explicit candidate `local_audio_path` or `audio_path`;
  - indexed sound records for the candidate `music_id`;
  - local `.m4a` files found in the candidate folder.
- Duplicate Review now includes a reversible `Quarantine duplicates` workflow:
  - the selected row is treated as the keeper;
  - the remaining candidates in that group are moved under `reports/duplicate-quarantine/{timestamp}-{group}/`;
  - a `quarantined_duplicates` decision is appended to `reports/duplicate-decisions.jsonl`;
  - the index is rebuilt after the quarantine action.
- The visual system was pushed further toward the requested reference:
  - graphite app shell;
  - layered brushed-metal main deck;
  - pill search field;
  - deeper inset cards and tables;
  - tactile row play buttons;
  - stronger danger-button styling for dedupe actions.

The quarantine action is intentionally non-destructive. Final delete/merge/restore tooling should build on this rather than bypass it.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515g.tar.gz
sha256: 3caf0ea1ff514be472ea21f02278644eed12b3599ecc7198369895260ca2f7b1
size: 57,129 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515g.zip
sha256: ce9dc89a150f3be0109f654966295a58bcba9c49b8a686e630a20a004aa51340
size: 68,917 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: ebae517659914c94f809bcf8acc5584cb0de578d7a2a3aa747724535dd32b7bb
size: 53,918 bytes
```

`dist/CURRENT-BEST-MAC-BUILD.txt` is the source of truth for current handoff details.

## verification

```text
pytest -q
139 passed
```

```text
git diff --check
passed
```

Source real-vault GUI smoke:

```text
2036 indexed records
145 results for search "bitch"
TRANSCRIPTS (1,363)
search focus preserved: true
row play button rendered: true
139 duplicate groups
first duplicate candidate playable: true
```

Installed-wheel GUI smoke:

```text
2036 indexed records
145 results for search "bitch"
TRANSCRIPTS (1,363)
search focus preserved: true
row play button rendered: true
139 duplicate groups
first duplicate candidate playable: true
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel.

## regression coverage added

- Offscreen Qt GUI workflow test for search focus and cursor preservation.
- View-model test for duplicate candidate playback resolution through indexed records.
- View-model test for duplicate folder quarantine and decision recording.
- Source tests asserting duplicate-review quarantine wiring and visual-control identifiers.

## remaining follow-up

- Add restore-from-quarantine and final delete/merge affordances after the quarantine workflow gets real use.
- Replace the large `QTableWidget` surfaces with model/delegate tables for better full-vault performance.
- Add screenshot regression captures for the main library and duplicate-review views.
- Continue pushing the UI from styled widgets toward a coherent hardware-console design system.
