from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

from sound_vault.workers.result import WorkerRunResult, write_worker_run


@dataclass(frozen=True)
class CloudASRConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: int = 120


Transcriber = Callable[[Path, CloudASRConfig], dict[str, Any]]


def transcribe_cloud_batch(vault_root: Path, *, config: CloudASRConfig, transcriber: Transcriber) -> WorkerRunResult:
    ok = 0
    skipped = 0
    empty = 0
    errors: list[str] = []
    verified: list[str] = []
    events: list[dict[str, Any]] = []
    for metadata_path in sorted((vault_root / "sounds").glob("* - */metadata.json")):
        try:
            metadata = _read_json(metadata_path)
            audio = _path_value(metadata, "audio")
            if audio is None or not audio.exists():
                skipped += 1
                _set_transcription_status(metadata, provider=config.provider, model=config.model, status="skipped", has_text=False)
                _write_json(metadata_path, metadata)
                continue
            result = transcriber(audio, config)
            text = str(result.get("text") or "").strip()
            transcript_dir = metadata_path.parent / "transcripts" / f"cloud_{_safe(config.provider)}"
            transcript_path = transcript_dir / "transcript.json"
            transcript_payload = {
                "engine": f"cloud-{config.provider}",
                "provider": config.provider,
                "base_url": _redact_base_url(config.base_url),
                "model": config.model,
                "text": text,
                "language": result.get("language") or "",
                "duration_seconds": result.get("duration_seconds") or 0,
                "segments": result.get("segments") if isinstance(result.get("segments"), list) else [],
                "created_at": _now(),
                "needs_human_review": True,
            }
            transcript_dir.mkdir(parents=True, exist_ok=True)
            _write_json(transcript_path, transcript_payload)
            paths = metadata.setdefault("paths", {})
            if isinstance(paths, dict):
                paths["transcript"] = str(transcript_path)
                paths["cloud_transcript_dir"] = str(transcript_dir)
            metadata["speech_transcript_v2"] = {
                "provider": config.provider,
                "model": config.model,
                "best_text": text,
                "best_variant": "cloud",
                "needs_human_review": True,
                "updated_at": _now(),
            }
            _set_transcription_status(metadata, provider=config.provider, model=config.model, status="ok" if text else "empty", has_text=bool(text))
            _write_json(metadata_path, metadata)
            verified.extend([str(metadata_path), str(transcript_path)])
            events.append({"event": "cloud_asr.transcribed", "metadata": str(metadata_path), "transcript": str(transcript_path), "has_text": bool(text)})
            if text:
                ok += 1
            else:
                empty += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{metadata_path}: {exc!r}")
    status = "ok" if not errors else ("partial" if ok or empty or skipped else "error")
    result = WorkerRunResult(
        worker="cloud_asr",
        status=status,
        counts={"total": ok + empty + skipped + len(errors), "ok": ok, "empty": empty, "skipped": skipped, "errors": len(errors)},
        verified_outputs=verified,
        errors=errors,
        next_actions=[] if status == "ok" else ["review cloud ASR failures; confirm provider key/endpoint without storing credentials in the vault"],
    )
    write_worker_run(vault_root, result, events=events)
    return result.normalized()


def _set_transcription_status(metadata: dict[str, Any], *, provider: str, model: str, status: str, has_text: bool) -> None:
    transcription = metadata.setdefault("transcription", {})
    if not isinstance(transcription, dict):
        transcription = {}
        metadata["transcription"] = transcription
    transcription["cloud"] = {"status": status, "provider": provider, "model": model, "has_text": has_text, "updated_at": _now()}
    audit = metadata.setdefault("audit", {})
    if isinstance(audit, dict):
        audit["missing_transcript"] = not has_text


def _path_value(metadata: dict[str, Any], key: str) -> Path | None:
    paths = metadata.get("paths")
    if not isinstance(paths, dict):
        return None
    value = paths.get(key)
    return Path(str(value)) if value else None


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value) or "provider"


def _redact_base_url(value: str) -> str:
    return value.split("?", 1)[0]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
