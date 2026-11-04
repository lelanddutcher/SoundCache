"""Waveform peaks and tempo for the track list, computed once per sound at import.

Both come from one ffmpeg decode to mono PCM. Results live next to the audio, the
same file-native way as everything else in the vault:

* ``waveform.json`` beside the audio: ``{"version", "peaks": [0..1 × N], "duration"}``.
* ``metadata.json`` gains ``bpm`` and ``bpm_confidence`` (``bpm`` is null when the
  estimate is not trustworthy, e.g. a 3-second SFX or free-time speech), plus
  ``paths.waveform`` and an ``analysis`` stamp.

The SQLite index only caches these, so a rebuild recovers them from disk.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from sound_vault.vault.metadata_io import atomic_write_json, read_json_lenient

ANALYSIS_VERSION = 1
PEAK_COUNT = 400
DECODE_RATE = 11_025
WAVEFORM_FILENAME = "waveform.json"
# Below this the tempo is reported as unknown ("—" in the UI) instead of guessed.
MIN_BPM_CONFIDENCE = 0.2
MIN_BPM, MAX_BPM = 60.0, 200.0


class AnalysisError(RuntimeError):
    """The audio could not be decoded."""


@dataclass(frozen=True)
class AudioAnalysis:
    peaks: list[float]
    duration: float
    bpm: float | None
    bpm_confidence: float


def decode_mono(audio_path: Path, *, sample_rate: int = DECODE_RATE, timeout: float = 60.0) -> np.ndarray:
    """Decode any audio ffmpeg understands to mono float32 in [-1, 1]."""
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(audio_path),
        "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnalysisError(f"ffmpeg could not run: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no audio"]
        raise AnalysisError(f"ffmpeg could not decode {audio_path.name}: {detail[0]}")
    return np.frombuffer(result.stdout, dtype="<i2").astype(np.float32) / 32768.0


def compute_peaks(samples: np.ndarray, count: int = PEAK_COUNT) -> list[float]:
    """Max |amplitude| per bucket, scaled so the loudest bucket is 1.0."""
    if samples.size == 0:
        return [0.0] * count
    edges = np.linspace(0, samples.size, count + 1).astype(int)
    peaks = np.array(
        [float(np.abs(samples[a:b]).max()) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])],
        dtype=np.float64,
    )
    top = peaks.max()
    if top > 0:
        peaks /= top
    return [round(float(p), 3) for p in peaks]


def estimate_bpm(samples: np.ndarray, sample_rate: int = DECODE_RATE) -> tuple[float | None, float]:
    """Tempo from the autocorrelation of an onset-strength envelope.

    Returns ``(bpm, confidence)``; ``bpm`` is None when confidence is below
    :data:`MIN_BPM_CONFIDENCE`. Confidence is the normalised autocorrelation at the
    chosen beat period (0 = no periodicity, 1 = a perfect click track).
    """
    hop = max(1, int(sample_rate * 0.005))  # 5 ms frames
    frames = samples.size // hop
    if frames < int(4.0 / 0.005):  # under ~4 s there are too few beats to trust
        return None, 0.0
    trimmed = samples[: frames * hop].reshape(frames, hop)
    energy = np.log1p(1000.0 * np.square(trimmed.astype(np.float64)).mean(axis=1))
    onset = np.maximum(np.diff(energy, prepend=energy[0]), 0.0)
    onset -= onset.mean()
    if not np.any(onset):
        return None, 0.0
    n = onset.size
    spectrum = np.fft.rfft(onset, 2 * n)
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[:n]
    if ac[0] <= 0:
        return None, 0.0
    ac /= ac[0]

    frame_s = hop / sample_rate
    lo = int(np.floor(60.0 / (MAX_BPM * frame_s)))
    hi = min(n // 2 - 1, int(np.ceil(60.0 / (MIN_BPM * frame_s))))
    if hi <= lo + 2:
        return None, 0.0
    lags = np.arange(lo, hi + 1)
    # Comb score: a true beat period also lines up at 2×, 3× and 4× its lag, while an
    # off-beat (half) period lands on odd multiples where the pattern does not repeat,
    # so it scores lower. Half tempo (twice the lag) ties with the true tempo; among
    # near-ties the fastest wins, which matches how editors count BPM.
    positions = np.arange(n, dtype=np.float64)
    # Peaks are about one 5 ms frame wide, so at fast tempos the multiples fall between
    # frames. Widen them by ±1 frame for the comb sum (not for the chosen lag).
    ac_wide = np.maximum(ac, np.maximum(np.r_[ac[1:], 0.0], np.r_[0.0, ac[:-1]]))
    score = np.zeros(lags.size)
    for idx, lag_frames in enumerate(lags):
        multiples = lag_frames * np.arange(1, 5)
        multiples = multiples[multiples < n - 1]
        score[idx] = float(np.interp(multiples, positions, ac_wide).mean()) if multiples.size else 0.0
    # Only local maxima of the comb are candidates (avoids the shoulders of a peak).
    is_peak = np.r_[False, (score[1:-1] >= score[:-2]) & (score[1:-1] >= score[2:]), False]
    # The period itself must repeat strongly, or a third/quarter of the beat could win
    # on the strength of its higher multiples alone.
    own = ac[lags]
    is_peak &= own >= 0.5 * own.max()
    candidates = np.flatnonzero(is_peak) if is_peak.any() else np.array([int(np.argmax(score))])
    top = score[candidates].max()
    near = candidates[score[candidates] >= 0.9 * top]
    best = int(near.min())  # smallest lag = fastest tempo among the near-ties
    lag = float(lags[best])
    # Parabolic interpolation around the autocorrelation peak for sub-frame precision.
    i = int(lags[best])
    if 1 <= i < n - 1:
        y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
        denom = y0 - 2 * y1 + y2
        if denom < 0:
            lag = i + 0.5 * (y0 - y2) / denom
    confidence = float(max(0.0, min(1.0, ac[i])))
    bpm = float(60.0 / (lag * frame_s))
    if confidence < MIN_BPM_CONFIDENCE or not (MIN_BPM <= bpm <= MAX_BPM):
        return None, round(confidence, 3)
    return round(bpm, 1), round(confidence, 3)


def analyze_audio(audio_path: Path) -> AudioAnalysis:
    samples = decode_mono(audio_path)
    bpm, confidence = estimate_bpm(samples)
    return AudioAnalysis(
        peaks=compute_peaks(samples),
        duration=round(samples.size / DECODE_RATE, 3),
        bpm=bpm,
        bpm_confidence=confidence,
    )


def needs_analysis(metadata: dict) -> bool:
    stamp = metadata.get("analysis")
    return not (isinstance(stamp, dict) and int(stamp.get("version") or 0) >= ANALYSIS_VERSION)


def analyze_sound_folder(folder: Path, audio_path: Path, *, force: bool = False) -> AudioAnalysis | None:
    """Analyze one sound and write waveform.json + metadata fields. Returns None if skipped.

    Raises :class:`AnalysisError` when the audio cannot be decoded; the caller decides
    whether that is fatal (it never is for import).
    """
    metadata_path = folder / "metadata.json"
    metadata = read_json_lenient(metadata_path, default={}) if metadata_path.exists() else {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not force and not needs_analysis(metadata) and (folder / WAVEFORM_FILENAME).exists():
        return None
    result = analyze_audio(audio_path)
    waveform_path = folder / WAVEFORM_FILENAME
    atomic_write_json(
        waveform_path,
        {"version": ANALYSIS_VERSION, "peaks": result.peaks, "duration": result.duration},
    )
    paths = dict(metadata.get("paths") if isinstance(metadata.get("paths"), dict) else {})
    paths["waveform"] = str(waveform_path)
    metadata["paths"] = paths
    metadata["bpm"] = result.bpm
    metadata["bpm_confidence"] = result.bpm_confidence
    if not metadata.get("duration_seconds") and result.duration:
        metadata["duration_seconds"] = result.duration
    metadata["analysis"] = {
        "version": ANALYSIS_VERSION,
        "analyzed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    atomic_write_json(metadata_path, metadata)
    return result


def load_peaks(path: Path | None) -> list[float] | None:
    """Read peaks from a waveform.json; None if missing or unreadable."""
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    peaks = payload.get("peaks") if isinstance(payload, dict) else None
    if not isinstance(peaks, list):
        return None
    try:
        return [float(p) for p in peaks]
    except (TypeError, ValueError):
        return None
