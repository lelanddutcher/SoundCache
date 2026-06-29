import json
from pathlib import Path

from sound_vault.ingest.package import PackagedSound, build_human_filename, package_sound
from sound_vault.vault.indexer import build_index


def fake_tagger(src, dst, tags):
    Path(dst).write_bytes(Path(src).read_bytes())


def test_package_creates_folder_and_metadata(tmp_path):
    pkg = package_sound(
        vault_root=tmp_path,
        music_id="123",
        title="Kickoff",
        artist="Creator",
        canonical_url="https://www.tiktok.com/music/Kickoff-123",
        audio_path=None,
        status="ingested",
        tags=["from_shortcut"],
        now_iso="2026-06-13T00:00:00Z",
    )
    assert isinstance(pkg, PackagedSound)
    assert pkg.folder == tmp_path / "sounds" / "123 - Kickoff - Creator"
    assert pkg.folder.is_dir()
    meta = json.loads((pkg.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["vault_version"] == 1
    assert meta["tiktok_music_id"] == "123"
    assert meta["status"] == "ingested"
    assert meta["tags"] == ["from_shortcut"]
    assert meta["paths"]["folder"] == "sounds/123 - Kickoff - Creator"
    assert meta["packaged_at"] == "2026-06-13T00:00:00Z"
    assert meta["canonical_url"] == "https://www.tiktok.com/music/Kickoff-123"


def test_package_appends_one_catalog_row(tmp_path):
    package_sound(vault_root=tmp_path, music_id="123", title="K", artist="C", audio_path=None, now_iso="t")
    catalog = tmp_path / "catalog" / "sounds.jsonl"
    rows = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["tiktok_music_id"] == "123"


def test_package_with_audio_moves_and_tags(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "123.m4a"
    src.write_bytes(b"\x00audio-bytes")
    pkg = package_sound(
        vault_root=tmp_path,
        music_id="123",
        title="Kickoff",
        artist="Creator",
        audio_path=src,
        status="ingested",
        tagger=fake_tagger,
        now_iso="t",
    )
    assert pkg.audio_path is not None
    assert pkg.audio_path.exists()
    assert pkg.audio_path.suffix == ".m4a"
    assert pkg.audio_path.parent == pkg.folder
    assert not src.exists()  # moved out of the temp dir
    meta = json.loads((pkg.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["paths"]["audio"].startswith("sounds/123 - Kickoff - Creator/")
    assert meta["paths"]["audio"].endswith(".m4a")


def test_package_sanitizes_illegal_chars(tmp_path):
    pkg = package_sound(
        vault_root=tmp_path,
        music_id="9",
        title='Hello/World: ♬ test',
        artist='Bad|Name"',
        audio_path=None,
        now_iso="t",
    )
    name = pkg.folder.name
    assert name.startswith("9 - ")
    for bad in '/:*?"<>|':
        assert bad not in name


def test_sanitize_normalizes_to_nfc():
    import unicodedata

    from sound_vault.ingest.package import sanitize_filename_component

    nfd = unicodedata.normalize("NFD", "Café")  # decomposed (what macOS hands back)
    out = sanitize_filename_component(nfd)
    assert out == "Café"
    assert unicodedata.normalize("NFC", out) == out  # portable canonical form


def test_sanitize_byte_cap_keeps_valid_utf8_under_limit():
    from sound_vault.ingest.package import sanitize_filename_component

    out = sanitize_filename_component("🔥" * 100, max_len=100, max_bytes=40)
    assert len(out.encode("utf-8")) <= 40
    # Truncated on a whole-codepoint boundary (re-decodes cleanly, no replacement char).
    assert out.encode("utf-8").decode("utf-8") == out
    assert "�" not in out


def test_portable_folder_name_stays_under_name_max_and_keeps_id_prefix():
    from sound_vault.ingest.package import portable_folder_name

    name = portable_folder_name("7209633324539693830", "🔥" * 80, "🎵" * 80)
    assert len(name.encode("utf-8")) <= 200  # safely under the 255-byte NAME_MAX
    assert name.startswith("7209633324539693830 - ")  # indexer globs on this prefix


def test_build_human_filename_byte_capped():
    name = build_human_filename("🔥" * 80, "🎵" * 80, "7209633324539693830", "ingested")
    assert len(name.encode("utf-8")) <= 255
    assert name.endswith(".m4a")


def test_is_portable_filename_flags_copy_breakers():
    from sound_vault.ingest.package import is_portable_filename

    assert is_portable_filename("7209633324539693830 - sound - (~￣³￣)~")  # valid UTF-8 kaomoji
    assert is_portable_filename("🔥 keep emoji")
    assert not is_portable_filename("has​zero​width")  # Cf format chars (ZWSP)
    assert not is_portable_filename("ends with ￴")  # Unicode non-character
    assert not is_portable_filename("a" * 300)  # > 255 bytes


def test_zwj_emoji_sequences_are_preserved():
    """The Zero Width Joiner (U+200D, Cf) is load-bearing for emoji ZWJ sequences and
    must NOT be stripped — dropping it splits 👩🏽‍🍳 into 👩🏽 + 🍳."""
    from sound_vault.ingest.package import is_portable_filename, sanitize_filename_component

    for emoji in ("👩🏽‍🍳", "🐈‍⬛", "❤️‍🔥", "🧚🏽‍♀️"):
        assert "‍" in emoji  # sanity: these are real ZWJ sequences
        assert sanitize_filename_component(emoji) == emoji  # untouched
        assert is_portable_filename(emoji)  # not flagged for repair


def test_package_handles_unicode_noncharacters_without_eilseq(tmp_path):
    from sound_vault.ingest.package import sanitize_filename_component

    # U+FFF4 (unassigned non-character) + a zero-width char caused EILSEQ on mkdir.
    assert sanitize_filename_component("￴") == "untitled"
    assert sanitize_filename_component("zero​width") == "zerowidth"
    assert sanitize_filename_component("🔥 keep emoji") == "🔥 keep emoji"  # emoji preserved
    pkg = package_sound(
        vault_root=tmp_path,
        music_id="7013196724253280258",
        title="sound",
        artist="￴",  # the exact artist that broke the user's import
        audio_path=None,
        now_iso="t",
    )
    assert pkg.folder.exists()  # mkdir succeeded (no Illegal byte sequence)
    assert "￴" not in pkg.folder.name


def test_packaged_sound_is_indexable(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    src = raw / "123.m4a"
    src.write_bytes(b"\x00audio")
    package_sound(
        vault_root=tmp_path,
        music_id="123",
        title="Kickoff",
        artist="Creator",
        canonical_url="https://www.tiktok.com/music/Kickoff-123",
        audio_path=src,
        status="ingested",
        tags=["from_shortcut"],
        tagger=fake_tagger,
        now_iso="t",
    )
    records = build_index(tmp_path)
    assert len(records) == 1
    rec = records[0]
    assert rec.music_id == "123"
    assert rec.title == "Kickoff"
    assert rec.artist == "Creator"
    assert rec.status == "ingested"
    assert rec.local_audio_path is not None and rec.local_audio_path.exists()
    assert "from_shortcut" in rec.tags
    assert rec.canonical_url == "https://www.tiktok.com/music/Kickoff-123"


def test_package_without_audio_is_indexable(tmp_path):
    pkg = package_sound(vault_root=tmp_path, music_id="5", title="NoAudio", artist="X", audio_path=None, now_iso="t")
    meta = json.loads((pkg.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["paths"]["audio"] is None
    records = build_index(tmp_path)
    assert records[0].music_id == "5"
    assert records[0].local_audio_path is None


def test_package_can_skip_catalog(tmp_path):
    package_sound(vault_root=tmp_path, music_id="7", title="T", artist="A", audio_path=None, append_catalog=False, now_iso="t")
    catalog = tmp_path / "catalog" / "sounds.jsonl"
    assert not catalog.exists() or catalog.read_text(encoding="utf-8").strip() == ""


def test_package_non_tiktok_platform(tmp_path):
    pkg = package_sound(
        vault_root=tmp_path,
        music_id="dQw4w9WgXcQ",
        title="Song",
        artist="Band",
        platform="youtube",
        source_url="https://youtu.be/dQw4w9WgXcQ",
        audio_path=None,
        now_iso="t",
    )
    assert pkg.folder.name.startswith("dQw4w9WgXcQ - ")
    meta = json.loads((pkg.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["platform"] == "youtube"
    assert meta["source_url"] == "https://youtu.be/dQw4w9WgXcQ"


def test_build_human_filename_is_safe():
    name = build_human_filename("Some / Title ♬", "An Artist", "999", "ingested")
    assert name.endswith(".m4a")
    assert "[TT-999]" in name
    for bad in '/:*?"<>|':
        assert bad not in name
