#!/usr/bin/env python3
"""Cloud ASR recovery worker for Sound Vault spoken-word/lyrics metadata.

Targets sounds where local faster-whisper produced empty/short output. It creates
non-destructive audio variants, sends them to OpenAI's transcription API, writes
provider sidecars, and merges best text back into metadata/searchable fields.

Uses either normal OpenAI API keys (`OPENAI_API_KEY`/`OPENAI_KEY`) or Hermes
OpenAI Codex OAuth (`/opt/data/auth.json`) against ChatGPT backend transcribe.
No secret values are printed.
"""
from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request
import uuid

VAULT_ROOT = Path("/nas/TikTok Sound Vault")
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_TRANSCRIBE_URL = "https://chatgpt.com/backend-api/transcribe"
AUDIO_SUFFIXES = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".mov")
PROMPT = (
    "This is a short TikTok sound. It may contain sung lyrics, meme audio, distorted vocals, "
    "background music, slang, repeated words, chants, nonstandard phrases, or voiceover. "
    "Transcribe any audible spoken words, sung lyrics, chant, catchphrase, or voiceover. "
    "Preserve uncertain words rather than omitting them."
)


def load_dotenv(path: Path = Path("/opt/data/.env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def now() -> str:
    return datetime.now(UTC).isoformat()


def safe_slug(text: str, max_len: int = 90) -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]+", "-", text).strip(" .-")
    return text[:max_len] or "sound"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def audio_for(folder: Path) -> Path | None:
    metadata_path = folder / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = read_json(metadata_path)
            paths = metadata.get("paths")
            if isinstance(paths, dict):
                for key in ("audio", "preview", "preview_audio", "m4a", "file"):
                    raw = paths.get(key)
                    if not raw:
                        continue
                    p = Path(str(raw))
                    if p.exists():
                        return p
        except Exception:
            pass
    matches: list[Path] = []
    for suffix in AUDIO_SUFFIXES:
        matches.extend(sorted(folder.glob(f"*{suffix}")))
    return matches[0] if matches else None


def duration_seconds(audio: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception:
        return None


def local_transcript_text(folder: Path) -> str:
    p = folder / "transcript.json"
    if not p.exists():
        return ""
    try:
        return str(read_json(p).get("text") or "").strip()
    except Exception:
        return ""


def build_manifest(vault: Path, output: Path, *, max_local_chars: int = 20, limit: int = 0) -> list[dict[str, Any]]:
    sounds = vault / "sounds"
    rows: list[dict[str, Any]] = []
    for folder in sorted(sounds.iterdir()):
        if not folder.is_dir():
            continue
        audio = audio_for(folder)
        if audio is None:
            continue
        txt = local_transcript_text(folder)
        reason = None
        if not (folder / "transcript.json").exists():
            reason = "missing_local_transcript"
        elif not txt:
            reason = "empty_local_asr"
        elif len(txt) <= max_local_chars:
            reason = "short_local_asr"
        if reason:
            rows.append({
                "folder": str(folder),
                "folder_name": folder.name,
                "source_audio": str(audio),
                "local_text": txt,
                "reason": reason,
            })
        if limit and len(rows) >= limit:
            break
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return rows


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-1000:])


def make_variants(audio: Path, out_dir: Path, wanted: list[str]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    variants: dict[str, Path] = {}
    if "original" in wanted:
        variants["original"] = audio
    if "clean" in wanted:
        clean = out_dir / "speech-clean.wav"
        if not clean.exists():
            run_ffmpeg([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio),
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "highpass=f=120,lowpass=f=7800,loudnorm,afftdn",
                str(clean),
            ])
        variants["clean"] = clean
    if "loudest_15" in wanted:
        # pragmatic approximation: normalized 15s window from start. Full loudness scan is slower;
        # keep this variant cheap for cloud triage.
        loud = out_dir / "window-start-15-clean.wav"
        if not loud.exists():
            run_ffmpeg([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "0", "-t", "15", "-i", str(audio),
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "highpass=f=120,lowpass=f=7800,loudnorm,afftdn",
                str(loud),
            ])
        variants["loudest_15"] = loud
    if "demucs_vocals" in wanted:
        demucs = shutil.which("demucs") or ("/opt/data/venvs/sound-vault-demucs/bin/demucs" if Path("/opt/data/venvs/sound-vault-demucs/bin/demucs").exists() else None)
        if demucs:
            demucs_root = out_dir / "demucs"
            vocals = demucs_root / "htdemucs_ft" / audio.stem / "vocals.wav"
            if not vocals.exists():
                subprocess.run([demucs, "--two-stems=vocals", "-n", "htdemucs_ft", "-o", str(demucs_root), str(audio)], check=True, timeout=900)
            if vocals.exists():
                variants["demucs_vocals"] = vocals
    return variants


def _b64url_decode(segment: str) -> bytes:
    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment.encode())


def _jwt_exp(token: str) -> int:
    try:
        return int(json.loads(_b64url_decode(token.split(".")[1])).get("exp") or 0)
    except Exception:
        return 0


def _http_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode(errors="replace")
        return json.loads(raw) if raw else {}


def _codex_access_token(auth_path: Path = Path("/opt/data/auth.json")) -> tuple[str, str | None]:
    if not auth_path.exists():
        raise RuntimeError(f"missing Hermes auth store: {auth_path}")
    data = json.loads(auth_path.read_text())
    pool = data.get("credential_pool") if isinstance(data.get("credential_pool"), dict) else {}
    entries = pool.get("openai-codex") if isinstance(pool, dict) else None
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("no openai-codex OAuth credential in Hermes credential_pool")
    entry = next((e for e in entries if isinstance(e, dict) and e.get("access_token") and e.get("refresh_token")), None)
    if entry is None:
        raise RuntimeError("openai-codex credential is missing access_token or refresh_token")
    access = str(entry["access_token"])
    if _jwt_exp(access) < int(time.time()) + 300:
        refreshed = _http_json(
            CODEX_TOKEN_URL,
            method="POST",
            body={
                "client_id": CODEX_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": entry["refresh_token"],
                "scope": "openid profile email",
            },
            timeout=20,
        )
        access = str(refreshed.get("access_token") or "")
        if not access:
            raise RuntimeError("Codex OAuth refresh response missing access_token")
        entry["access_token"] = access
        if refreshed.get("refresh_token"):
            entry["refresh_token"] = refreshed["refresh_token"]
        if refreshed.get("id_token"):
            entry["id_token"] = refreshed["id_token"]
        entry["last_refresh"] = now()
        auth_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        try:
            auth_path.chmod(0o600)
        except OSError:
            pass
    return access, str(entry.get("account_id") or "") or None


def _multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = "----hermes" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    for key, file_path in files.items():
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), boundary


def codex_transcribe(audio: Path) -> dict[str, Any]:
    access, account_id = _codex_access_token()
    body, boundary = _multipart({}, {"file": audio})
    headers = {
        "Authorization": f"Bearer {access}",
        "User-Agent": "codex-cli",
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    req = urllib.request.Request(CODEX_TRANSCRIBE_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")[:1000]
        raise RuntimeError(f"codex transcribe HTTP {exc.code}: {raw}") from exc
    payload = json.loads(raw) if raw else {}
    return {
        "engine": "chatgpt-codex-backend-transcribe",
        "model": "codex-oauth-transcribe",
        "variant_audio": str(audio),
        "text": str(payload.get("text") or "").strip(),
        "language": str(payload.get("language") or ""),
        "duration_seconds": duration_seconds(audio),
        "segments": payload.get("segments") if isinstance(payload.get("segments"), list) else [],
        "raw_response_keys": sorted(payload.keys()),
        "created_at": now(),
        "kind": "spoken_word_or_lyrics_cloud_asr_recovery",
    }


def openai_transcribe(audio: Path, *, model: str, api_key: str) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out = Path(tmp.name)
    cmd = [
        "curl", "-sS", "--fail-with-body", "https://api.openai.com/v1/audio/transcriptions",
        "-H", f"Authorization: Bearer {api_key}",
        "-F", f"file=@{audio}",
        "-F", f"model={model}",
        "-F", "response_format=verbose_json",
        "-F", "timestamp_granularities[]=segment",
        "-F", f"prompt={PROMPT}",
        "-o", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    raw = out.read_text(errors="ignore") if out.exists() else ""
    out.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"curl/openai failed rc={result.returncode}: {(result.stderr + raw)[-1200:]}")
    payload = json.loads(raw)
    return {
        "engine": "openai-audio-transcriptions",
        "model": model,
        "variant_audio": str(audio),
        "prompt": PROMPT,
        "text": str(payload.get("text") or "").strip(),
        "language": str(payload.get("language") or ""),
        "duration_seconds": payload.get("duration"),
        "segments": payload.get("segments") if isinstance(payload.get("segments"), list) else [],
        "raw_response_keys": sorted(payload.keys()),
        "created_at": now(),
        "kind": "spoken_word_or_lyrics_cloud_asr_recovery",
    }


WORD_RE = re.compile(r"[\w’']+", re.UNICODE)


def transcript_qc(text: str, duration: float | None = None) -> dict[str, Any]:
    """Return lightweight guardrails for cloud-ASR runaway loops.

    TikTok clips often contain real repetition, so this does not reject ordinary hooks.
    It catches pathological backend loops: thousands of repeated tokens, impossible WPM,
    or one trigram dominating a long transcript.
    """
    text = re.sub(r"\s+", " ", str(text or "").strip())
    words = WORD_RE.findall(text.lower())
    word_count = len(words)
    unique_ratio = len(set(words)) / word_count if word_count else 0.0
    max_trigram_count = 0
    max_trigram_ratio = 0.0
    if word_count >= 3:
        counts: dict[tuple[str, str, str], int] = {}
        for idx in range(word_count - 2):
            gram = tuple(words[idx : idx + 3])
            counts[gram] = counts.get(gram, 0) + 1
        max_trigram_count = max(counts.values())
        max_trigram_ratio = max_trigram_count / max(1, word_count - 2)
    words_per_minute = word_count / (duration / 60) if duration else 0.0
    wpm_limit = 450 if duration and duration < 15 else 350
    suspicious = bool(
        word_count > 1000
        or (duration and words_per_minute > wpm_limit)
        or (word_count >= 200 and unique_ratio < 0.20)
        or (word_count >= 200 and max_trigram_ratio > 0.12)
    )
    return {
        "word_count": word_count,
        "unique_word_ratio": unique_ratio,
        "max_repeated_trigram_count": max_trigram_count,
        "max_repeated_trigram_ratio": max_trigram_ratio,
        "words_per_minute": words_per_minute,
        "suspicious_cloud_loop_or_overlong": suspicious,
    }


def choose_best(results: list[dict[str, Any]], duration: float | None = None) -> dict[str, Any] | None:
    nonempty = [r for r in results if str(r.get("text") or "").strip()]
    if not nonempty:
        return None
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for result in nonempty:
        qc = transcript_qc(str(result.get("text") or ""), duration)
        result["qc"] = qc
        if not qc["suspicious_cloud_loop_or_overlong"]:
            scored.append((int(qc["word_count"]), len(str(result.get("text") or "")), result))
    if not scored:
        return None
    # Recall-first among sane candidates: prefer the longest plausible transcript.
    return sorted(scored, key=lambda row: (row[0], row[1]), reverse=True)[0][2]


def update_metadata(folder: Path, best: dict[str, Any], provider_dir: Path, results: list[dict[str, Any]]) -> None:
    metadata_path = folder / "metadata.json"
    if not metadata_path.exists():
        return
    try:
        metadata = read_json(metadata_path)
    except Exception:
        return
    metadata.setdefault("paths", {})["cloud_transcript_dir"] = str(provider_dir)
    metadata["speech_transcript_v2"] = {
        "best_text": best.get("text", ""),
        "language": best.get("language", ""),
        "engine": best.get("engine", ""),
        "model": best.get("model", ""),
        "source_variant": best.get("variant", ""),
        "has_text": bool(str(best.get("text") or "").strip()),
        "alternates": [r.get("text", "") for r in results if r.get("text") and r is not best],
        "provider_sidecars": [str(provider_dir / f"{r.get('variant','variant')}.json") for r in results],
        "updated_at": now(),
        "needs_human_review": True,
    }
    write_json(metadata_path, metadata)


def process_row(row: dict[str, Any], *, model: str, api_key: str | None, provider: str, variants_wanted: list[str], force: bool) -> dict[str, Any]:
    folder = Path(row["folder"])
    audio = Path(row["source_audio"])
    work_dir = folder / "derived" / "transcription_v2"
    provider_dir = folder / "transcripts" / f"cloud_recovery_{provider}"
    merged_path = folder / "transcripts" / "cloud_recovery_merged.json"
    if merged_path.exists() and not force:
        merged = read_json(merged_path)
        return {"folder": str(folder), "status": "skipped_existing", "best_text": merged.get("best_text", "")}
    variants = make_variants(audio, work_dir, variants_wanted)
    results = []
    errors = []
    for name, variant_audio in variants.items():
        sidecar = provider_dir / f"{name}.json"
        if sidecar.exists() and not force:
            payload = read_json(sidecar)
        else:
            try:
                payload = openai_transcribe(variant_audio, model=model, api_key=api_key) if provider == "openai_api" else codex_transcribe(variant_audio)
                payload["variant"] = name
                write_json(sidecar, payload)
            except Exception as exc:  # noqa: BLE001
                payload = None
                errors.append({"variant": name, "error": str(exc), "created_at": now()})
                write_json(provider_dir / f"{name}.error.json", errors[-1])
        if payload:
            payload.setdefault("variant", name)
            results.append(payload)
    source_duration = duration_seconds(audio)
    best = choose_best(results, source_duration)
    merged = {
        "folder": str(folder),
        "folder_name": folder.name,
        "source_audio": str(audio),
        "local_reason": row.get("reason"),
        "local_text": row.get("local_text", ""),
        "provider": provider,
        "model": model,
        "variants_attempted": list(variants.keys()),
        "results": [{"variant": r.get("variant"), "text": r.get("text", ""), "language": r.get("language", ""), "qc": r.get("qc", {})} for r in results],
        "errors": errors,
        "best_text": best.get("text", "") if best else "",
        "best_variant": best.get("variant", "") if best else "",
        "updated_at": now(),
        "needs_human_review": True,
    }
    write_json(merged_path, merged)
    if best:
        update_metadata(folder, best, provider_dir, results)
    return {"folder": str(folder), "status": "ok", "best_text": merged["best_text"], "best_variant": merged["best_variant"], "errors": len(errors)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover missed Sound Vault spoken-word/lyrics transcripts with cloud ASR.")
    parser.add_argument("--vault", type=Path, default=VAULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=VAULT_ROOT / "workers" / "transcription" / "cloud_recovery_candidates.jsonl")
    parser.add_argument("--build-manifest", action="store_true")
    parser.add_argument("--manifest-limit", type=int, default=0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--model", default="gpt-4o-transcribe")
    parser.add_argument("--provider", choices=["auto", "openai_api", "codex"], default="auto")
    parser.add_argument("--variants", default="original,clean,loudest_15,demucs_vocals")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    if args.build_manifest or not args.manifest.exists():
        rows = build_manifest(args.vault, args.manifest, limit=args.manifest_limit)
        print(f"wrote manifest: {args.manifest} rows={len(rows)}")
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    provider = args.provider
    if provider == "auto":
        provider = "openai_api" if api_key else "codex"
    if provider == "openai_api" and not api_key:
        print("ERROR: missing OPENAI_API_KEY/OPENAI_KEY for --provider openai_api; manifest is still usable", file=sys.stderr)
        return 2
    if provider == "codex":
        try:
            _codex_access_token()
        except Exception as exc:
            print(f"ERROR: Codex OAuth transcription unavailable: {exc}", file=sys.stderr)
            return 2
    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit > 0:
        rows = rows[: args.limit]
    variants_wanted = [v.strip() for v in args.variants.split(",") if v.strip()]
    summary = []
    print(f"processing rows={len(rows)} provider={provider} model={args.model} variants={','.join(variants_wanted)}")
    for idx, row in enumerate(rows, 1):
        print(f"[{idx}/{len(rows)}] {row.get('folder_name')} reason={row.get('reason')}", flush=True)
        try:
            result = process_row(row, model=args.model, api_key=api_key, provider=provider, variants_wanted=variants_wanted, force=args.force)
            text = result.get("best_text", "")
            print(f"  {result.get('status')} best_variant={result.get('best_variant','')} chars={len(text)} preview={text[:100]!r}", flush=True)
            summary.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {exc}", file=sys.stderr, flush=True)
            summary.append({"folder": row.get("folder"), "status": "error", "error": str(exc)})
    out = args.vault / "workers" / "transcription" / f"cloud_recovery_run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    write_json(out, {"created_at": now(), "provider": provider, "model": args.model, "variants": variants_wanted, "summary": summary})
    ok = sum(1 for r in summary if r.get("status") in {"ok", "skipped_existing"})
    recovered = sum(1 for r in summary if str(r.get("best_text") or "").strip())
    errors = sum(1 for r in summary if r.get("status") == "error")
    print(f"summary_path={out}")
    print(f"done ok={ok} recovered_text={recovered} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
