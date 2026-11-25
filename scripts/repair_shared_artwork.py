#!/usr/bin/env python3
"""Set aside artwork that one image file shares across many unrelated sounds.

Dry run by default: prints the groups it would reject. Pass --apply to change the vault,
or --restore to undo an earlier --apply. See sound_vault.vault.artwork_repair.
After --apply or --restore, click "Rebuild index" in the app so the library picks it up.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from sound_vault.vault.artwork_repair import find_shared_artwork, reject_artwork, restore_artwork


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("vault", type=Path, help="vault root (the folder that holds sounds/)")
    parser.add_argument("--apply", action="store_true", help="reject the shared artwork")
    parser.add_argument("--restore", action="store_true", help="undo an earlier --apply")
    parser.add_argument("--min-sounds", type=int, default=3)
    parser.add_argument("--min-authors", type=int, default=3)
    args = parser.parse_args()

    if args.restore:
        restored = sum(restore_artwork(f) for f in sorted((args.vault / "sounds").iterdir()) if f.is_dir())
        print(f"restored {restored} sounds")
        return 0

    groups = find_shared_artwork(args.vault, min_sounds=args.min_sounds, min_authors=args.min_authors)
    total = sum(len(g.folders) for g in groups)
    for group in groups:
        print(f"{len(group.folders):5d} sounds, {group.authors:4d} creators share {group.digest[:12]}  e.g. {group.folders[0].name}")
    print(f"{total} sounds in {len(groups)} groups")
    if not args.apply:
        print("dry run: pass --apply to set this artwork aside")
        return 0
    changed = 0
    for group in groups:
        for folder in group.folders:
            changed += reject_artwork(folder, reason="shared-across-unrelated-sounds", digest=group.digest)
    print(f"rejected artwork on {changed} sounds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
