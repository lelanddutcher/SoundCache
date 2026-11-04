"""Benchmark the Library with a large fixture vault (default 20,000 sounds).

Measures what a big library feels like: index rebuild, the refresh after a search
keystroke or sort change, the first paint of the track list, and the time per frame
while scrolling through it. Prints JSON; with --check, fails when a budget is blown.

    python scripts/bench_library.py                 # 20k sounds, report only
    python scripts/bench_library.py --check         # CI: enforce the budgets below
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for _flag in (
    "SOUND_VAULT_DISABLE_AUTO_INDEX", "SOUND_VAULT_DISABLE_RELAY_POLL", "SOUND_VAULT_DISABLE_ONBOARDING",
    "SOUND_VAULT_DISABLE_TIKTOK_PROMPT", "SOUND_VAULT_DISABLE_ANALYSIS", "SOUND_VAULT_DISABLE_UPDATE_CHECK",
    "SOUND_VAULT_DISABLE_TRANSCRIBE", "SOUND_VAULT_DISABLE_AUDIO",
):
    os.environ.setdefault(_flag, "1")

# Budgets for --check, in milliseconds. Generous for shared CI runners; the point is
# to catch an O(n²) or per-paint disk hit, not to measure a fast Mac.
# Measured 2026-11-04 on a Linux CI-class box: refresh ~330 ms, keystroke ~520 ms,
# sort ~820 ms (two refreshes), scroll p95 ~11 ms. Before the ID-only search the
# first three were 2.2 s / 2.2 s / 5.4 s.
BUDGETS_MS = {
    "refresh_table": 1000,       # full model reset over 20k rows
    "search_keystroke": 1200,    # type one character into search
    "sort_change": 2000,
    "first_paint": 400,          # first track-list frame
    "scroll_frame_p95": 50,      # one viewport repaint while scrolling (< 20 fps would be bad)
}


def _ms(fn) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=20_000)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    from fixture_vault import build_fixture_vault
    from PySide6.QtWidgets import QApplication

    work = Path(tempfile.mkdtemp(prefix="sc-bench-"))
    os.environ["SOUND_VAULT_DATA_DIR"] = str(work / "data")
    os.environ["SOUND_VAULT_CONFIG_DIR"] = str(work / "config")
    results: dict[str, float] = {}
    results["build_fixture_s"] = round(_ms(lambda: build_fixture_vault(work / "vault", count=args.count, with_audio=False)) / 1000, 1)

    app = QApplication.instance() or QApplication([])
    from sound_vault.ui import desktop

    window = desktop.SoundVaultWindow(vault_root=work / "vault")
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    results["rebuild_index_s"] = round(_ms(window.vm.rebuild_index) / 1000, 2)
    results["refresh_table"] = _ms(lambda: (window.refresh_table(), app.processEvents()))
    results["rows"] = window.table.model().rowCount()

    window.search_box.blockSignals(True)
    window.search_box.setText("s")
    window.search_box.blockSignals(False)
    results["search_keystroke"] = _ms(lambda: (window.refresh_table(), app.processEvents()))
    window.search_box.setText("")
    window.refresh_table()

    results["sort_change"] = _ms(lambda: (window._apply_sort_choice(3), window.refresh_table(), app.processEvents()))

    view = window.track_list
    view.viewport().update()
    results["first_paint"] = _ms(lambda: view.viewport().repaint())

    bar = view.verticalScrollBar()
    frames = []
    step = max(1, bar.pageStep() // 2)
    for value in range(0, min(bar.maximum(), step * 300), step):
        bar.setValue(value)
        frames.append(_ms(lambda: view.viewport().repaint()))
    frames.sort()
    results["scroll_frames"] = len(frames)
    results["scroll_frame_median"] = statistics.median(frames)
    results["scroll_frame_p95"] = frames[int(len(frames) * 0.95) - 1]

    results = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in results.items()}
    over = {k: (results[k], budget) for k, budget in BUDGETS_MS.items() if results[k] > budget}
    report = {"count": args.count, "results": results, "budgets_ms": BUDGETS_MS, "over_budget": over}
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)
    window.close()
    return 1 if (args.check and over) else 0


if __name__ == "__main__":
    raise SystemExit(main())
