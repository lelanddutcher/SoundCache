from sound_vault.workers.transcription import (
    _asr_repo_for,
    _mlx_model_repo,
    _qwen3_model_repo,
    asr_model_is_cached,
    download_asr_model,
    qwen3_asr_available,
)


def test_qwen3_repo_mapping():
    assert _qwen3_model_repo("qwen3-asr-0.6b") == "mlx-community/Qwen3-ASR-0.6B-bf16"
    assert _qwen3_model_repo("qwen3-asr-1.7b") == "mlx-community/Qwen3-ASR-1.7B-bf16"
    assert _qwen3_model_repo("base") == "mlx-community/Qwen3-ASR-1.7B-bf16"  # unknown -> 1.7B default
    assert _qwen3_model_repo("mlx-community/Custom-Repo") == "mlx-community/Custom-Repo"  # explicit repo


def test_large_v3_turbo_is_available_as_a_whisper_model():
    # The easy accuracy upgrade over `base` — a config value, no new engine.
    assert _mlx_model_repo("large-v3-turbo") == "mlx-community/whisper-large-v3-turbo"


def test_qwen3_availability_is_a_bool_and_never_raises():
    # Missing package / non-arm64 must degrade to False (so build_transcriber falls back
    # to Whisper), never crash.
    assert isinstance(qwen3_asr_available(), bool)


def test_asr_repo_for_maps_engine_to_the_right_hf_repo():
    assert _asr_repo_for("qwen3-asr", "qwen3-asr-1.7b") == "mlx-community/Qwen3-ASR-1.7B-bf16"
    assert _asr_repo_for("mlx-whisper", "large-v3-turbo") == "mlx-community/whisper-large-v3-turbo"
    assert _asr_repo_for("mlx-whisper", "base") == "mlx-community/whisper-base-mlx"
    assert _asr_repo_for("faster-whisper", "base") is None  # CT2 fetches its own way -> no HF prefetch


def test_download_skips_engine_without_hf_model():
    # faster-whisper has no HF repo to prefetch -> download is a clean no-op, not an error.
    res = download_asr_model("faster-whisper", "base")
    assert res["status"] == "skipped"


def test_asr_model_is_cached_returns_bool_offline_safe():
    # Never raises (offline / hub error / unknown) — always a plain bool.
    assert isinstance(asr_model_is_cached("qwen3-asr", "qwen3-asr-1.7b"), bool)
    assert asr_model_is_cached("faster-whisper", "base") is True  # nothing to fetch -> cached
