# Cloud ASR vs Demucs benchmark — 2026-05-15

Scope: read-only benchmark for TikTok Sound Vault transcription recovery. It creates derived benchmark audio under `/nas/TikTok Sound Vault/workers/transcription/benchmarks/` and calls cloud ASR, but does not update sound `metadata.json` or merged transcript sidecars.

Benchmark harness:

```text
/nas/TikTok Sound Vault/workers/transcription/benchmark_cloud_demucs_compare.py
```

Raw report:

```text
/nas/TikTok Sound Vault/workers/transcription/benchmarks/cloud_demucs_compare_1778878154.json
```

## Auth/provider status

- Standard OpenAI Audio API path: unavailable in this runtime (`OPENAI_API_KEY` / `OPENAI_KEY` not present).
- Hermes/OpenAI Codex OAuth ChatGPT backend transcription: available.

So today’s test is “cloud ASR via OpenAI/Codex OAuth backend,” not the official `/v1/audio/transcriptions` API. The worker can use official OpenAI later if a key is provided.

## Runtime result: one representative 60s backlog item

Sample:

```text
manifest row 26
6730315527257851905 - - HNNY - HNNY
source duration: 60.0s
local ASR text: empty
```

| variant | preprocessing time | cloud ASR time | transcript |
|---|---:|---:|---|
| original | 0.003s | 2.033s | empty |
| clean | 4.245s | 2.119s | empty |
| loudest_15 | 1.071s | 2.087s | empty |
| demucs_vocals | 415.161s | 1.728s | empty |

Takeaway: for this 60s clip, `htdemucs_ft` vocals extraction cost ~6m55s on CPU and did not recover text. Cheap variants cost ~1–4s preprocessing plus ~2s cloud ASR.

## Transcript-quality comparison from existing smoke sidecars

### Empire of the Sun — manifest row 1, 29.9s

| variant | transcript |
|---|---|
| original | empty |
| clean | “It's a grandma to be upon the calm one. I feel a to be upon the calm one.” |
| loudest_15 | “It's a grand love to be upon the” |
| demucs_vocals | “This town to every new.” |

Takeaway: Demucs produced some words, but worse than `clean`. For this item, `clean` is the useful recovery.

### George Michael — manifest row 8, 60s

| variant | excerpt |
|---|---|
| original | “I will be your father figure, put your tiny hands in mine…” |
| clean | “I will be your father, see, put your tiny hands in mine…” |
| loudest_15 | “I will be your father, see me put your tiny hands in mine…” |

Takeaway: original is semantically better on “father figure”; clean is longer but slightly corrupts that phrase. This is why the merged result must keep alternates and be human-review flagged.

### Mariah Carey — manifest row 9, 60s

| variant | excerpt |
|---|---|
| original | “Hey, baby, I'm so into you. Darling, if you only knew…” |
| clean | “Baby, I'm so into you, darling, if you only knew…” |
| loudest_15 | “And baby, I'm so into you…” |

Takeaway: original wins; clean/window are useful alternates.

### The Hit Crew — manifest row 25, 60s

| variant | excerpt |
|---|---|
| original | “Mississippi Queen, if you know what I mean…” |
| clean | “Mississippi Queen, if you know what I mean…” |
| loudest_15 | “Mississippi Queen, if you know what I mean…” |

Takeaway: original/clean both recover searchable lyric metadata; loudest_15 is enough for a cheap phrase hit but not full metadata.

## Practical recommendation

Do not run Demucs as default across the vault. Use it as a third-pass retry lane only after `original,clean,loudest_15` return empty or obviously broken text.

Suggested staged flow:

1. First pass: `original,clean,loudest_15` on the next 100 candidates.
2. Compare hit rate and obvious garbage rate.
3. Build a “still empty after cloud cheap variants” manifest.
4. Run Demucs only against that smaller miss list, ideally capped to short clips first.
5. Keep all alternates in metadata; mark `needs_human_review: true`.

Rough speed from this test:

- Cheap cloud matrix per 60s sound: about 9–11 seconds total wall time if run serially.
- Demucs `htdemucs_ft` per 60s sound on CPU: about 7 minutes before the cloud call.
- Demucs is ~40x slower than the cheap cloud matrix for the tested 60s item.
