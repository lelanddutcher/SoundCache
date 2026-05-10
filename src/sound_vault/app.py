from __future__ import annotations

import argparse
from pathlib import Path

from sound_vault.settings import AppSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Sound Vault desktop app")
    parser.add_argument("--vault", type=Path, default=None, help="Path to a Sound Vault folder")
    parser.add_argument("--cli", action="store_true", help="Print index count instead of opening the GUI")
    args = parser.parse_args()
    vault_root = args.vault or AppSettings().vault_root()
    if args.cli:
        from sound_vault.vault.indexer import build_index

        records = build_index(vault_root)
        print(f"Sound Vault loaded {len(records)} records from {vault_root}")
        return
    from sound_vault.ui.desktop import run_desktop

    raise SystemExit(run_desktop(vault_root))


if __name__ == "__main__":
    main()
