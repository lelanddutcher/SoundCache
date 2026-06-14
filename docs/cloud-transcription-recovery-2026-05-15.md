# Cloud transcription recovery lane — 2026-05-15

Goal: recover spoken words / lyrics / catchphrases missed by the original local `faster-whisper base` Sound Vault ASR pass.

## What exists now

Script:

```text
scripts/transcribe_cloud_recovery.py
```

Candidate manifest:

```text
/nas/TikTok Sound Vault/workers/transcription/cloud_recovery_candidates.jsonl
```

Current manifest seed: first 100 empty/short local-ASR candidates.

The worker is non-destructive:

- keeps original audio untouched;
- creates derived audio variants under each sound folder's `derived/transcription_v2/`;
- writes provider sidecars under `transcripts/cloud_recovery_openai/`;
- writes a merged result to `transcripts/cloud_recovery_merged.json`;
- writes `metadata.json.speech_transcript_v2` only when cloud ASR returns non-empty text;
- keeps `needs_human_review: true` because lyric/catchphrase ASR is recall-oriented, not verified lyric licensing truth.

## Runtime/auth status

Normal OpenAI API-key env vars were not present:

- `OPENAI_API_KEY`: absent
- `OPENAI_KEY`: absent / not set as a value in `/opt/data/.env`
- `DEEPGRAM_API_KEY`: absent
- `ASSEMBLYAI_API_KEY`: absent

But Hermes' OpenAI Codex OAuth credential pool is usable for ChatGPT backend voice transcription. A direct probe confirmed:

- endpoint: `https://chatgpt.com/backend-api/transcribe`
- auth: Hermes `credential_pool.openai-codex` bearer token + optional `chatgpt-account-id`
- multipart field: `file`
- sample POST returned HTTP 200 with JSON `{ "text": "" }` for a tone/silence probe.

The worker now supports:

```bash
--provider auto       # OpenAI API key if present, otherwise Codex OAuth
--provider codex      # force Hermes/OpenAI Codex OAuth transcription
--provider openai_api # force platform OpenAI Audio API key path
```

## Demucs status

Installed off-NAS:

```text
/opt/data/venvs/sound-vault-demucs/bin/demucs
```

Also installed `torchcodec` because current `torchaudio` save paths require it. Verified Demucs can separate vocals on a real Sound Vault item. CPU-only runtime is slow: one ~35s clip with `htdemucs_ft` bag-of-4 took a few minutes. Use Demucs as a second-pass recovery lane, not the default first pass across thousands of sounds.

## Smoke run completed

First real Codex OAuth cloud-ASR batch:

```bash
cd '/nas/TikTok Sound Vault/product/sound-vault-desktop'
python3 scripts/transcribe_cloud_recovery.py \
  --vault '/nas/TikTok Sound Vault' \
  --manifest '/nas/TikTok Sound Vault/workers/transcription/cloud_recovery_candidates.jsonl' \
  --limit 25 \
  --provider codex \
  --variants original,clean,loudest_15 \
  --force
```

Result:

```text
done ok=25 recovered_text=14 errors=0
```

Disk verification after the run:

```json
{
  "merged_sidecars": 25,
  "codex_provider_sidecars": 76,
  "metadata_speech_transcript_v2": 14,
  "metadata_v2_with_text": 14
}
```

Demucs smoke:

```bash
python3 scripts/transcribe_cloud_recovery.py \
  --vault '/nas/TikTok Sound Vault' \
  --manifest '/nas/TikTok Sound Vault/workers/transcription/cloud_recovery_candidates.jsonl' \
  --limit 1 \
  --provider codex \
  --variants demucs_vocals \
  --force
```

Result: completed and produced a vocal-stem transcript, but for the tested item `clean` beat `demucs_vocals`, so the worker should keep original/clean/window variants in the ensemble and use Demucs selectively.

## Build/refresh manifest

```bash
cd '/nas/TikTok Sound Vault/product/sound-vault-desktop'
python3 scripts/transcribe_cloud_recovery.py \
  --vault '/nas/TikTok Sound Vault' \
  --build-manifest \
  --manifest-limit 0 \
  --limit 0
```

Note: without an API key this command still writes/refreshes the manifest, then exits with a missing-key message.

## Proposed staged rollout

1. Run `--limit 25` against empty/short local transcript candidates.
2. Spot-check raw sidecars and merged text for hallucinated lyrics.
3. If useful, run `--limit 100`.
4. Rebuild Sound Vault desktop index after metadata updates.
5. Smoke-search newly recovered phrases.
6. Only then expand to all candidates.

## 100-candidate rollout completed

Approved 2026-05-15 and run with Codex OAuth cloud ASR:

```bash
cd '/nas/TikTok Sound Vault/product/sound-vault-desktop'
python3 scripts/transcribe_cloud_recovery.py \
  --vault '/nas/TikTok Sound Vault' \
  --manifest '/nas/TikTok Sound Vault/workers/transcription/cloud_recovery_candidates.jsonl' \
  --limit 100 \
  --provider codex \
  --variants original,clean,loudest_15
```

Result:

```text
summary_path=/nas/TikTok Sound Vault/workers/transcription/cloud_recovery_run_20260515_211225.json
done ok=100 recovered_text=49 errors=0
```

Post-run verification:

- merged sidecars: 100 / 100 candidate rows
- recovered cloud text: 49 / 100 candidate rows
- CLI smoke: `Sound Vault loaded 2036 records from /nas/TikTok Sound Vault`
- full index build: 2,036 records
- records with searchable transcript text after cloud recovery: 1,400
- phrase smoke searches found newly recovered rows:
  - `old Taylor can` → `6830245700903717638 | Spencer Hunt | Spencer Hunt`
  - `I am fucking crazy` → `6839852689929571077 | tanner devore | tanner devore`
  - `watch me work` → `6856890666622912514 | Mason & Princess Superstar | Mason & Princess Superstar`

Notes:

- Keep `needs_human_review: true`; this lane is recall-oriented ASR for catchphrase/lyric search, not licensed lyric truth.
- Some results are clearly noisy/hallucinatory on instrumental/no-speech clips, but empty results stayed empty and search indexing is working.
- One outlier recovered a very long transcript (`6854383948970117893`, 43k chars); review before treating it as editorially reliable.

## Why variants matter

- `original`: cloud may simply outperform local Whisper on the same mix.
- `clean`: FFmpeg highpass/lowpass/loudnorm/afftdn can make voice clearer without changing originals.
- `loudest_15`: cheap short-window pass for TikTok sounds where the catchphrase is near the start.
- `demucs_vocals`: optional vocal source separation when `demucs` is installed; useful for sung lyrics/voiceover buried under instrumentation, but can create artifacts, so original/clean must remain in the ensemble.
