"""Durable/corruption-tolerant JSON I/O — the fix for NFS NUL-padded metadata.json.

The vault lives on a JuiceFS-over-NFS mount whose writes can intermittently hand a
reader valid JSON followed by a run of NUL padding (close-to-open consistency). A
bare ``json.loads`` then dies with "Extra data" and the sound reads as broken — this
is exactly what made a right-click Re-transcribe report "1 failed". ``read_json_lenient``
must recover such files; ``atomic_write_json`` must round-trip cleanly.
"""
import json

import pytest

from sound_vault.vault.metadata_io import atomic_write_json, read_json_lenient


def test_roundtrip_clean(tmp_path):
    p = tmp_path / "metadata.json"
    data = {"music_id": "123", "identity": {"title": "x"}, "n": [1, 2, 3]}
    atomic_write_json(p, data)
    assert read_json_lenient(p) == data
    # a clean write must also satisfy a strict reader
    assert json.loads(p.read_text(encoding="utf-8")) == data


def test_recovers_nul_padded_tail(tmp_path):
    """The exact production corruption: valid JSON + a wall of \\x00."""
    p = tmp_path / "metadata.json"
    good = {"music_id": "7656057508091480839", "audit": {"missing_transcript": False}}
    p.write_bytes(json.dumps(good).encode("utf-8") + b"\x00" * 1009)
    with pytest.raises(json.JSONDecodeError):
        json.loads(p.read_text(encoding="utf-8"))  # proves a bare reader fails
    assert read_json_lenient(p) == good              # lenient reader recovers it


def test_recovers_whitespace_and_replacement_char_tail(tmp_path):
    p = tmp_path / "m.json"
    good = {"a": 1}
    p.write_bytes(json.dumps(good).encode("utf-8") + b"\r\n\x00 \t")
    assert read_json_lenient(p) == good
    # An invalid-UTF8 tail byte decodes to U+FFFD and is also stripped.
    p.write_bytes(json.dumps(good).encode("utf-8") + b"\xff\xfe")
    assert read_json_lenient(p) == good


def test_takes_first_value_on_concatenated_garbage(tmp_path):
    """If a torn write leaves a second partial object, keep the first complete one."""
    p = tmp_path / "m.json"
    p.write_text('{"a": 1}{"b": 2}garbage', encoding="utf-8")
    assert read_json_lenient(p) == {"a": 1}


def test_default_on_unrecoverable_else_raises(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("not json at all", encoding="utf-8")
    assert read_json_lenient(p, default=None) is None
    assert read_json_lenient(p, default={"fallback": True}) == {"fallback": True}
    with pytest.raises(json.JSONDecodeError):
        read_json_lenient(p)


def test_transcribe_reads_padded_metadata(tmp_path):
    """End-to-end guard on the reported bug: transcribe_sound_folder must NOT return
    'unreadable metadata' for a NUL-padded metadata.json — it should read past the pad
    and proceed to the (here, stubbed) transcriber."""
    from sound_vault.workers.transcription import transcribe_sound_folder

    folder = tmp_path / "7656057508091480839 - original sound"
    folder.mkdir()
    meta = {"music_id": "7656", "identity": {"title": "t"}, "paths": {}}
    (folder / "metadata.json").write_bytes(json.dumps(meta).encode("utf-8") + b"\x00" * 512)
    audio = folder / "sound [ingested].m4a"
    audio.write_bytes(b"\x00\x01\x02\x03")  # non-empty; transcriber is stubbed

    def fake_transcriber(path, **kw):
        return {"text": "hello world", "language": "en", "model": "test", "engine": "test"}

    res = transcribe_sound_folder(folder, audio_path=audio, transcriber=fake_transcriber, overwrite=True)
    assert res["status"] == "ok", res
    assert "unreadable metadata" not in str(res.get("reason", ""))
    # And the worker's own rewrite heals the file: a strict reader now succeeds.
    assert json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["music_id"] == "7656"
