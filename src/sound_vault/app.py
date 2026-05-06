from __future__ import annotations

import argparse
from pathlib import Path

from sound_vault.vault.indexer import build_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Sound Vault desktop app placeholder")
    parser.add_argument("--vault", type=Path, default=Path.cwd(), help="Path to a Sound Vault folder")
    args = parser.parse_args()
    records = build_index(args.vault)
    print(f"Sound Vault loaded {len(records)} records from {args.vault}")


if __name__ == "__main__":
    main()
