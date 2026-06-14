# Sound Vault QA response build - 2026-05-15k

This note records the Library popularity sorting fix after Leland showed the popularity column sorting values as text.

## user report addressed

The popularity column sorted like this:

```text
9997
997600
9954
99200
```

That is lexicographic text ordering, not numeric popularity ordering.

## fixed in build 20260515k

- Library popularity sorting now uses integer `usage_count`.
- The Library table no longer lets native `QTableWidget` sorting re-sort the main table after refresh.
- The app keeps its own library sort column/order state and repopulates rows in that order.
- Popularity defaults to numeric descending.
- Clicking the popularity header toggles numeric ascending/descending.
- Added a regression fixture with `997600`, `9997`, and `9954` to prevent this exact failure from returning.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515k.tar.gz
sha256: 551959f370bac0c42140cb45c9c1bd7410d4973b7da00dfa0e762922a9ca7a64
size: 59,541 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515k.zip
sha256: 321c05e6b120eb98b849d557562db21599d9956509eefcb6b723bbca8f47b6ab
size: 71,332 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: f0df84d1ed214b8f88ddb2bad35327ecfc1ebfdec00992f17b9bf36d8567321a
size: 56,333 bytes
```

## verification

Targeted popularity tests:

```text
pytest -q tests/test_desktop_gui_workflows.py::test_library_popularity_sort_is_numeric_not_text tests/test_desktop_ui_source.py::test_desktop_table_columns_include_dates_popularity_and_local_audio tests/test_desktop_ui_source.py::test_desktop_preserves_selection_scroll_and_sort_when_refreshing_library
3 passed
```

Full suite:

```text
pytest -q
150 passed
```

Source real-vault popularity smoke:

```text
2036 indexed records
descending top values: 23100000, 10000000, 9300000, 8300000
descending numeric: true
ascending toggle numeric: true
```

Installed-wheel popularity smoke:

```text
2036 indexed records
descending top values: 23100000, 10000000, 9300000, 8300000
descending numeric: true
ascending toggle numeric: true
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel.
