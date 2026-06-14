# QA Response Build 2026-05-18b

This build adds favorites, manual sorting bins, smart sorts, drag/drop bin assignment, and readable menu/dropdown popups.

## Fix

- Added `sound_vault.vault.library_collections` as a durable file-native store at `catalog/library_collections.json`.
- Added a favorite star column at the far left of the Library table, immediately before row playback.
- Added a collapsible Library section in the left sidebar.
- Added built-in smart sorts:
  - Favorites;
  - No transcript / likely instrumental;
  - Missing audio;
  - 100K+ uses;
  - Has example videos.
- Added a plus button to create manual sorting bins.
- Added drag/drop from Library rows into Favorites and user sorting bins.
- Added right-click Add to submenu with Favorites, existing bins, and New sorting bin.
- Enlarged `QMenu` rows and added explicit hover/selected styling.
- Restyled `QComboBox QAbstractItemView` so dropdown item text is readable before hover.
- Guarded against stale saved bin filters from another vault.

## Artifacts

Preferred tarball:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260518b.tar.gz
sha256: d86655a449418f2963e3ca6c8a97fb8972f9e9fd62835f45c3d30aa97e701146
```

Fallback zip:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.3.0-20260518b.zip
sha256: 13fa999631c45e74609eb065abe50a99ba6df88e821ee44e3c98612dbd9d9f14
```

Wheel:

```text
/Volumes/hermes-share/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: aee451bc72ef3c19a75d7d100069f8f055e27da1029f88f850f15f22f4c12284
```

## Verification

- `pytest -q`: 174 passed.
- Targeted collections/settings/desktop GUI/source tests: 49 passed.
- `git diff --check`: passed.
- Source offscreen collection smoke passed.
- Installed-wheel collection GUI smoke passed.
- Installed-wheel real-vault diagnostics passed.
- Installed-wheel real-vault CLI smoke loaded 2,036 records.
- Installed-wheel real-vault smart-sort smoke passed:
  - 2,036 records;
  - no-transcript smart sort returned 288 rows;
  - high-popularity smart sort returned 626 rows.
- Launcher zip/tar, executable mode, and bundled wheel verified.

## Explicit Limits

- Sorting bins store membership, not custom order yet.
- Drag/drop assigns rows to sidebar bins; in-bin drag reordering is not implemented yet.
- Smart sorts are local deterministic filters.
