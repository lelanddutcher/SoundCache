import json

from sound_vault.vault.library_collections import LibraryCollectionsStore


def test_library_collections_round_trip_favorites_and_bins(tmp_path):
    vault = tmp_path / "vault"
    store = LibraryCollectionsStore(vault)

    assert store.toggle_favorite("123") is True
    assert store.toggle_favorite("456") is True
    assert store.favorites() == ("123", "456")
    assert store.toggle_favorite("123") is False
    assert store.favorites() == ("456",)

    bin_row = store.create_bin("Football Hype")
    assert bin_row.name == "Football Hype"
    assert store.add_to_bin(bin_row.id, "456") is True
    assert store.add_to_bin(bin_row.id, "456") is True
    assert store.bin_music_ids(bin_row.id) == ("456",)

    persisted = json.loads((vault / "catalog" / "library_collections.json").read_text(encoding="utf-8"))
    assert persisted["favorites"] == ["456"]
    assert persisted["bins"][0]["music_ids"] == ["456"]


def test_library_collections_ignores_corrupt_or_duplicate_values(tmp_path):
    vault = tmp_path / "vault"
    path = vault / "catalog" / "library_collections.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "favorites": ["1", "1", "", None, "2"],
                "bins": [{"id": "bin-a", "name": "A", "music_ids": ["2", "2", "3"]}],
            }
        ),
        encoding="utf-8",
    )

    store = LibraryCollectionsStore(vault)

    assert store.favorites() == ("1", "2")
    assert store.bins()[0].music_ids == ("2", "3")
