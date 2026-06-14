#!/usr/bin/env python3
"""Generate a reference plist of the "Save to Sound Vault" iOS Shortcut.

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
shortcuts-toolkit).

Usage:
    python scripts/build_ios_shortcut.py --relay https://your-relay.vercel.app --pair-code RIVER-7421 --out web/shortcut
"""
from __future__ import annotations

import argparse
from pathlib import Path
import plistlib


def _text(value: str) -> dict:
    return {"WFSerializationType": "WFTextTokenString", "Value": {"string": value, "attachmentsByRange": {}}}


def _shortcut_input() -> dict:
    # The Share Sheet input (the shared URL/link).
    return {"WFSerializationType": "WFTextTokenAttachment", "Value": {"Type": "ExtensionInput", "Aggrandizements": []}}


def _dict_item(key: str, value: dict, item_type: int = 0) -> dict:
    return {"WFItemType": item_type, "WFKey": _text(key), "WFValue": value}


def build_workflow(relay_url: str, pair_code: str) -> dict:
    submit_url = relay_url.rstrip("/") + "/v1/inbox/submit"

    headers = {
        "WFSerializationType": "WFDictionaryFieldValue",
        "Value": {"WFDictionaryFieldValueItems": [_dict_item("Content-Type", _text("application/json"))]},
    }
    json_body = {
        "WFSerializationType": "WFDictionaryFieldValue",
        "Value": {
            "WFDictionaryFieldValueItems": [
                _dict_item("pair_code", _text(pair_code)),
                _dict_item("url", _shortcut_input()),
                _dict_item("source", _text("ios_shortcut")),
            ]
        },
    }

    actions = [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "WFURL": submit_url,
                "WFHTTPMethod": "POST",
                "WFHTTPHeaders": headers,
                "WFHTTPBodyType": "JSON",
                "WFJSONValues": json_body,
                "ShowHeaders": True,
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.shownotification",
            "WFWorkflowActionParameters": {
                "WFNotificationActionTitle": "Sound Vault",
                "WFNotificationActionBody": "Sent to Sound Vault ✨",
            },
        },
    ]

    return {
        "WFWorkflowClientVersion": "2607.0.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4292093695,
            "WFWorkflowIconGlyphNumber": 61440,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowInputContentItemClasses": [
            "WFURLContentItem",
            "WFStringContentItem",
            "WFSafariWebPageContentItem",
            "WFRichTextContentItem",
        ],
        "WFWorkflowActions": actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Save to Sound Vault iOS Shortcut")
    parser.add_argument("--relay", default="https://your-relay.vercel.app", help="relay base URL")
    parser.add_argument("--pair-code", default="YOUR-PAIR-CODE", help="pairing code from the desktop app")
    parser.add_argument("--out", type=Path, default=Path("web/shortcut"), help="output directory")
    parser.add_argument("--name", default="SoundVault", help="output file basename")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    plist_path = args.out / f"{args.name}.unsigned.plist"
    workflow = build_workflow(args.relay, args.pair_code)
    plist_path.write_bytes(plistlib.dumps(workflow, fmt=plistlib.FMT_XML))
    print(f"wrote reference plist {plist_path}")
    print("Build the importable Shortcut by hand: docs/ios-shortcut-v1-recipe.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
