# Sound Vault QA response build - 2026-05-15i

This note records the evidence-aware duplicate detector pass made after Leland reported that Duplicate Review was treating sounds as duplicates when they only shared similar title/artist metadata while having very different transcripts, durations, and thumbnails.

## user reports addressed

- Duplicate detection was too insensitive to actual sound evidence.
- Same or similar title/artist was enough to pollute Duplicate Review.
- Candidates with massively different duration should not be considered duplicates.
- Candidates with unrelated transcripts should not be considered duplicates.
- Different artwork/thumbnails should lower confidence and be visible in the review evidence.

## fixed in build 20260515i

- `scripts/audit_duplicates.py` now builds evidence-aware duplicate groups instead of flat title/artist groups.
- Title/artist similarity is only an initial clue. A candidate pair now needs corroborating evidence:
  - close duration;
  - close transcript;
  - same artwork/thumbnail fingerprint;
  - same local audio path;
  - same canonical URL.
- The detector hard-rejects candidate pairs when both transcripts exist and are not close.
- The detector hard-rejects candidate pairs when known durations are vastly different.
- Different artwork/thumbnail fingerprints reduce confidence and appear in the group reason.
- Duplicate reports now include group score, candidate score, artwork path, transcript excerpt, local audio path, and detailed reason text.
- Duplicate Review preserves those report evidence fields for fallback inspector hydration when a candidate cannot be found in the index.

## real-vault report migration

The active real-vault duplicate report was regenerated with the smarter detector.

New active report:

```text
/Volumes/hermes-share/TikTok Sound Vault/reports/duplicate-candidates.json
4 groups / 8 candidate rows
```

Backups of the previous noisy report:

```text
/Volumes/hermes-share/TikTok Sound Vault/reports/duplicate-candidates.pre-20260515i.json
/Volumes/hermes-share/TikTok Sound Vault/reports/duplicate-candidates.pre-20260515i.csv
139 groups / 352 candidate rows
```

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515i.tar.gz
sha256: 7e297fbf68621f96b53fb5e5c97b8fe6a8fba116052ec0207cf9a43eba2d51fd
size: 58,802 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260515i.zip
sha256: 419358af285084c7f72b63338649f31331564aefc2a1e803c1bcf0c7ff93abc2
size: 70,613 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 0e05d033069229de4d015050e522971754cbc8ba581e8bbc5ed2fbcffb173fcb
size: 55,614 bytes
```

## verification

Targeted duplicate audit/review tests:

```text
pytest -q tests/test_duplicate_audit.py tests/test_dedupe_review_workflow.py tests/test_dedupe_desktop_source.py tests/test_desktop_gui_workflows.py
15 passed
```

Full suite:

```text
pytest -q
148 passed
```

Additional checks:

```text
git diff --check
passed

py_compile audit_duplicates.py / dedupe_review.py / view_model.py
passed
```

Real-vault smart duplicate audit, temp output:

```text
duplicate candidate rows: 8
duplicate groups: 4
```

Installed-wheel smart Duplicate Review smoke:

```text
2036 indexed records
4 active duplicate groups
2 candidates in selected group
first candidate playable: true
candidate selection changed inspector metadata: true
duration line present: true
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar/zip contain `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel.

## regression coverage added

- Same title/artist plus close duration still yields candidates.
- Same title/artist plus unrelated transcripts yields no candidates.
- Same title/artist plus vastly different durations yields no candidates.
- Different artwork/thumbnails lower confidence but do not override strong matching transcript/duration evidence.
- Report-only duplicate candidates can still hydrate fallback artwork, transcript excerpt, duration, and audio path in the inspector.

## remaining follow-up

- Move the smart audit generator into the packaged desktop app so `Refresh duplicate candidates` can regenerate the report instead of only reloading it.
- Add a review-page column or inspector block for duplicate score and reason details.
- Consider audio fingerprinting/waveform similarity for the next precision jump.
