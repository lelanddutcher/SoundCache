"""Build the "Save to Sound Cache" iOS Shortcut + pairing QR, in-process.

This is the GUI-agnostic core behind the in-app *Pair iPhone* panel and the
``scripts/build_ios_shortcut.py`` CLI. It produces:

* the WorkflowKit ``plist`` for a Share-Sheet shortcut that POSTs a shared link
  to the relay's ``/v1/inbox/submit`` with the user's pairing code, and
* a pairing **setup URL** + **QR** the user scans on their phone to land on the
  guided setup page with their relay URL + pair code pre-filled.

iOS 15+ only imports shortcuts signed through the Shortcuts app (``shortcuts
sign`` rejects raw plists), so the exported plist is a *pre-filled reference*:
advanced users import it via community tooling, everyone else taps the QR ->
guided page -> iCloud "Add Shortcut" and pastes the two values. Pairing values
ride in the URL **fragment** (``#``) so they are never sent to the web server.
"""
from __future__ import annotations

import plistlib
from urllib.parse import quote

SHORTCUT_NAME = "Save to Sound Cache"
DEFAULT_SITE_BASE = "https://soundcache.io"


def submit_endpoint(relay_url: str) -> str:
    """Relay endpoint the shortcut POSTs the shared link to."""
    return relay_url.strip().rstrip("/") + "/v1/inbox/submit"


def _text(value: str) -> dict:
    return {"WFSerializationType": "WFTextTokenString", "Value": {"string": value, "attachmentsByRange": {}}}


def _shortcut_input() -> dict:
    # The Share Sheet input (the shared URL/link).
    return {"WFSerializationType": "WFTextTokenAttachment", "Value": {"Type": "ExtensionInput", "Aggrandizements": []}}


def _dict_item(key: str, value: dict, item_type: int = 0) -> dict:
    return {"WFItemType": item_type, "WFKey": _text(key), "WFValue": value}


def build_workflow(relay_url: str, pair_code: str) -> dict:
    """The WorkflowKit dict for the Share-Sheet shortcut, pre-filled."""
    submit_url = submit_endpoint(relay_url)

    headers = {
        "WFSerializationType": "WFDictionaryFieldValue",
        "Value": {"WFDictionaryFieldValueItems": [_dict_item("Content-Type", _text("application/json"))]},
    }
    json_body = {
        "WFSerializationType": "WFDictionaryFieldValue",
        "Value": {
            "WFDictionaryFieldValueItems": [
                _dict_item("pair_code", _text(pair_code.strip().upper())),
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
                "WFNotificationActionTitle": "Sound Cache",
                "WFNotificationActionBody": "Saved to Sound Cache ✨",
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


def workflow_plist_bytes(relay_url: str, pair_code: str) -> bytes:
    """Serialized (unsigned) shortcut plist, ready to write to disk."""
    return plistlib.dumps(build_workflow(relay_url, pair_code), fmt=plistlib.FMT_XML)


def setup_url(relay_url: str, pair_code: str, *, site_base: str = DEFAULT_SITE_BASE) -> str:
    """Guided-setup URL to encode in the QR.

    Pairing values are placed in the URL *fragment* so they stay client-side
    (never transmitted to the web server as query params).
    """
    relay = quote(relay_url.strip().rstrip("/"), safe="")
    code = quote(pair_code.strip().upper(), safe="")
    return f"{site_base.rstrip('/')}/shortcut#relay={relay}&code={code}"


def qrcode_available() -> bool:
    try:
        import qrcode  # noqa: F401
    except Exception:
        return False
    return True


def qr_matrix(data: str) -> list[list[bool]]:
    """QR module matrix (True = dark). Requires the ``qrcode`` package."""
    import qrcode

    qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    return [[bool(cell) for cell in row] for row in qr.get_matrix()]


def qr_svg(data: str, *, scale: int = 8, quiet_zone: int = 4) -> str:
    """Render ``data`` as a self-contained SVG QR (dark modules as one path).

    Pure string output — no Pillow, no Qt — so it is usable from the relay/CLI
    and easy to unit-test.
    """
    matrix = qr_matrix(data)
    size = len(matrix)
    dim = (size + quiet_zone * 2) * scale
    segments: list[str] = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                px = (x + quiet_zone) * scale
                py = (y + quiet_zone) * scale
                segments.append(f"M{px} {py}h{scale}v{scale}h-{scale}z")
    path = "".join(segments)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges">'
        f'<rect width="{dim}" height="{dim}" fill="#ffffff"/>'
        f'<path d="{path}" fill="#0a0518"/>'
        "</svg>"
    )
