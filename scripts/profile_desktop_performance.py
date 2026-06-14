from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from time import perf_counter


def _measure(label: str, callback) -> dict[str, object]:
    start = perf_counter()
    result = callback()
    elapsed_ms = round((perf_counter() - start) * 1000, 2)
    payload = {"label": label, "elapsed_ms": elapsed_ms}
    if isinstance(result, dict):
        payload.update(result)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile Sound Vault desktop startup/search paths.")
    parser.add_argument("--vault", required=True, help="Path to the TikTok Sound Vault root or sounds folder.")
    parser.add_argument("--query", action="append", default=["b", "bi", "bitch", "needle"])
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the SQLite cache before measuring UI refresh.")
    parser.add_argument("--fail-search-ms", type=float, default=0.0)
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("SOUND_VAULT_DISABLE_AUTO_INDEX", "1")

    from PySide6.QtWidgets import QApplication

    from sound_vault.ui.desktop import SoundVaultWindow

    app = QApplication.instance() or QApplication([])
    window = SoundVaultWindow(vault_root=Path(args.vault))
    window.show()
    app.processEvents()
    results: list[dict[str, object]] = []
    if args.rebuild:
        results.append(
            _measure(
                "rebuild_index",
                lambda: {"indexed": window.vm.rebuild_index()},
            )
        )
    results.append(
        _measure(
            "initial_refresh",
            lambda: (window.refresh_table(), app.processEvents(), {"rows": window.table.rowCount()})[-1],
        )
    )
    search_results = []
    for query in args.query:
        window.search_box.setText(query)
        result = _measure(
            f"search:{query}",
            lambda: (window.refresh_table(), app.processEvents(), {"rows": window.table.rowCount()})[-1],
        )
        search_results.append(result)
        results.append(result)
    window.close()
    print(json.dumps({"vault": str(Path(args.vault)), "results": results}, indent=2))
    if args.fail_search_ms:
        slow = [row for row in search_results if float(row["elapsed_ms"]) > args.fail_search_ms]
        if slow:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
