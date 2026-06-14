# Sound Vault QA response build - 2026-05-16b

This build fixes the right-inspector transcript display after validating that the metadata summary was clipping transcript text.

## validated

The transcript ingestion/index path preserves full transcript text from metadata or transcript sidecars. The truncation was in the right inspector:

```text
record.transcript_text[:180]
```

That meant the metadata summary only showed the first 180 characters even when the full transcript was available.

## fixed in build 20260516b

- Added a dedicated read-only `Transcript` panel in the right inspector.
- The transcript panel uses `setPlainText()` with the full indexed transcript text.
- The metadata summary now reports transcript availability, character count, language, and sidecar filename instead of showing a clipped excerpt.
- Duplicate Review also uses the same transcript panel, so candidate transcripts are visible during dedupe review.
- Added source and GUI regressions to ensure transcript text is not clipped in metadata again.

## artifact

Current best Mac build:

```text
dist/SoundVault-mac-launcher-0.3.0-20260516b.tar.gz
sha256: 1ce65cb30ba2f902c5b4dc488ed5503772afc20083813411a155d69217dff2d2
size: 61,799 bytes
```

Fallback zip:

```text
dist/SoundVault-mac-launcher-0.3.0-20260516b.zip
sha256: 891532a97ade2873e64f269e891497b4a7f8ff377f6ac3ca988b8c16adbe4e8c
size: 73,613 bytes
```

Wheel:

```text
dist/sound_vault_desktop-0.3.0-py3-none-any.whl
sha256: 02d4275912acbc426b6743d17a6ee3c9e9f16292d70c42e1fc7bced8d2064cfc
size: 58,614 bytes
```

## verification

Full suite:

```text
pytest -q
154 passed
```

Targeted transcript tests:

```text
pytest -q tests/test_desktop_gui_workflows.py::test_desktop_gui_qa_harness_exercises_core_editor_workflows tests/test_desktop_ui_source.py::test_desktop_inspector_shows_full_transcript_without_metadata_truncation tests/test_redesign_archive_model.py::test_build_index_includes_spoken_word_transcripts_in_search_metadata tests/test_redesign_archive_model.py::test_build_index_includes_cloud_recovery_transcript_v2_text
4 passed
```

Source real-vault transcript smoke:

```text
indexed: 2036
music_id: 7183303677226044165
transcript chars: 424
shown chars: 424
metadata summary: transcript: available (424 chars • en • transcript.json)
```

Installed-wheel transcript smoke:

```text
indexed: 2036
music_id: 7183303677226044165
transcript chars: 424
shown chars: 424
```

Installed-wheel CLI smoke:

```text
Sound Vault loaded 2036 records from /Volumes/hermes-share/TikTok Sound Vault
```

Launcher archive checks:

- generated launcher scripts pass `zsh -n`;
- tar contains `Sound Vault.app`, `Open Sound Vault.command`, and bundled wheel;
- launcher scripts are executable mode `700`.
