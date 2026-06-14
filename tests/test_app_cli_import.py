from __future__ import annotations

import json
from types import SimpleNamespace
import sys


def test_app_module_import_does_not_require_gui_libraries():
    import sound_vault.app as app

    assert callable(app.main)


def test_diagnose_mode_does_not_import_gui_libraries(monkeypatch, tmp_path, capsys):
    import sound_vault.app as app

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SOUND_VAULT_DEFAULT_VAULT", str(vault))
    monkeypatch.setattr(sys, "argv", ["sound-vault", "--diagnose"])
    sys.modules.pop("sound_vault.ui.desktop", None)

    app.main()

    output = capsys.readouterr().out
    assert "Sound Cache diagnostics" in output
    assert "config dir:" in output
    assert "data dir:" in output
    assert "index path:" in output
    assert "vault root:" in output
    assert "sound_vault.ui.desktop" not in sys.modules

    events = [
        json.loads(line)
        for line in (data_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["app.start", "app.diagnose_complete"]


def test_import_favorite_sounds_cli_does_not_import_gui_libraries(monkeypatch, tmp_path, capsys):
    import sound_vault.app as app

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    export = tmp_path / "favorite sounds list.json"
    export.write_text(
        'Favorite Sounds": {"FavoriteSoundList": ['
        '{"Date": "2026-05-02 15:58:30", '
        '"Link": "https://m.tiktok.com/h5/share/music/6817565543474661378.html"}'
        "]},",
        encoding="utf-8",
    )
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sound-vault",
            "--vault",
            str(vault),
            "--import-favorite-sounds",
            str(export),
            "--import-date-label",
            "2026-05-18",
        ],
    )
    sys.modules.pop("sound_vault.ui.desktop", None)

    app.main()

    output = capsys.readouterr().out
    assert "Imported TikTok favorite sounds export" in output
    assert (vault / "catalog/imports/favorite_sounds_import_normalized_2026-05-18.json").exists()
    assert (vault / "catalog/imports/favorite_sounds_import_normalized_2026-05-18.csv").exists()
    assert "sound_vault.ui.desktop" not in sys.modules


def test_package_imported_sounds_cli_does_not_import_gui_libraries(monkeypatch, tmp_path, capsys):
    import sound_vault.app as app

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    imported = tmp_path / "favorite_sounds_oembed_enriched_2026-05-18.json"
    imported.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "tiktok_music_id": "88",
                        "canonical_url_guess": "https://www.tiktok.com/music/-88",
                        "oembed_status": "ok",
                        "oembed_title": "CLI Package",
                        "oembed_author_name": "Vault Tester",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sound-vault",
            "--vault",
            str(vault),
            "--package-imported-sounds",
            str(imported),
        ],
    )
    sys.modules.pop("sound_vault.ui.desktop", None)

    app.main()

    output = capsys.readouterr().out
    assert "Packaged imported TikTok sounds" in output
    assert (vault / "sounds" / "88 - CLI Package - Vault Tester" / "metadata.json").exists()
    assert (vault / "catalog" / "sounds.jsonl").exists()
    assert "sound_vault.ui.desktop" not in sys.modules


def test_oembed_enrichment_cli_uses_worker_without_importing_gui(monkeypatch, tmp_path, capsys):
    import sound_vault.app as app
    import sound_vault.workers.oembed as oembed

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    normalized = tmp_path / "favorite_sounds_import_normalized_2026-05-18.json"
    normalized.write_text(json.dumps({"records": [{"tiktok_music_id": "77"}]}), encoding="utf-8")
    json_path = vault / "catalog" / "imports" / "favorite_sounds_oembed_enriched_2026-05-18.json"
    csv_path = vault / "catalog" / "imports" / "favorite_sounds_oembed_enriched_2026-05-18.csv"

    def fake_enrich(input_path, out_dir, *, date_label=None, delay_seconds=0.6):
        assert input_path == normalized
        assert out_dir == vault / "catalog" / "imports"
        assert date_label == "2026-05-18"
        assert delay_seconds == 0
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps({"records": []}), encoding="utf-8")
        csv_path.write_text("tiktok_music_id\n", encoding="utf-8")
        return SimpleNamespace(
            json_path=json_path,
            csv_path=csv_path,
            summary=SimpleNamespace(record_count=1, ok_count=1, error_count=0, resumed_count=0),
        )

    monkeypatch.setattr(oembed, "enrich_favorite_sounds_oembed", fake_enrich)
    monkeypatch.setenv("SOUND_VAULT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SOUND_VAULT_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sound-vault",
            "--vault",
            str(vault),
            "--enrich-favorite-sounds-oembed",
            str(normalized),
            "--import-date-label",
            "2026-05-18",
            "--oembed-delay",
            "0",
        ],
    )
    sys.modules.pop("sound_vault.ui.desktop", None)

    app.main()

    output = capsys.readouterr().out
    assert "Enriched TikTok favorite sounds through public oEmbed" in output
    assert "sound_vault.ui.desktop" not in sys.modules
