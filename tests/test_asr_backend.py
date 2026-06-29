"""ASR backend selection: GPU-first (MLX on Apple Silicon) with faster-whisper fallback."""
from __future__ import annotations

from types import SimpleNamespace

import sound_vault.workers.transcription as tx
from sound_vault.ingest.factory import build_transcriber
from sound_vault.workers.transcription import _mlx_model_repo

_SETTINGS = SimpleNamespace(transcription_config=lambda: {"local_model": "base"})


def _fake_builder(engine_name):
    """A builder that yields a transcriber tagging its engine, so we can see which won."""
    def builder(_cfg):
        return lambda *a, **k: {"text": "", "language": "", "model": "base", "engine": engine_name}
    return builder


def _none_builder(_cfg):
    return None


def _clear_env(monkeypatch):
    monkeypatch.delenv("SOUND_VAULT_DISABLE_TRANSCRIBE", raising=False)
    monkeypatch.delenv("SOUND_VAULT_ASR_BACKEND", raising=False)


def test_auto_prefers_mlx_when_available(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(tx, "mlx_whisper_transcriber", _fake_builder("mlx-whisper"))
    monkeypatch.setattr(tx, "faster_whisper_transcriber", _fake_builder("faster-whisper"))
    t = build_transcriber(settings=_SETTINGS)
    assert t("x")["engine"] == "mlx-whisper"


def test_auto_falls_back_to_faster_whisper_when_mlx_unavailable(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(tx, "mlx_whisper_transcriber", _none_builder)  # e.g. not Apple Silicon
    monkeypatch.setattr(tx, "faster_whisper_transcriber", _fake_builder("faster-whisper"))
    t = build_transcriber(settings=_SETTINGS)
    assert t("x")["engine"] == "faster-whisper"


def test_env_override_forces_faster_whisper(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SOUND_VAULT_ASR_BACKEND", "faster-whisper")
    monkeypatch.setattr(tx, "mlx_whisper_transcriber", _fake_builder("mlx-whisper"))
    monkeypatch.setattr(tx, "faster_whisper_transcriber", _fake_builder("faster-whisper"))
    t = build_transcriber(settings=_SETTINGS)
    assert t("x")["engine"] == "faster-whisper"


def test_disable_env_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SOUND_VAULT_DISABLE_TRANSCRIBE", "1")
    assert build_transcriber(settings=_SETTINGS) is None


def test_no_backend_available_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(tx, "mlx_whisper_transcriber", _none_builder)
    monkeypatch.setattr(tx, "faster_whisper_transcriber", _none_builder)
    assert build_transcriber(settings=_SETTINGS) is None


def test_mlx_model_repo_mapping():
    assert _mlx_model_repo("base") == "mlx-community/whisper-base-mlx"
    assert _mlx_model_repo("small") == "mlx-community/whisper-small-mlx"
    assert _mlx_model_repo("large-v3") == "mlx-community/whisper-large-v3-mlx"
    assert _mlx_model_repo("turbo") == "mlx-community/whisper-large-v3-turbo"
    assert _mlx_model_repo("mlx-community/whisper-tiny") == "mlx-community/whisper-tiny"  # explicit repo kept
