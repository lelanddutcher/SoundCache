# Sound Vault QA response build - 2026-05-15h

This note records the Duplicate Review fixes made after Leland reported that review buttons did not remove groups from the queue and the right inspector did not update while reviewing duplicate candidates.

## user reports addressed

- `Mark duplicates` recorded a decision but left the group visible in Duplicate Review.
- `Mark not duplicates` recorded a decision but left the group visible in Duplicate Review.
- The right-side metadata inspector stayed on the Library selection instead of updating to the duplicate candidate being reviewed.
- Duplicate Review needed page-specific pressure testing before another build handoff.

## fixed in build 20260515h

- Duplicate Review now filters reviewed groups out of the queue after terminal decisions:
  - `duplicates`
  - `not_duplicates`
  - `quarantined_duplicates`
- `Mark duplicates` now requires a selected keeper row and records the remaining candidates as duplicates.
- `Mark not duplicates` records the whole group as reviewed without inventing duplicate rows.
- Duplicate candidate selection now hydrates the right inspector from the indexed sound record where possible, including title, artist/source, duration, transcript, folder/audio paths, evidence, artwork, and playable state.
- Duplicate candidate playback selects and hydrates the candidate before starting playback, so the right inspector stays aligned with the auditioned row.
- Candidate fallback preview still works for report rows that are not in the index.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515h.tar.gz
sha256: b745e485c147fe7e6c8031a24d1bc74f0aed0914ba201c28e848cae4fa3bcb39
size: 58,371 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515h.zip
sha256: 4ff183faaa4ea94b74988daed74a7ee1e05d477301f7fb2d9ca8ed100685052b
size: 70,165 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: a8c36a5b1e888b93fa5e54d411535abe51c37df6e949d3dd30c911c8c4fc1ea5
size: 55,166 bytes
```

`dist/CURRENT-BEST-MAC-BUILD.txt` is the source of truth for current handoff details.

## verification

Targeted Duplicate Review tests:

```text
pytest -q tests/test_dedupe_review_workflow.py tests/test_dedupe_desktop_source.py tests/test_desktop_gui_workflows.py
10 passed
```

Full suite:

```text
pytest -q
143 passed
```

Additional checks:

```text
git diff --check
passed

py_compile desktop.py/view_model.py/duplicate-review tests
passed
```

Source real-vault Duplicate Review smoke:

```text
2036 indexed records
137 open duplicate groups after filtering reviewed groups
first duplicate group candidates: 2
candidate selection changed inspector metadata: true
first candidate playable: true
duration line present: true
transcript line present: true
```

Installed-wheel real-vault Duplicate Review smoke:

```text
2036 indexed records
137 open duplicate groups after filtering reviewed groups
first duplicate group candidates: 2
candidate selection changed inspector metadata: true
first candidate playable: true
duration line present: true
transcript line present: true
```

Installed-wheel fixture Duplicate Review smoke:

```text
inspector update: true
play button enabled: true
Mark duplicates: groups 2 -> 1
Mark not duplicates: groups 1 -> 0
Quarantine duplicates: duplicate folder moved and group removed
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel.

## regression coverage added

- View-model test that reviewed terminal decisions hide Duplicate Review groups.
- View-model test that duplicate candidate previews hydrate indexed metadata, duration, and transcript text.
- Offscreen Qt GUI test that Duplicate Review updates the right inspector, plays through the candidate path, and removes groups after `Mark duplicates` / `Mark not duplicates`.
- Offscreen Qt GUI test that the quarantine path moves duplicate folders and removes the reviewed group.

## remaining follow-up

- Add a visible reviewed/decisions history panel for Duplicate Review.
- Add restore-from-quarantine from inside the UI.
- Add richer A/B candidate comparison, waveform hints, and duplicate confidence details.
