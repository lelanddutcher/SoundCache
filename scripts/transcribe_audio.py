#!/usr/bin/env python3
"""Create spoken-word/lyrics transcript sidecars for Sound Vault audio.

Uses faster-whisper when installed, then openai-whisper if available. The output is
searchable by the desktop indexer via transcript.json. This worker is resumable and
keeps error sidecars separate so failed items do not masquerade as completed transcripts.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

VAULT_ROOT = Path("/path/to/Sound Cache")
AUDIO_SUFFIXES = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".mp4", ".mov")
_MODEL_CACHE: dict[str, Any] = {}


def _audio_for(folder: Path) -> Path | None:
    metadata_path = folder / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            paths = metadata.get("paths")
            if isinstance(paths, dict):
                for key in ("audio", "preview", "preview_audio", "m4a", "file"):
                    if not paths.get(key):
                        continue
                    p = Path(str(paths[key]))
                    if p.exists():
                        return p
        except (OSError, json.JSONDecodeError):
            pass
    matches: list[Path] = []
    for suffix in AUDIO_SUFFIXES:
        matches.extend(sorted(folder.glob(f"*{suffix}")))
    return matches[0] if matches else None


def _duration_seconds(audio: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _transcribe_with_python(audio: Path, model_name: str) -> dict[str, Any] | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ModuleNotFoundError:
        WhisperModel = None  # type: ignore
    if WhisperModel is not None:
        cache_key = f"{model_name}:cpu:int8"
        model = _MODEL_CACHE.get(cache_key)
        if model is None:
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _MODEL_CACHE[cache_key] = model
        segments, info = model.transcribe(str(audio), vad_filter=True)
        rows = [
            {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()}
            for seg in segments
            if seg.text.strip()
        ]
        return {
            "engine": "faster-whisper",
            "model": model_name,
            "language": getattr(info, "language", "") or "",
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "text": " ".join(row["text"] for row in rows).strip(),
            "segments": rows,
        }
    return None


def _transcribe_with_cli(audio: Path, model_name: str, tmp_dir: Path) -> dict[str, Any] | None:
    if subprocess.run(["bash", "-lc", "command -v whisper >/dev/null"], check=False).returncode != 0:
        return None
    tmp_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["whisper", str(audio), "--model", model_name, "--output_format", "json", "--output_dir", str(tmp_dir)],
        text=True,
        capture_output=True,
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:1000] or f"whisper exited {result.returncode}")
    out = tmp_dir / f"{audio.stem}.json"
    if not out.exists():
        return None
    payload = json.loads(out.read_text(encoding="utf-8"))
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    return {
        "engine": "openai-whisper-cli",
        "model": model_name,
        "language": str(payload.get("language") or ""),
        "text": str(payload.get("text") or "").strip(),
        "segments": [
            {"start": s.get("start"), "end": s.get("end"), "text": str(s.get("text") or "").strip()}
            for s in segments
            if isinstance(s, dict) and str(s.get("text") or "").strip()
        ],
    }


def transcribe(audio: Path, model_name: str, tmp_dir: Path) -> dict[str, Any]:
    payload = _transcribe_with_python(audio, model_name) or _transcribe_with_cli(audio, model_name, tmp_dir)
    if payload is None:
        raise RuntimeError("No transcription engine installed. Install sound-vault-desktop[asr] or openai-whisper.")
    payload["audio_path"] = str(audio)
    payload["duration_seconds"] = _duration_seconds(audio)
    payload["created_at"] = datetime.now(UTC).isoformat()
    payload["kind"] = "spoken_word_or_lyrics_asr"
    return payload


def _update_metadata(folder: Path, transcript_path: Path, payload: dict[str, Any]) -> None:
    metadata_path = folder / "metadata.json"
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    metadata.setdefault("paths", {})["transcript"] = str(transcript_path)
    metadata["speech_transcript"] = {
        "text": payload.get("text", ""),
        "language": payload.get("language", ""),
        "engine": payload.get("engine", ""),
        "model": payload.get("model", ""),
        "has_text": bool(str(payload.get("text") or "").strip()),
    }
    if payload.get("duration_seconds") is not None:
        metadata.setdefault("duration_seconds", payload["duration_seconds"])
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def iter_pending_folders(vault_root: Path, *, force: bool) -> list[Path]:
    sounds_root = vault_root / "sounds"
    if not sounds_root.exists():
        raise FileNotFoundError(f"missing sounds folder: {sounds_root}")
    folders = []
    for folder in sorted(sounds_root.iterdir()):
        if not folder.is_dir():
            continue
        if (folder / "transcript.json").exists() and not force:
            continue
        if _audio_for(folder) is None:
            continue
        folders.append(folder)
    return folders


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill searchable spoken-word/lyrics transcript sidecars for Sound Vault audio.")
    parser.add_argument("--vault", type=Path, default=VAULT_ROOT)
    parser.add_argument("--limit", type=int, default=0, help="0 means all pending audio files")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()

    pending = iter_pending_folders(args.vault, force=args.force)
    if args.limit > 0:
        pending = pending[: args.limit]
    total = len(pending)
    wrote = 0
    empty = 0
    errors = 0
    print(f"pending transcripts: {total}")
    for index, folder in enumerate(pending, start=1):
        audio = _audio_for(folder)
        if audio is None:
            continue
        transcript_path = folder / "transcript.json"
        error_path = folder / "transcription_error.json"
        print(f"[{index}/{total}] transcript {folder.name}", flush=True)
        if args.dry_run:
            print(f"  {audio}")
            continue
        try:
            payload = transcribe(audio, args.model, folder / ".transcribe-tmp")
            transcript_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            _update_metadata(folder, transcript_path, payload)
            if error_path.exists():
                error_path.unlink()
            if payload.get("text"):
                wrote += 1
                print(f"  wrote {len(payload['text'])} chars", flush=True)
            else:
                empty += 1
                print("  no speech/lyrics detected; wrote empty transcript sidecar", flush=True)
        except Exception as exc:  # noqa: BLE001 - batch worker should continue and checkpoint errors
            errors += 1
            error_payload = {
                "audio_path": str(audio),
                "error": str(exc),
                "engine": "faster-whisper/openai-whisper",
                "model": args.model,
                "created_at": datetime.now(UTC).isoformat(),
            }
            error_path.write_text(json.dumps(error_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  ERROR: {exc}", file=sys.stderr, flush=True)
            if args.stop_on_error:
                raise
    print(f"done: processed {total}; text={wrote}; empty={empty}; errors={errors}")
    return 1 if errors and args.stop_on_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
