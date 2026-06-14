#!/usr/bin/env python3
"""Generate a reference plist of the "Save to Sound Cache" iOS Shortcut.

This emits the exact WorkflowKit structure the Shortcut should have:
  - it appears in the Share Sheet of every app that shares a link (TikTok,
    Instagram, YouTube, X, ...) via WFWorkflowTypes=[ActionExtension] + URL/text
    input classes, and
  - POSTs the shared URL to the relay's /v1/inbox/submit with the pairing code.

NOTE: iOS 15+ only imports shortcuts that were signed through the Shortcuts app
(`shortcuts sign` rejects raw plists), so this file is a reference, not a directly
importable artifact. Build the Shortcut once by hand following
docs/ios-shortcut-v1-recipe.md, then Share -> Export to get a distributable file.
Advanced users can import this plist via community tooling (e.g. routinehub /
shortcuts-toolkit). The desktop app's "Pair iPhone" panel produces the same
artifact plus a scan-to-set-up QR.

Usage:
    python scripts/build_ios_shortcut.py --relay https://your-relay.vercel.app --pair-code RIVER-7421 --out web/shortcut
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sound_vault.ingest.shortcut_builder import qr_svg, setup_url, workflow_plist_bytes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Save to Sound Cache iOS Shortcut")
    parser.add_argument("--relay", default="https://your-relay.vercel.app", help="relay base URL")
    parser.add_argument("--pair-code", default="YOUR-PAIR-CODE", help="pairing code from the desktop app")
    parser.add_argument("--out", type=Path, default=Path("web/shortcut"), help="output directory")
    parser.add_argument("--name", default="SoundCache", help="output file basename")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    plist_path = args.out / f"{args.name}.unsigned.plist"
    plist_path.write_bytes(workflow_plist_bytes(args.relay, args.pair_code))
    print(f"wrote reference plist {plist_path}")

    try:
        qr_path = args.out / f"{args.name}.qr.svg"
        qr_path.write_text(qr_svg(setup_url(args.relay, args.pair_code)), encoding="utf-8")
        print(f"wrote setup QR {qr_path}")
    except Exception as exc:  # qrcode optional in CLI contexts
        print(f"(skipped QR: {exc}; pip install qrcode)")

    print("Build the importable Shortcut by hand: docs/ios-shortcut-v1-recipe.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
