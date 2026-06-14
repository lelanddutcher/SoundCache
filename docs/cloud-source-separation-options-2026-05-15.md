# Cloud/API alternatives to local Demucs — 2026-05-15

Goal: avoid CPU-only local Demucs for TikTok Sound Vault transcription recovery.

## Current local status

Local Demucs `htdemucs_ft` on CPU took ~415s for one 60s clip and did not recover text on that test. Cheap FFmpeg + cloud ASR variants were ~9–11s per 60s sound serially.

No provider keys are currently exposed in this runtime:

```json
{
  "REPLICATE_API_TOKEN": false,
  "AUDIOSHAKE_API_KEY": false,
  "MOISES_API_KEY": false,
  "LALAL_API_KEY": false,
  "OPENAI_API_KEY": false,
  "OPENAI_KEY": false
}
```

## Best candidates

### 1. AudioShake API — best fit if we want lyric metadata quality

Why it fits:

- Has stem separation API: `https://api.audioshake.ai/tasks` with targets like `{ "model": "vocals", "formats": ["wav"] }`.
- Has lyric transcription API: same task endpoint with `{ "model": "transcription", "formats": ["json"] }`.
- Public docs explicitly frame lyric transcription as: split acapella, then transcribe and align word-by-word.
- Designed for catalog / metadata / lyric workflows, not just karaoke extraction.

Use for:

- high-value music/lyric clips;
- cases where searchable lyric metadata matters more than raw ASR speed;
- replacing both Demucs + transcription in one provider pipeline.

Caveat: likely paid / account-gated. Need API key.

### 2. Moises Developer Platform — good music-specific API, clear pricing

Why it fits:

- Developer platform lists `Stems separation` and `Lyrics transcription` modules.
- Stems separation: upload one file, get stems such as drums, bass, vocals.
- Lyrics transcription: returns transcribed lyrics with word sync and automatic line breaks.
- Published legacy pricing seen in docs: stems `$0.10/min`, lyrics `$0.07/min`.

Use for:

- clean music-first fallback;
- testing against AudioShake if we can get API access.

Caveat: docs are labeled legacy; verify current access before building around it.

### 3. LALAL.AI API v1 — production stem/noise API, less obviously lyric-native

Why it fits:

- API v1 supports stem splitting, voice/background cleanup, multistem, and batch processing.
- Search result/docs mention `/split/` and `/check/` flow.
- Good candidate if we only need cleaner vocal or voice stem before our existing OpenAI/Codex transcription.

Use for:

- vocal isolation / voice cleaner before ASR;
- batch stem separation at scale.

Caveat: less directly focused on lyric transcription than AudioShake/Moises. Need API key and docs probe.

### 4. Replicate Demucs — fastest path to GPU Demucs without hosting

Why it fits:

- Replicate hosts Demucs models, including `cjwbw/demucs`.
- Same conceptual output as local Demucs but on cloud GPU.
- Easy to wrap if `REPLICATE_API_TOKEN` is available.

Use for:

- apples-to-apples comparison with current local Demucs;
- quick validation before negotiating vendor APIs.

Caveat: hosted community model reliability/versioning may be less stable than direct vendor APIs. Need token.

### 5. Self-host GPU Demucs on RunPod/Modal/etc.

Why it fits:

- Keeps open-source model and file flow under our control.
- Cheapest at high volume if many clips need source separation.
- Can use faster `htdemucs` instead of 4x slower `htdemucs_ft`.

Use for:

- high-volume second-pass lane if API costs or vendor constraints get annoying.

Caveat: requires ops work: queue, object storage, cleanup, retries, GPU image.

## Recommended evaluation order

1. Keep first pass as `original,clean,loudest_15` through existing cloud ASR. It is cheap and already works.
2. For remaining misses, test AudioShake first because it directly targets lyric transcription + stem separation.
3. Test Moises second if API access is available; compare lyric transcript output and per-minute cost.
4. Use Replicate Demucs for a fast apples-to-apples GPU benchmark if we get a token.
5. Only build self-host GPU Demucs if the miss list is large enough to justify it.

## Minimal integration shape

Add provider modes to `scripts/transcribe_cloud_recovery.py`:

```text
--separation-provider none|local_demucs|replicate_demucs|audioshake|moises|lalal
--lyric-provider codex|openai_api|audioshake|moises
```

Then preserve current metadata contract:

- provider sidecars under `transcripts/cloud_recovery_<provider>/`;
- derived stems under `derived/transcription_v2/` or worker benchmark folder;
- `speech_transcript_v2.best_text` + `alternates`;
- always `needs_human_review: true`.
