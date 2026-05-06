from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_VAULT = Path("/nas/TikTok Sound Vault")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sound Vault desktop app")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT, help="Path to a Sound Vault folder")
    parser.add_argument("--cli", action="store_true", help="Print index count instead of opening the GUI")
    args = parser.parse_args()
    if args.cli:
        from sound_vault.vault.indexer import build_index

        records = build_index(args.vault)
        print(f"Sound Vault loaded {len(records)} records from {args.vault}")
        return
    from sound_vault.ui.desktop import run_desktop

    raise SystemExit(run_desktop(args.vault))


if __name__ == "__main__":
    main()
