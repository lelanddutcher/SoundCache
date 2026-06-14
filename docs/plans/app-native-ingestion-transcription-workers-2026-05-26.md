# App-Native Ingestion, Transcription, Artwork, and Capture Workers Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Preserve the gunmetal design system. Do not flatten the UI while adding operational depth.

**Goal:** Turn Sound Vault from a desktop librarian with some external recovery scripts into a complete app-native workflow for importing TikTok data-export favorite sounds, enriching metadata, downloading/capturing user-authorized media, generating transcripts through local/cloud ASR, optionally separating vocals with Demucs, backfilling artwork, and verifying searchable file-native vault output across macOS, Windows, and Linux.

**Goal statement for execution/QA:** Build and QA a cross-platform, local-first Sound Vault workflow where a non-technical editor can select TikTok's `favorite sounds list.json`, see repair/normalize previews, enrich and package the imported sounds, configure local or cloud transcription, authenticate TikTok when media capture is requested, run artwork/audio/transcript workers with resumable progress, and verify that every durable output appears as browsable vault files plus searchable app metadata without relying on Hermes/agent-only behavior.

**Architecture:** Keep the vault file-native and the database disposable. Convert the existing one-off/scripts into importable worker modules with typed results, resumable manifests, per-run logs, and Qt-safe async execution. The desktop app provides workflow orchestration, settings, progress, and verification gates; the durable truth remains `catalog/*.jsonl|csv`, per-sound `metadata.json`, transcript/artwork/audio sidecars, worker manifests, and audit summaries.

**Tech Stack:** Python 3.11+, PySide6, SQLite/FTS5, ffmpeg/ffprobe, faster-whisper/CTranslate2, OpenAI-compatible cloud transcription APIs, optional Demucs/PyTorch, Playwright or local-browser CDP for user-authorized TikTok sessions, platform keyring or local masked config for secrets.

**Primary source docs/code:**
- `docs/design-system-gunmetal-2026-05-25.md` — visual source of truth.
- `README.md` — product/vault layout and current CLI lanes.
- `src/sound_vault/app.py` — current CLI import/oEmbed/package entrypoints.
- `src/sound_vault/importers/tiktok_archive.py` — current favorite-sounds repair/normalize/dedupe.
- `src/sound_vault/workers/oembed.py` — current public oEmbed enrichment.
- `src/sound_vault/vault/package_writer.py` — current metadata-only folder/catalog packaging.
- `scripts/transcribe_audio.py` — current local ASR worker script.
- `scripts/transcribe_cloud_recovery.py` — current cloud recovery + Demucs variant script.
- `src/sound_vault/ui/desktop.py` — current UI, worker timer, settings dialog, Import/Workers buttons.

---

## Non-negotiables

1. **No Hermes-only magic in product flows.** Existing Hermes/Codex OAuth transcription can remain a dev/operator shortcut, but user-facing cloud ASR must use explicit provider/API-key config.
2. **No raw credentials in vault files, logs, sidecars, screenshots, or summaries.** Store secrets in OS keychain where possible; fallback local config must be masked and clearly marked local-only.
3. **Media download/capture aggressiveness is user-settable.** Metadata-only import remains the safe default. Audio/video capture requires a visible mode, user authorization, and a logged-in TikTok session prompt.
4. **Cloud ASR is the recommended default for most users.** Local ASR remains available with guided dependency/model install. Demucs is optional acceleration/recovery, with CPU warning.
5. **GPU-aware Demucs.** Assume many users have GPU, but detect realistically: Apple Silicon/MPS on Mac, CUDA on NVIDIA Windows/Linux, CPU fallback with warning.
6. **No stale/competing source docs.** If this plan supersedes older implementation notes, move outdated drafts into `docs/_deprecated/` with a short note before shipping final docs.
7. **Gunmetal UI survives.** New settings/workers use existing HUD wells, StatusDot, HealthBar, primary/danger buttons, and dark inset panels. No flat SaaS cards.
8. **Verification is part of the app workflow.** Each worker must produce counts, failures, logs, and a next action. Completion means disk artifacts + index/search verification, not just “thread returned.”

---

## Existing state summary

The app already has a partial app-native spine:

- **Import favorite sounds export:** `SoundVaultWindow.import_tiktok_favorite_sounds_export()` calls `LibraryViewModel.import_favorite_sounds_export()`, which uses `tiktok_archive.py` to repair/normalize TikTok's export and dedupe against the vault.
- **oEmbed enrichment:** Worker Status has “Run oEmbed enrichment,” backed by `workers/oembed.py`.
- **Metadata packaging:** Worker Status has “Package imported metadata,” backed by `vault/package_writer.py`.
- **Worker async shell:** `desktop.py` has `_start_worker_job()` and `_finish_async_worker_job()`, but it assumes only two result shapes and lacks progress/cancel/log streaming.
- **Local/cloud ASR:** exists as scripts, not app-native modules.
- **Demucs:** exists in Leland's runtime, but product needs user install/detect/download guidance.
- **Artwork/media capture:** partially documented by previous workflows, but needs dedicated product workers.

---

## Target user flow

### Import Wizard

1. User opens **Ingest inbox → Import TikTok data export**.
2. App asks for TikTok `favorite sounds list.json`.
3. App repairs fragment non-destructively and previews:
   - source file name/size
   - rows
   - unique music IDs
   - blank IDs
   - duplicates
   - malformed rows
   - already in vault
   - new to vault
   - ambiguous matches
4. User clicks **Normalize import**.
5. User clicks **Enrich metadata**.
6. User clicks **Package into vault**.
7. App rebuilds index and shows archive health delta.
8. App offers next workers:
   - artwork backfill
   - audio download/capture
   - local/cloud ASR
   - associated/example videos
   - verification search smoke

### Transcription setup

1. User opens **Settings → Transcription**.
2. App shows provider cards:
   - Cloud ASR: OpenAI-compatible endpoint/key/model.
   - Local ASR: faster-whisper installed/missing, model cache state, download model action.
   - Source separation: Demucs installed/missing, GPU detected, CPU warning.
3. User can test provider on one selected/fixture audio item.
4. Settings are saved safely and never written into the vault.

### Media capture/download setup

1. User opens **Settings → TikTok session/media capture**.
2. App explains: metadata-only mode does not need login; audio/video capture requires logged-in TikTok session.
3. User chooses capture aggressiveness:
   - metadata only
   - artwork only
   - short preview audio
   - full available sound audio where user has access
   - associated videos/examples
4. App prompts for login/session via browser flow.
5. App verifies session on one music URL before batch work.
6. Worker runs slowly/resumably and stops on checkpoints/CAPTCHA/account-risk signals.

---

## Data model additions

Create durable, explicit state without making SQLite canonical.

### `metadata.json` additions

```json
{
  "paths": {
    "audio": null,
    "artwork": null,
    "transcript": null,
    "cloud_transcript_dir": null,
    "source_separation_dir": null,
    "page_snapshot": null
  },
  "transcription": {
    "local": {
      "status": "missing|queued|running|ok|empty|error|skipped",
      "engine": "faster-whisper",
      "model": "base",
      "has_text": false,
      "updated_at": ""
    },
    "cloud": {
      "status": "missing|queued|running|ok|empty|error|skipped",
      "provider": "openai",
      "model": "gpt-4o-transcribe",
      "best_variant": "clean",
      "has_text": true,
      "needs_human_review": true,
      "updated_at": ""
    }
  },
  "media_capture": {
    "audio_status": "metadata_only|queued|ok|error|not_authorized|not_available",
    "artwork_status": "missing|queued|ok|error|fallback",
    "capture_mode": "metadata_only|artwork|preview_audio|full_audio|associated_videos",
    "session_required": true,
    "last_attempt_at": ""
  }
}
```

### Worker run summary

All workers write:

```text
vault/workers/{worker_name}/runs/{timestamp}.json
vault/workers/{worker_name}/runs/{timestamp}.jsonl
vault/workers/{worker_name}/failed/{timestamp}.csv
```

Summary JSON shape:

```json
{
  "worker": "cloud_asr",
  "started_at": "",
  "finished_at": "",
  "status": "ok|partial|error|cancelled",
  "input_manifest": "",
  "counts": {
    "total": 0,
    "ok": 0,
    "empty": 0,
    "skipped": 0,
    "errors": 0
  },
  "outputs": {},
  "next_actions": []
}
```

### App settings additions

Settings should live outside the vault, via `AppSettings` plus secret store:

```json
{
  "transcription": {
    "preferred_provider": "cloud|local",
    "cloud_provider": "openai",
    "cloud_base_url": "https://api.openai.com/v1",
    "cloud_model": "gpt-4o-transcribe",
    "local_engine": "faster-whisper",
    "local_model": "base",
    "model_cache_dir": "",
    "demucs_enabled": false,
    "demucs_model": "htdemucs_ft"
  },
  "capture": {
    "aggressiveness": "metadata_only",
    "require_manual_login": true,
    "max_batch_items": 25,
    "delay_seconds": 10,
    "stop_on_checkpoint": true
  }
}
```

Secret keys:

- `sound-vault/cloud/openai/api_key`
- future: `sound-vault/cloud/deepgram/api_key`, etc.

---

## Cross-platform dependency strategy

### ffmpeg / ffprobe

Required for media probing, audio cleaning, preview extraction, duration, waveform/review outputs.

Detection:

```python
shutil.which("ffmpeg")
shutil.which("ffprobe")
```

Guidance:

- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg` or bundled ffmpeg path picker
- Linux: `sudo apt install ffmpeg` / distro package manager

Settings UI must show path and version:

```bash
ffmpeg -version
ffprobe -version
```

### local ASR / faster-whisper

Important clarification: faster-whisper itself does not need to be “in PATH” if installed inside the Python environment. The product problem is dependency availability and model cache location, not shell PATH alone.

Handle:

- Python package installed/missing.
- CTranslate2 backend availability.
- model selected/downloaded/missing.
- model cache dir writable and preferably off NAS.
- platform acceleration:
  - CPU int8 everywhere.
  - CUDA where available on NVIDIA.
  - Apple Silicon support is not guaranteed through faster-whisper/CTranslate2 the same way PyTorch MPS works; detect honestly and do not promise Metal acceleration unless verified.

Install guidance:

- App-packaged build: bundle dependency or provide guided install if advanced mode.
- Source/dev install: `python -m pip install -e ".[asr]"`.
- Model download: trigger by constructing `WhisperModel(model_name, ...)` in a background test job, with progress/status text.

Suggested default models:

- quick preview: `tiny`
- normal local lane: `base` or `small`
- high quality: `medium`, warned as slower/larger

### cloud ASR

Cloud ASR is the default recommended lane for most users because it avoids local model/install/GPU complexity and often recovers more TikTok lyric/catchphrase text.

Start with one provider abstraction, OpenAI-compatible:

```python
class TranscriptionProvider(Protocol):
    def transcribe(self, audio_path: Path, *, prompt: str) -> TranscriptResult: ...
```

Support:

- API key
- base URL
- model
- timeout
- max file size warning
- response normalization

Output normalization matters but is tractable. Do not let provider variability leak into the indexer. Normalize to:

```json
{
  "engine": "cloud-openai",
  "provider": "openai",
  "model": "gpt-4o-transcribe",
  "text": "",
  "language": "",
  "duration_seconds": 0,
  "segments": [],
  "raw_response_keys": [],
  "created_at": "",
  "kind": "spoken_word_or_lyrics_cloud_asr"
}
```

Keep raw/provider sidecars for debugging, but index only normalized fields.

### Demucs / source separation

Demucs should be a selectable recovery enhancement, not a universal requirement.

Detection:

- `demucs` executable on PATH.
- package import if app-bundled.
- PyTorch backend:
  - CUDA available: `torch.cuda.is_available()`
  - Apple MPS available: `torch.backends.mps.is_available()`
  - CPU fallback

Reality check:

- CUDA on NVIDIA Windows/Linux can be fast if correct torch build/drivers exist.
- Apple Silicon may use MPS for PyTorch-dependent workloads, but Demucs support/performance must be verified in-app with a test clip.
- CPU works but is slow; warn and estimate.

Settings UI should show:

- Demucs installed/missing
- backend: CUDA/MPS/CPU/unknown
- model: `htdemucs_ft` default
- test result on a short clip

---

## Implementation phases

### Phase 0 — plan/doc cleanup and source-of-truth lock

**Objective:** Prevent competing plans and stale design docs from steering implementation.

**Files:**
- Create/modify: `docs/plans/app-native-ingestion-transcription-workers-2026-05-26.md`
- Review: `docs/design-system-gunmetal-2026-05-25.md`
- Optionally move stale drafts: `docs/_deprecated/`

**Steps:**
1. Commit this plan.
2. Add a short link from `README.md` Roadmap or docs index to this plan.
3. If older app-native ingestion plans conflict, move them into `docs/_deprecated/` with `README.md` note.

**Verification:**
- Plan exists.
- README/doc pointer exists.
- No conflicting active source-of-truth document remains.

---

### Phase 1 — worker result abstraction

**Objective:** Replace shape-specific worker handling with a generic app worker contract.

**Files:**
- Create: `src/sound_vault/workers/base.py`
- Modify: `src/sound_vault/ui/view_model.py`
- Modify: `src/sound_vault/ui/desktop.py`
- Tests: `tests/test_worker_results.py`

**Implementation sketch:**

```python
@dataclass(frozen=True)
class WorkerCounts:
    total: int = 0
    ok: int = 0
    skipped: int = 0
    empty: int = 0
    errors: int = 0

@dataclass(frozen=True)
class WorkerRunResult:
    worker: str
    title: str
    status: str
    counts: WorkerCounts
    summary_path: Path | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    next_actions: tuple[str, ...] = ()
    message: str = ""
```

**Steps:**
1. Add `WorkerRunResult` and adapter helpers for current import/oEmbed/package result dataclasses.
2. Update `_finish_async_worker_job()` to render generic counts/outputs instead of checking `hasattr(summary, "ok_count")`.
3. Keep old paths working via adapters.
4. Add tests for generic rendering/adaptation.

**Verification:**
- Existing oEmbed/package workers still complete.
- Worker Status shows useful counts for both old workers.
- `pytest tests/test_worker_results.py -q` passes.

---

### Phase 2 — import wizard/state machine

**Objective:** Make favorite-sounds import a guided workflow instead of three separate file-picker buttons.

**Files:**
- Create: `src/sound_vault/ui/import_wizard.py` or section in `desktop.py` if keeping file count low initially.
- Modify: `src/sound_vault/ui/desktop.py`
- Modify: `src/sound_vault/ui/view_model.py`
- Tests: `tests/test_import_wizard_state.py`

**Workflow states:**

```text
select_export -> preview_repair -> normalized -> enriched -> packaged -> indexed -> verified
```

**Steps:**
1. Add an Import Wizard panel under Ingest inbox.
2. Wire file selection to existing `write_normalized_favorite_sounds_import()` but show preview before final write where feasible.
3. Add step buttons for Enrich and Package using the selected output from prior step automatically.
4. Store latest import session under `vault/workers/import_sessions/{timestamp}.json`.
5. After packaging, run index rebuild and show archive health delta.

**Verification:**
- Existing `Import TikTok export` still works or is replaced cleanly.
- User does not need to manually pick the normalized JSON for the next step.
- Malformed TikTok fragment is repaired non-destructively.
- Summary shows existing/new/ambiguous counts.

---

### Phase 3 — settings/secrets for transcription and capture

**Objective:** Add real product configuration for cloud ASR, local ASR, Demucs, and TikTok session/capture behavior.

**Files:**
- Modify: `src/sound_vault/settings.py`
- Modify: `src/sound_vault/ui/desktop.py` or create `src/sound_vault/ui/settings_dialog.py`
- Create: `src/sound_vault/secrets.py`
- Tests: `tests/test_settings_transcription.py`, `tests/test_secret_storage.py`

**Secret storage plan:**
1. Try `keyring` if installed and working.
2. If unavailable, use local config file with explicit warning and `0600` permissions on POSIX.
3. Never write secret values to vault, logs, status messages, exception fields, or screenshots.

**Settings sections:**
- Shortcut Relay, existing.
- Transcription Cloud.
- Local ASR.
- Source Separation.
- TikTok Session / Media Capture.
- Dependencies.

**Verification:**
- API key saves and reloads masked.
- Logs do not contain the key.
- Missing key blocks cloud ASR with actionable UI.
- Capture aggressiveness persists.

---

### Phase 4 — dependency diagnostics and guided install

**Objective:** Teach the app what is installed and what the user needs to do per OS.

**Files:**
- Create: `src/sound_vault/diagnostics/dependencies.py`
- Modify: `src/sound_vault/diagnostics.py`
- Modify: Settings UI
- Tests: `tests/test_dependency_diagnostics.py`

**Checks:**
- ffmpeg/ffprobe path/version.
- faster-whisper import availability.
- selected local model cached/missing.
- model cache dir writable/off-NAS warning.
- demucs executable/import availability.
- torch CUDA/MPS/CPU backend.
- Playwright/Chromium availability if capture worker needs it.

**UI copy:**
- macOS commands.
- Windows winget/choco/manual ffmpeg guidance.
- Linux apt/dnf/pacman guidance.
- “PATH means the shell can find the executable; Python packages are detected inside the app environment.”

**Verification:**
- Simulated missing deps produce specific messages.
- `sound-vault --diagnose` includes dependency status without importing Qt.

---

### Phase 5 — app-native local ASR worker

**Objective:** Convert `scripts/transcribe_audio.py` into an importable, resumable worker with model download/test flow.

**Files:**
- Create: `src/sound_vault/workers/transcription/local_asr.py`
- Keep script as CLI wrapper or deprecate into wrapper.
- Modify: `scripts/transcribe_audio.py`
- Modify: ViewModel/UI Worker actions.
- Tests: `tests/test_local_asr_worker.py`

**Worker behavior:**
- Build manifest of sounds with audio and missing/empty transcript.
- Support `limit`, `force`, `model`, `device`, `compute_type`.
- Write `transcript.json` and metadata local transcription summary.
- Write `transcription_error.json` for failures.
- Do not block library population.

**Verification:**
- Dry-run manifest works without ASR dependency.
- Missing dependency gives useful settings error.
- Fixture audio writes normalized transcript sidecar.
- Indexer reads transcript into search.

---

### Phase 6 — app-native cloud ASR worker

**Objective:** Convert cloud recovery script into provider-configured product worker.

**Files:**
- Create: `src/sound_vault/workers/transcription/cloud_asr.py`
- Create: `src/sound_vault/workers/transcription/providers.py`
- Modify: `scripts/transcribe_cloud_recovery.py` into wrapper.
- Tests: `tests/test_cloud_asr_worker.py`, `tests/test_transcript_qc.py`

**Provider design:**
- Start with OpenAI-compatible audio transcription.
- Normalize output aggressively.
- Keep raw provider sidecars.
- Include prompt tuned for TikTok lyrics/catchphrases.
- Add QC guardrails: WPM, repeated trigram dominance, huge transcript, unique-word ratio.

**Variants:**
- `original`
- `clean`
- `loudest_15`
- optional `demucs_vocals`

**Verification:**
- Mock provider outputs normalize correctly.
- Empty, error, and timeout rows checkpoint without killing batch.
- Suspicious runaway transcript is rejected or marked review.
- `metadata.json.speech_transcript_v2` updates only on accepted non-empty text.

---

### Phase 7 — Demucs/source separation worker

**Objective:** Add optional vocal separation as an app-native recovery enhancement with GPU detection.

**Files:**
- Create: `src/sound_vault/workers/transcription/source_separation.py`
- Modify: cloud/local ASR variant pipeline.
- Tests: `tests/test_source_separation_worker.py`

**Behavior:**
- Detect backend: CUDA/MPS/CPU.
- Warn on CPU.
- Run only for selected candidates or failed/short transcript candidates by default.
- Write stems under `derived/transcription_v2/demucs/`.
- Do not overwrite originals.

**Verification:**
- Missing Demucs is reported, not fatal to normal ASR.
- Existing stems are reused unless force enabled.
- Backend detection is shown in Settings.

---

### Phase 8 — artwork backfill worker

**Objective:** Add a dedicated artwork worker that captures true sound artwork when available, not random screenshots.

**Files:**
- Create: `src/sound_vault/workers/artwork.py`
- Modify: `src/sound_vault/vault/indexer.py` if needed for artwork status.
- Modify: Settings/Worker UI.
- Tests: `tests/test_artwork_worker.py`

**Source priority:**
1. Stable TikTok page data / meta image associated with the music title.
2. Structured state/JSON image URLs.
3. Image element near the music title block.
4. Associated video cover fallback, marked `fallback`.
5. No artwork, with error/reason sidecar.

**Outputs:**
- `artwork.jpg|webp`
- `metadata.paths.artwork`
- `metadata.media_capture.artwork_status`
- evidence fields: source URL, selector/method, dimensions, hash, captured_at.

**Verification:**
- Never overwrites valid artwork with empty result.
- Fallback artwork is labeled fallback.
- UI artwork well prefers true artwork.

---

### Phase 9 — authenticated TikTok session and media capture worker

**Objective:** Engineer the non-agentic, scripted equivalent of the earlier authenticated media download/capture workflow.

**Files:**
- Create: `src/sound_vault/workers/tiktok_session.py`
- Create: `src/sound_vault/workers/media_capture.py`
- Create: `src/sound_vault/workers/associated_videos.py` if separate.
- Modify: Settings/Worker UI.
- Tests: `tests/test_tiktok_session_state.py`, `tests/test_media_capture_policy.py`

**Login/session model:**
- User initiates browser login from app.
- App stores Playwright `storageState` or CDP-derived state only with explicit approval.
- App validates session with a conservative probe on one music URL.
- Stop on CAPTCHA/checkpoint/blank shell.
- Do not store TikTok password/cookies in logs.

**Capture aggressiveness:**

```text
metadata_only       # no login needed
artwork             # capture artwork/page metadata only
preview_audio       # bounded short audio/media preview
full_audio          # available sound media where user has access
associated_videos   # 1-3 example videos per sound
```

**Media acquisition strategy:**
1. Resolve canonical music page.
2. Use authenticated session if mode requires it.
3. Inspect DOM/page state/network for direct media candidates.
4. Verify candidate with ffprobe before accepting.
5. If direct source unavailable, use bounded browser/system playback capture only if user enabled it.
6. Write rich editor-readable filenames and metadata sidecars.

**Verification:**
- Session probe produces yes/no with reason.
- Captured files pass ffprobe.
- Full batch is resumable and skip-complete.
- Checkpoint or CAPTCHA stops batch safely.

---

### Phase 10 — associated video/example capture

**Objective:** Bring the outside-of-app trend/evidence video capture lane into the app.

**Files:**
- Create/modify: `src/sound_vault/workers/associated_videos.py`
- Tests: `tests/test_associated_video_worker.py`

**Rules:**
- Pull ordered `/video/` links from the actual music page grid only.
- Exclude inbox/sidebar/notification/profile leakage.
- Download/verify 1-3 examples based on user setting.
- Save:
  - `videos/{rank}-{video_id}-{author}.mp4`
  - per-video JSON sidecars
  - `associated_videos_manifest.json`
  - metadata assets/evidence

**Verification:**
- `ffprobe` confirms video stream.
- Manifest references actual files.
- Metadata associated count is local evidence count, not TikTok popularity.

---

### Phase 11 — verification gates and search smoke

**Objective:** Make “done” prove itself.

**Files:**
- Create: `src/sound_vault/workers/verify.py`
- Modify: UI Worker Status.
- Tests: `tests/test_verify_worker.py`

**Verification checks:**
- catalog row count.
- folders with metadata.
- missing audio/artwork/transcript/videos/popularity counts.
- transcript sidecar count.
- cloud transcript count.
- exact phrase search smoke using index DB.
- ffprobe sample check for captured media.
- artwork file readability/dimensions sample.

**UI:**
- `Verify vault` button.
- Summary HUD with HealthBars.
- Export verification report to `vault/reports/verification-{timestamp}.json|md`.

---

### Phase 12 — tests, packaging, and multi-environment QA

**Objective:** Make this shippable across Mac/Windows/Linux, not just Leland's NAS runtime.

**Test commands:**

```bash
python -m ruff check .
python -m pytest -q
python -m build --no-isolation
sound-vault --diagnose --vault "/path/to/test-vault"
```

**GUI smoke:**
- Offscreen construct window.
- Select sample import file.
- Run normalize with fixture.
- Mock cloud ASR provider.
- Select row and ensure inspector does not crash.

**Environment matrix:**

| OS | Required QA |
|---|---|
| macOS Intel | app launch, ffmpeg detection, local/cloud ASR settings, import wizard |
| macOS Apple Silicon | Demucs/PyTorch backend detection, CPU/MPS messaging, app bundle path behavior |
| Windows 11 NVIDIA | ffmpeg path detection, CUDA/Demucs detection, key storage, path quoting with spaces/unicode |
| Linux NVIDIA | CUDA/Demucs detection, ffmpeg apt path, NAS/local paths |
| Linux CPU only | clear slow warnings, ASR fallback behavior, no false GPU claims |

**Vault fixture QA:**
- tiny fixture vault committed to tests.
- real vault smoke optional/local only.
- TikTok network tests gated behind explicit env var and never run in CI by default.

---

## Build order recommendation

1. Worker result abstraction.
2. Import wizard state machine.
3. Settings/secrets/dependency diagnostics.
4. Cloud ASR provider worker with mocked tests.
5. Local ASR worker/model diagnostics.
6. Demucs detection/source separation variant.
7. Artwork worker.
8. Authenticated session/media capture worker.
9. Associated videos worker.
10. Verification/report worker.
11. Cross-platform packaging/QA.

This order gets the deterministic non-login import path polished first, then cloud ASR, then progressively riskier authenticated media capture.

---

## Risks and decisions

### Cloud output variability

Yes, provider outputs vary, but the right fix is a narrow normalization boundary. Store raw sidecars, index normalized text only, and keep QC guardrails. Do not build provider-specific metadata into the indexer.

### Local model download complexity

Do not force users to understand Python PATH. The app should distinguish:

- executable dependencies: ffmpeg, demucs CLI, browser tools
- Python package dependencies: faster-whisper, torch
- model cache assets: Whisper model files

Each gets different guidance.

### TikTok media capture risk

Make it explicit, slow, user-authorized, resumable, and stoppable. Metadata-only import must remain useful without login. Audio/video capture should be settable because users may have different rights/risk tolerance.

### Demucs acceleration

Detect, don't assume. If CUDA/MPS exists, show it. If not, warn CPU slow. Use source separation on failed/short transcript candidates first.

---

## Acceptance criteria

The implementation is complete when:

1. A user can import a TikTok `favorite sounds list.json` through a guided app flow without manually selecting intermediate JSON files.
2. The app can enrich and package imported records into file-native vault folders and catalog rows.
3. Settings can store/test a cloud transcription API key without leaking secrets.
4. The app can run cloud ASR over selected candidates and index recovered transcript text.
5. The app can detect/install-guide local ASR dependencies and run a local ASR worker when available.
6. The app can detect Demucs/GPU/CPU state and optionally use vocal stems for recovery.
7. The app has a dedicated artwork worker that prefers actual sound artwork and marks fallbacks honestly.
8. Media capture/download behavior is controlled by user-selected aggressiveness and requires/session-validates TikTok login when needed.
9. Worker outputs are resumable, logged, and visible in Worker Status.
10. Verification reports prove disk artifacts, index/search behavior, and archive-health deltas.
11. QA has been run on multiple OS environments, with platform-specific dependency results documented.
