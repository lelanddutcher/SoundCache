# QA Response Build 2026-05-18c

This build adds Library multi-select duplicate marking and clarifies Duplicate Review quarantine behavior.

## Changes

- Library table now uses extended row selection.
- Right-clicking a selected Library row preserves the selected set instead of collapsing to one row.
- Added `Mark as Duplicate` to the Library right-click menu.
- Manual duplicate marking writes a new group to `reports/duplicate-candidates.json`.
- Manual marking does not delete, quarantine, or record a terminal duplicate decision.
- Duplicate Review remains the place to audition candidates, choose the keeper, mark not-duplicates, or quarantine non-keepers.
- `Add to Favorites`, `Add to sorting bin`, and `New sorting bin` context actions now operate on all selected Library rows.
- Dragging multiple selected rows into Favorites or a sorting bin now carries the selected set.
- Quarantine copy now explicitly says the selected row is the keeper, remains in `sounds/`, and only the other candidate folders move to `reports/duplicate-quarantine`.

## Quarantine Audit

`Quarantine duplicates` keeps the selected candidate folder in the vault. It moves only the other candidate folders in the selected duplicate group into:

```text
reports/duplicate-quarantine/{timestamp}-{group}/
```

It then records a `quarantined_duplicates` row in `reports/duplicate-decisions.jsonl` and rebuilds the index. It does not delete both sounds. It does not move the selected keeper. Existing tests verify the keeper folder remains and the duplicate folder is moved.

## Build Artifacts

Preferred Mac launcher:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260518c.tar.gz
sha256: e872bf53a6a3df4a02724e020a48694cffc2d3d8dd42c92d21604475315b0bee
size: 86,184 bytes
```

Fallback zip:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260518c.zip
sha256: 86a6ac04ac79e996319b926af5d8a8f6d58ee9fa599068caafedda519eed1dce
size: 98,469 bytes
```

Wheel:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: e2473f6d0bfd211b83c73cb6f2ce59dd75dcea8b79b3c05f6136b4b92713f2f7
size: 83,470 bytes
```

## Verification

- `pytest -q`: 177 passed.
- Targeted duplicate/manual-selection/desktop GUI/source tests: 50 passed.
- `py_compile` for desktop, view model, and duplicate review worker: passed.
- `git diff --check`: passed.
- Installed-wheel manual duplicate GUI smoke: 3 fixture records, 1 manual duplicate group, 2 candidate rows.
- Installed-wheel real-vault diagnostics: passed.
- Installed-wheel real-vault duplicate smoke: 2,036 records loaded, duplicate review groups read without error.
- Launcher archive contents verified.
- Launcher entrypoints pass `zsh -n`.
- Launcher entrypoints are executable.

## Remaining Limits

- Manual duplicate marking intentionally only creates a review group. It does not decide the keeper.
- In-app restore from duplicate quarantine is not built yet.
- Sorting bins still store membership, not custom row order.
