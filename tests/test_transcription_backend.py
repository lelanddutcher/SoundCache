from sound_vault.workers.transcription import (
    _mlx_model_repo,
    _qwen3_model_repo,
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
