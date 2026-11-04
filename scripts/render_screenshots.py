"""Render the real desktop window, headless, at several window sizes.

Builds the deterministic fixture vault, analyses it (waveforms + BPM), opens
``SoundVaultWindow`` with ``QT_QPA_PLATFORM=offscreen`` and saves one PNG per
(size, view, state). CI uploads the folder as an artifact on every PR so a reviewer
can see a UI change without building the app, and ``--check`` fails the run when a
layout rule breaks (controls overlapping, text clipped, the track list empty).

    python scripts/render_screenshots.py out/            # all sizes
    python scripts/render_screenshots.py out/ --check    # + layout assertions
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _flag in (
    "SOUND_VAULT_DISABLE_AUTO_INDEX",
    "SOUND_VAULT_DISABLE_RELAY_POLL",
    "SOUND_VAULT_DISABLE_ONBOARDING",
    "SOUND_VAULT_DISABLE_TIKTOK_PROMPT",
    "SOUND_VAULT_DISABLE_ANALYSIS",
    "SOUND_VAULT_DISABLE_UPDATE_CHECK",
    "SOUND_VAULT_DISABLE_TRANSCRIBE",
    "SOUND_VAULT_DISABLE_AUDIO",
):
    os.environ.setdefault(_flag, "1")

# Window sizes: smallest supported → large external display.
SIZES: list[tuple[str, int, int]] = [
    ("1080x700", 1080, 700),  # the window's minimum size
    ("1280x800", 1280, 800),
    ("1440x900", 1440, 900),
    ("1920x1080", 1920, 1080),
    ("2560x1440", 2560, 1440),
]


def _overlaps(widgets, root) -> list[str]:
    """Visible filter-bar controls must not overlap (compared in ``root`` coordinates)."""
    from PySide6.QtCore import QPoint, QRect

    problems = []
    rects = [
        (w.objectName() or type(w).__name__, QRect(w.mapTo(root, QPoint(0, 0)), w.size()))
        for w in widgets
        if w.isVisible()
    ]
    for i, (name_a, a) in enumerate(rects):
        for name_b, b in rects[i + 1 :]:
            if a.intersects(b):
                problems.append(f"{name_a} overlaps {name_b}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--check", action="store_true", help="fail on layout problems")
    ap.add_argument("--count", type=int, default=40)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    from fixture_vault import FIXTURE_NOW, build_fixture_vault
    from PySide6.QtWidgets import QApplication, QWidget

    from sound_vault.vault.analysis import analyze_sound_folder

    work = Path(tempfile.mkdtemp(prefix="sc-shots-"))
    os.environ["SOUND_VAULT_DATA_DIR"] = str(work / "data")
    os.environ["SOUND_VAULT_CONFIG_DIR"] = str(work / "config")
    vault = work / "vault"
    for sound in build_fixture_vault(vault, count=args.count):
        if sound.has_audio:
            analyze_sound_folder(sound.folder, sound.folder / "audio.wav")

    app = QApplication.instance() or QApplication([])
    from sound_vault.ui import desktop

    desktop._load_brand_fonts()
    window = desktop.SoundVaultWindow(vault_root=vault)
    window.track_delegate.now = lambda: FIXTURE_NOW
    window.show()
    app.processEvents()
    window.vm.rebuild_index()
    window.refresh_table()
    window._update_sidebar_counts()  # what the app's index-finished handler does
    app.processEvents()

    problems: list[str] = []
    manifest = []
    playing = window.table.model().music_id_at(1)

    def shoot(name: str) -> None:
        app.processEvents()
        path = args.out / f"{name}.png"
        window.grab().save(str(path))
        manifest.append(path.name)

    for label, width, height in SIZES:
        window.resize(width, height)
        app.processEvents()
        # Track list, a row selected, one row "playing" 40% through.
        window.set_library_view_mode("list", persist=False)
        window.table.selectRow(2)
        window._set_playing(playing)
        window.progress_slider.setRange(0, 1000)
        window.progress_slider.setValue(400)
        shoot(f"{label}-list")
        window.set_library_density("compact")
        shoot(f"{label}-list-compact")
        window.set_library_density("comfortable")
        window._set_playing(None)
        window.set_library_view_mode("table", persist=False)
        shoot(f"{label}-table")
        window.set_library_view_mode("list", persist=False)

        if args.check:
            bar = [
                window.duration_filter, window.media_filter, window.status_filter, window.usage_filter,
                window.bpm_filter, window.vocals_only, window.sort_combo, window.view_mode_button,
                window.result_count_label, window.library_view.findChild(QWidget, "columnsButton"),
            ]
            bar = [w for w in bar if w is not None]
            for w in bar:
                if w.isVisible() and w.width() < w.minimumSizeHint().width() - 1:
                    problems.append(f"{label}: {w.objectName() or type(w).__name__} squeezed to {w.width()} px")
            problems += [f"{label}: {p}" for p in _overlaps(bar, window.library_view)]
            if window.track_list.model().rowCount() == 0:
                problems.append(f"{label}: track list is empty")
            first = window.track_list.visualRect(window.track_list.model().index(0, desktop.SOUND_COL))
            if first.width() < 300:
                problems.append(f"{label}: track list rows only {first.width()} px wide")
            if window.track_list.horizontalScrollBar().isVisible():
                problems.append(f"{label}: track list scrolls sideways")

    # Other main views at the default size.
    window.resize(1440, 900)
    for view in ("inbox", "review", "dedupe", "worker"):
        window.show_view(view)
        shoot(f"1440x900-{view}")
    window.show_view("library")

    (args.out / "manifest.json").write_text(json.dumps({"images": manifest, "problems": problems}, indent=2))
    print(f"wrote {len(manifest)} screenshots to {args.out}")
    for problem in problems:
        print(f"LAYOUT: {problem}")
    window.close()
    return 1 if (args.check and problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
