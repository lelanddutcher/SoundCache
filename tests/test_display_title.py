"""Song names for sounds whose visible title is only the creator, and phantom transcripts."""
from __future__ import annotations

from types import SimpleNamespace

from sound_vault.vault.indexer import display_title, has_speech


def _r(title, artist, page=""):
    return SimpleNamespace(title=title, artist=artist, music_page_title=page, music_id="1")


def test_song_name_comes_from_the_music_page_when_title_is_the_creator():
    assert display_title(_r("Keith Whitley", "Keith Whitley", "(13)Keith Whitley - Miami, My Amy | TikTok")) == "Miami, My Amy"


def test_localized_original_sound_reads_as_original_sound():
    assert display_title(_r("Gabriel", "Gabriel", "(4)Gabriel - Originalton | TikTok")) == "original sound"


def test_a_real_title_is_kept_and_a_missing_page_falls_back():
    assert display_title(_r("Sweater Weather", "The Neighbourhood", "(1)x - y | TikTok")) == "Sweater Weather"
    assert display_title(_r("EXR", "EXR")) == "EXR"


def test_phantom_transcripts_are_not_speech():
    for text in ("Music Music Music Music", " you", "Thank you.", "[Music]", "", None):
        assert not has_speech(text), text
    assert has_speech("I said hey, what's going on")
