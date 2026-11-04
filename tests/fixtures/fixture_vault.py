"""Build a synthetic Sound Cache vault for tests, screenshots and benchmarks.

Every sound is generated: click-track audio at a known tempo (so BPM detection has an
oracle), flat-color PNG artwork, and a metadata.json in the layout the indexer reads.
No real or copyrighted audio is involved, and the output is deterministic for a given
seed, so screenshots of it are stable.

    python tests/fixtures/fixture_vault.py /tmp/vault            # 50 sounds
    python tests/fixtures/fixture_vault.py /tmp/vault --count 20000 --no-audio   # perf

The mix covers every row state on the design boards: playable, missing audio,
not transcribed, instrumental (empty transcript sidecar), transcribed, favorites,
NEW (saved in the last 7 days), with and without artwork and example videos.
"""
from __future__ import annotations

import argparse
import json
import struct
import wave
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

# Fixed "now" so NEW badges and relative dates never drift between runs.
FIXTURE_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SAMPLE_RATE = 16_000

_TITLES = [
    "sahara phonk", "oh no oh no oh no", "butterfly y2k bounce", "rainy lofi study",
    "main character walk", "sped up heartbreak", "cinematic drone swell", "brazilian funk loop",
    "tell me why (acapella)", "retro arcade coin", "midnight drift", "glitter gloss",
    "fit check bounce", "slow-mo outfit", "storytime piano", "gym hype intro",
    "beach day ukulele", "sad violin meme", "dramatic zoom", "kawaii synth pop",
    "trap snare roll", "vinyl crackle loop", "jersey club remix", "hyperpop sparkle",
    "a very long sound title that should truncate cleanly in every column and pane",
]
_ARTISTS = [
    "Memphis Cowbell", "Kreepa", "2k babie", "night bloom", "original sound · @lena.edits",
    "velvet.mp3", "original sound · @atlas.fpv", "MC Fernanda", "Juno Bay", "pixelpush",
]
_TAGS = [
    ["phonk", "drift", "car edits", "hype"], ["meme", "comedy", "fails"], ["y2k", "bounce", "dance"],
    ["lofi", "chill", "study"], ["trend", "pov", "slow-mo"], ["sped up", "pop", "sad"],
    ["cinematic", "ambient", "travel"], ["funk", "brazil", "transition"], ["acapella", "r&b"],
    ["sfx", "retro", "game"],
]
_TILES = [(0x3B, 0x1D, 0x6E), (0x1D, 0x3B, 0x6E), (0x6E, 0x1D, 0x4F), (0x1D, 0x5A, 0x6E), (0x5A, 0x3B, 0x1D)]
# Tempos chosen to be distinct and inside the detector's 60–200 BPM range.
_TEMPOS = [70, 82, 90, 96, 100, 108, 115, 120, 128, 135, 140, 150, 160, 174]


@dataclass(frozen=True)
class FixtureSound:
    music_id: str
    title: str
    folder: Path
    bpm: int | None
    has_audio: bool
    transcript: str  # "available" | "empty" | "pending" | "no_audio"


def _png(path: Path, rgb: tuple[int, int, int], size: int = 64) -> None:
    """Write a solid-color RGB PNG with the stdlib only."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def click_track(bpm: float, seconds: float, *, seed: int = 0, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """A kick and hat on every beat, accented on the downbeat, a little noise. int16 mono.

    No off-beat hits on purpose: an off-beat hat makes double tempo a defensible
    answer, and the fixture is the BPM oracle, so it must have exactly one."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sample_rate)
    out = np.zeros(n, dtype=np.float64)
    beat = 60.0 / bpm
    i = np.arange(int(0.06 * sample_rate))
    env = np.exp(-i / (0.012 * sample_rate))
    hit = env * (0.8 * np.sin(2 * np.pi * 90.0 * i / sample_rate) + 0.2 * np.sin(2 * np.pi * 2400.0 * i / sample_rate))
    for beat_no, t in enumerate(np.arange(0.0, seconds, beat)):
        accent = 1.0 if beat_no % 4 == 0 else 0.7
        start = int(t * sample_rate)
        stop = min(n, start + hit.size)
        if start < stop:
            out[start:stop] += accent * hit[: stop - start]
    # A slow swell so waveforms are not flat bars.
    out *= 0.55 + 0.45 * np.sin(np.pi * np.arange(n) / n)
    out += rng.uniform(-0.015, 0.015, n)
    return np.clip(out * 30000, -32767, 32767).astype("<i2")


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())


def build_fixture_vault(root: Path, *, count: int = 50, with_audio: bool = True, seed: int = 7) -> list[FixtureSound]:
    """Create ``root/sounds/<id> - <title>/`` folders and return what was made."""
    sounds_root = root / "sounds"
    sounds_root.mkdir(parents=True, exist_ok=True)
    made: list[FixtureSound] = []
    for i in range(count):
        music_id = f"7{3600000000000000000 + i * 7919:018d}"[:19]
        title = _TITLES[i % len(_TITLES)] + ("" if i < len(_TITLES) else f" #{i // len(_TITLES) + 1}")
        artist = _ARTISTS[i % len(_ARTISTS)]
        folder = sounds_root / f"{music_id} - {title[:40]}"
        folder.mkdir(parents=True, exist_ok=True)

        # State mix (indices chosen so every state appears in the first 12 rows).
        missing_audio = i % 11 == 5
        transcript = "no_audio" if missing_audio else ("empty" if i % 7 == 3 else ("pending" if i % 5 == 1 else "available"))
        bpm = None if i % 13 == 9 else _TEMPOS[i % len(_TEMPOS)]
        seconds = [9.0, 12.0, 15.0, 23.0, 31.0][i % 5]
        has_audio = with_audio and not missing_audio

        paths: dict[str, str] = {"folder": str(folder)}
        if has_audio:
            audio = folder / "audio.wav"
            _write_wav(audio, click_track(bpm or 97, seconds, seed=i))
            paths["audio"] = str(audio)
        if i % 9 != 4:
            art = folder / "artwork.png"
            _png(art, _TILES[i % len(_TILES)])
            paths["artwork"] = str(art)

        data: dict[str, object] = {
            "tiktok_music_id": music_id,
            "tiktok_visible_title": title,
            "source_artist": artist,
            "tags": _TAGS[i % len(_TAGS)],
            "status": "needs_review" if i % 17 == 2 else "unreviewed",
            "saved_at": (FIXTURE_NOW - timedelta(days=(i * 3) % 40, hours=i)).isoformat(),
            "usage_count": None if i % 19 == 7 else int(10 ** (2 + (i * 37 % 50) / 10)),
            "duration_seconds": seconds,
            "associated_video_count": (i * 5) % 31 if i % 4 else 0,
            "canonical_url": f"https://www.tiktok.com/music/sound-{music_id}",
            "_fixture": {"bpm": bpm, "transcript": transcript, "seed": seed},
        }
        if transcript == "available":
            data["transcript"] = {"text": f"[fixture] words for {title}", "language": "en"}
        elif transcript == "empty":
            sidecar = folder / "transcript.json"
            sidecar.write_text(json.dumps({"text": "", "language": ""}))
            paths["transcript"] = str(sidecar)
        data["paths"] = paths
        (folder / "metadata.json").write_text(json.dumps(data, indent=2))
        made.append(FixtureSound(music_id, title, folder, bpm, has_audio, transcript))
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--no-audio", action="store_true", help="skip audio files (fast, for perf runs)")
    args = ap.parse_args(argv)
    made = build_fixture_vault(args.root, count=args.count, with_audio=not args.no_audio)
    print(f"wrote {len(made)} sounds to {args.root / 'sounds'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
