from __future__ import annotations

import json
from pathlib import Path

from sound_vault.ingest.download import PlaywrightCaptureDownloader
from sound_vault.ingest.package import package_sound
from sound_vault.ingest.resolve import ResolvedSource
from sound_vault.ingest.service import IngestService
from sound_vault.workers.oembed import fetch_sound_metadata


def _tagger(src, dst, tags):
    Path(dst).write_bytes(Path(src).read_bytes())


def _tiktok_music(url, music_id="123"):
    return ResolvedSource(
        input_url=url, final_url=url, platform="tiktok", kind="music",
        canonical_url=f"https://www.tiktok.com/music/x-{music_id}", source_id=music_id,
        slug="x", title_guess=None, share_music_id=None, status="ok",
    )


# ---- oEmbed helper ----

def test_fetch_sound_metadata_maps_fields():
    out = fetch_sound_metadata(
        "https://www.tiktok.com/music/x-1",
        fetch_json=lambda url: {"title": "paris in the rain", "author_name": "Lauv", "provider_name": "TikTok"},
    )
    assert out["title"] == "paris in the rain"
    assert out["author_name"] == "Lauv"
    assert out["provider_name"] == "TikTok"


def test_fetch_sound_metadata_swallows_errors():
    def boom(url):
        raise RuntimeError("network")
    assert fetch_sound_metadata("https://x", fetch_json=boom) == {}
    assert fetch_sound_metadata("") == {}


# ---- packager persists artwork + usage ----

def test_package_sound_writes_artwork_and_usage(tmp_path):
    audio = tmp_path / "src.m4a"
    audio.write_bytes(b"\x00audio")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8jpgdata")

    packaged = package_sound(
        vault_root=tmp_path / "vault",
        music_id="777",
        title="Hot Hook",
        artist="Creator",
        audio_path=audio,
        info={"cover_path": str(cover), "usage_count": 500000, "associated_video_count": 12, "source_provider": "TikTok"},
        tagger=_tagger,
        now_iso="2026-06-14T00:00:00Z",
    )
    md = packaged.metadata
    assert md["usage_count"] == 500000
    assert md["associated_video_count"] == 12
    assert md["source_provider"] == "TikTok"
    assert md["paths"]["artwork"] == "sounds/777 - Hot Hook - Creator/artwork.jpg"
    assert (packaged.folder / "artwork.jpg").exists()
    assert any(a.get("asset_type") == "artwork" for a in md["assets"])


# ---- Playwright downloader reads the capture sidecar ----

def test_playwright_downloader_reads_meta_sidecar(tmp_path):
    node_script = tmp_path / "capture.cjs"
    node_script.write_text("// fake")
    state = tmp_path / "state.json"
    state.write_text("{}")

    def fake_runner(cmd, cwd=None):
        # cmd = [node, script, url, dest_dir, music_id, state]
        dest = Path(cmd[3])
        music_id = cmd[4]
        (dest / f"{music_id}_raw.m4a").write_bytes(b"\x00\x00audio-bytes")
        (dest / f"{music_id}_cover.jpg").write_bytes(b"\xff\xd8cover")
        (dest / f"{music_id}_meta.json").write_text(json.dumps({
            "title": "original sound", "author": "tollan kim", "coverUrl": "https://x/c.jpg",
            "coverPath": f"{music_id}_cover.jpg", "usageCount": 1200000, "pageUrl": "https://www.tiktok.com/music/x-9",
        }))
        return (0, "", "")

    dl = PlaywrightCaptureDownloader(node_script=node_script, storage_state=state, runner=fake_runner)
    result = dl.download("https://www.tiktok.com/music/x-9", dest_dir=tmp_path / "work", basename="9", source_id="9")
    assert result.ok
    assert result.info["title"] == "original sound"
    assert result.info["artist"] == "tollan kim"
    assert result.info["usage_count"] == 1200000
    assert Path(result.info["cover_path"]).exists()


# ---- ingest service oEmbed fallback ----

class _ThinDownloader:
    """Returns audio but no title/author (the real TikTok capture-less case)."""

    def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        audio = Path(dest_dir) / f"{basename}.m4a"
        audio.write_bytes(b"\x00audio")
        return __import__("sound_vault.ingest.download", fromlist=["DownloadResult"]).DownloadResult(
            ok=True, audio_path=audio, info={"id": source_id, "title": "", "uploader": ""}, method="playwright"
        )


def test_ingest_oembed_fallback_fills_title_and_artist(tmp_path):
    svc = IngestService(
        vault_root=tmp_path / "vault",
        downloader=_ThinDownloader(),
        resolve_source=lambda u: _tiktok_music(u, "55"),
        tagger=_tagger,
        now=lambda: "2026-06-14T00:00:00Z",
        oembed_lookup=lambda url: {"title": "sunflower", "author_name": "Rex Orange County", "provider_name": "TikTok"},
    )
    out = svc.ingest_url("https://www.tiktok.com/t/abc/")
    assert out.status == "ingested"
    md = json.loads((out.folder / "metadata.json").read_text())
    assert md["tiktok_visible_title"] == "sunflower"
    assert md["tiktok_author_or_copyright"] == "Rex Orange County"


def test_ingest_uses_capture_metadata_over_oembed(tmp_path):
    """When the capture sidecar already provided rich info, oEmbed isn't needed."""
    cover = tmp_path / "c.jpg"
    cover.write_bytes(b"\xff\xd8jpg")

    class _RichDownloader:
        def download(self, url, *, dest_dir, basename, source_id=None, **kwargs):
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            audio = Path(dest_dir) / f"{basename}.m4a"
            audio.write_bytes(b"\x00audio")
            from sound_vault.ingest.download import DownloadResult
            return DownloadResult(ok=True, audio_path=audio, method="playwright", info={
                "id": source_id, "title": "real title", "uploader": "real creator", "artist": "real creator",
                "cover_path": str(cover), "usage_count": 999, "source_provider": "TikTok",
            })

    called = {"oembed": False}

    def _oembed(url):
        called["oembed"] = True
        return {"title": "WRONG", "author_name": "WRONG"}

    svc = IngestService(
        vault_root=tmp_path / "vault", downloader=_RichDownloader(),
        resolve_source=lambda u: _tiktok_music(u, "66"), tagger=_tagger,
        now=lambda: "2026-06-14T00:00:00Z", oembed_lookup=_oembed,
    )
    out = svc.ingest_url("https://www.tiktok.com/t/def/")
    md = json.loads((out.folder / "metadata.json").read_text())
    assert md["tiktok_visible_title"] == "real title"
    assert md["tiktok_author_or_copyright"] == "real creator"
    assert md["usage_count"] == 999
    assert called["oembed"] is False  # rich capture meta means no oEmbed call
