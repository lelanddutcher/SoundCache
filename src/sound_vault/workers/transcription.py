from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable

from sound_vault.vault.metadata_io import atomic_write_json
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
                # Vault-relative (portable); the indexer rebases on read.
                try:
                    paths["transcript"] = str(transcript_path.relative_to(vault_root))
                    paths["cloud_transcript_dir"] = str(transcript_dir.relative_to(vault_root))
                except ValueError:
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
    atomic_write_json(path, data, sort_keys=True)


# --------------------------------------------------------------------------- #
# Local (faster-whisper) per-sound transcription, wired into ingest/re-enrich.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LocalASRConfig:
    model: str = "base"
    compute_type: str = "int8"
    model_cache_dir: str | None = None


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _faster_whisper_device() -> tuple[str, str]:
    """Pick (device, compute_type) for CTranslate2. CUDA when an NVIDIA GPU is
    present (float16), else CPU (int8). CTranslate2 has no Metal/Apple-GPU path, so
    on Apple Silicon use the mlx backend instead — see mlx_whisper_transcriber."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:  # noqa: BLE001 - any probe failure -> safe CPU default
        pass
    return "cpu", "int8"


def mlx_whisper_available() -> bool:
    """MLX = Apple's on-device ML framework; runs whisper on the Apple-Silicon GPU.
    Only meaningful on arm64 macOS with the mlx-whisper package installed."""
    import platform

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _mlx_model_repo(model: str) -> str:
    """Map a model name to an mlx-community HF repo. A value already containing '/'
    is treated as an explicit repo."""
    name = (model or "base").strip()
    if "/" in name:
        return name
    aliases = {
        "turbo": "mlx-community/whisper-large-v3-turbo",
        "large": "mlx-community/whisper-large-v3-mlx",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    }
    return aliases.get(name, f"mlx-community/whisper-{name}-mlx")


def mlx_whisper_transcriber(config: LocalASRConfig | None = None) -> "Callable[[Path], dict[str, Any]] | None":
    """A callable(audio_path) -> {text, language, model, engine} using mlx-whisper on
    the Apple-Silicon GPU. The model downloads from HF on first use. Returns None when
    MLX isn't available (non-arm64-mac or package missing)."""
    if not mlx_whisper_available():
        return None
    cfg = config or LocalASRConfig()
    repo = _mlx_model_repo(cfg.model)

    def _transcribe(audio_path: Path, should_stop: "Callable[[], bool] | None" = None) -> dict[str, Any]:
        # mlx_whisper.transcribe is a single blocking GPU call (no segment generator
        # to break on), so the only safe cancel point is before it starts.
        if should_stop is not None and should_stop():
            return {"text": "", "language": "", "model": cfg.model, "engine": "mlx-whisper"}
        import mlx_whisper

        result = mlx_whisper.transcribe(str(audio_path), path_or_hf_repo=repo)
        return {
            "text": str(result.get("text") or "").strip(),
            "language": str(result.get("language") or ""),
            "model": cfg.model,
            "engine": "mlx-whisper",
        }

    return _transcribe


def faster_whisper_transcriber(config: LocalASRConfig | None = None) -> "Callable[[Path], dict[str, Any]] | None":
    """A lazy callable(audio_path) -> {text, language, model, engine} using faster-whisper.

    The model is loaded on the FIRST call and cached, so building a service stays
    cheap and the (slow, possibly downloading) model load only happens when a
    transcription actually runs. Returns None if faster-whisper isn't installed.
    """
    if not faster_whisper_available():
        return None
    cfg = config or LocalASRConfig()
    state: dict[str, Any] = {}

    def _transcribe(audio_path: Path, should_stop: "Callable[[], bool] | None" = None) -> dict[str, Any]:
        model = state.get("model")
        if model is None:
            from faster_whisper import WhisperModel

            device, compute_type = _faster_whisper_device()
            try:
                model = WhisperModel(
                    cfg.model, device=device, compute_type=compute_type,
                    download_root=cfg.model_cache_dir or None,
                )
            except Exception:  # noqa: BLE001 - GPU init can fail (missing cuDNN); fall back to CPU
                model = WhisperModel(
                    cfg.model, device="cpu", compute_type=cfg.compute_type,
                    download_root=cfg.model_cache_dir or None,
                )
            state["model"] = model
        # Honor a quit before the (uninterruptible) VAD preprocessing even starts.
        if should_stop is not None and should_stop():
            return {"text": "", "language": "", "model": cfg.model, "engine": "faster-whisper"}
        # model.transcribe(vad_filter=True) runs synchronous VAD preprocessing and
        # then returns a lazy generator — the per-segment compute happens as we
        # consume it. The initial preprocessing can't be interrupted, but checking
        # should_stop between segments makes the bulk of a long transcription
        # promptly cancellable on quit, so a mid-transcribe quit doesn't leave this
        # worker thread running past app teardown (the QThread-destroyed-while-running
        # crash the download path also guards against).
        segments, info = model.transcribe(str(audio_path), vad_filter=True)
        parts: list[str] = []
        for seg in segments:
            if should_stop is not None and should_stop():
                break
            parts.append(seg.text.strip())
        text = " ".join(part for part in parts if part).strip()
        return {
            "text": text,
            "language": getattr(info, "language", "") or "",
            "model": cfg.model,
            "engine": "faster-whisper",
        }

    return _transcribe


def _has_transcript_text(metadata: dict[str, Any]) -> bool:
    v2 = metadata.get("speech_transcript_v2")
    if isinstance(v2, dict) and str(v2.get("text") or v2.get("best_text") or "").strip():
        return True
    inline = metadata.get("transcript") or metadata.get("speech_transcript")
    if isinstance(inline, dict) and str(inline.get("text") or "").strip():
        return True
    if isinstance(inline, str) and inline.strip():
        return True
    return False


def transcribe_sound_folder(
    folder: Path,
    *,
    audio_path: Path | None,
    transcriber: "Callable[[Path], dict[str, Any]]",
    overwrite: bool = False,
    should_stop: "Callable[[], bool] | None" = None,
) -> dict[str, Any]:
    """Transcribe one packaged sound's audio and write the result into its
    metadata.json (speech_transcript_v2 + paths.transcript) and a sidecar.

    Best-effort + idempotent: skips if a transcript already exists (unless
    overwrite) or there's no audio. ``should_stop`` is forwarded into the engine so
    a quit cancels a long transcription promptly. Returns {status, has_text}.
    """
    folder = Path(folder)
    meta_path = folder / "metadata.json"
    if not meta_path.exists():
        return {"status": "skipped", "reason": "no metadata.json"}
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "failed", "reason": f"unreadable metadata: {exc}"}
    if not overwrite and _has_transcript_text(metadata):
        return {"status": "skipped", "reason": "already has transcript"}
    if audio_path is None or not Path(audio_path).exists():
        return {"status": "skipped", "reason": "no audio"}

    # Forward should_stop only when set, so transcribers (cloud, test fakes) that
    # take just the audio path keep their exact prior call shape.
    if should_stop is not None:
        try:
            result = transcriber(Path(audio_path), should_stop=should_stop)
        except TypeError:
            result = transcriber(Path(audio_path))
    else:
        result = transcriber(Path(audio_path))
    text = str(result.get("text") or "").strip()
    language = str(result.get("language") or "")
    model = str(result.get("model") or "")
    engine = str(result.get("engine") or "faster-whisper")

    transcript_dir = folder / "transcripts" / f"local_{_safe(engine)}"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / "transcript.json"
    _write_json(
        transcript_path,
        {"engine": engine, "model": model, "language": language, "text": text, "created_at": _now()},
    )

    metadata["speech_transcript_v2"] = {"text": text, "language": language, "model": model, "engine": engine}
    paths = metadata.setdefault("paths", {})
    if isinstance(paths, dict):
        # Store vault-relative (like package_sound) so the path survives a vault
        # move/remount; the indexer rebases it back to absolute on read.
        try:
            paths["transcript"] = str(transcript_path.relative_to(folder.parent.parent))
        except ValueError:
            paths["transcript"] = str(transcript_path)
    transcription = metadata.setdefault("transcription", {})
    if isinstance(transcription, dict):
        transcription["local"] = {
            "status": "ok" if text else "empty",
            "engine": engine,
            "model": model,
            "has_text": bool(text),
            "updated_at": _now(),
        }
    audit = metadata.setdefault("audit", {})
    if isinstance(audit, dict):
        audit["missing_transcript"] = not text
    atomic_write_json(meta_path, metadata)
    return {"status": "ok" if text else "empty", "has_text": bool(text)}
