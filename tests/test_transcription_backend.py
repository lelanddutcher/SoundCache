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


def test_decode_to_wav16k_converts_aac_that_libsndfile_rejects(tmp_path):
    """qwen3_asr_mlx loads audio via libsndfile, which CANNOT decode our .m4a/AAC vault
    files ("bad data offset"). _decode_to_wav16k must turn AAC into a 16 kHz mono WAV that
    IS readable — the fix that makes Qwen3-ASR work on the real (all-.m4a) library."""
    import shutil
    import subprocess

    import pytest

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    try:
        import soundfile as sf
    except Exception:  # noqa: BLE001
        pytest.skip("soundfile not installed")

    from sound_vault.workers.transcription import _decode_to_wav16k

    m4a = tmp_path / "tone.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.5", "-c:a", "aac", str(m4a)],
        check=True,
    )
    # Document the underlying breakage: libsndfile genuinely can't open the AAC.
    with pytest.raises(Exception):
        sf.read(str(m4a))
    # ...but our ffmpeg pre-decode yields a WAV soundfile reads, at 16 kHz mono.
    wav = _decode_to_wav16k(m4a)
    try:
        data, sr = sf.read(str(wav))
        assert sr == 16000
        assert data.ndim == 1  # mono
        assert len(data) > 0
    finally:
        wav.unlink(missing_ok=True)
