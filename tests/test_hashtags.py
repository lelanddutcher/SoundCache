from sound_vault.vault.hashtags import extract_hashtags, extract_hashtags_from_video_record


def test_extract_hashtags_normalizes_unicode_and_adjacent_tags():
    assert extract_hashtags("Fortnite #CapCut #балет#рекомендации https://example.com/#skip") == (
        "capcut",
        "балет",
        "рекомендации",
    )


def test_extract_hashtags_from_video_record_uses_card_and_description_text():
    record = {
        "description": "Main caption #FilmTok",
        "music_page_card": {"itemText": "creator\n\nclip card #EditTok #filmtok"},
    }

    assert extract_hashtags_from_video_record(record) == ("filmtok", "edittok")
