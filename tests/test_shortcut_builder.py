from __future__ import annotations

import plistlib
from urllib.parse import unquote

import pytest

from sound_vault.ingest import shortcut_builder as sb


def test_submit_endpoint_normalizes_trailing_slash():
    assert sb.submit_endpoint("https://api.soundcache.io/") == "https://api.soundcache.io/v1/inbox/submit"
    assert sb.submit_endpoint("  https://api.soundcache.io  ") == "https://api.soundcache.io/v1/inbox/submit"


def test_build_workflow_targets_relay_and_carries_pair_code():
    wf = sb.build_workflow("https://api.soundcache.io", "river-7421")

    # Share-Sheet wiring: shows up in every app that shares a link.
    assert wf["WFWorkflowTypes"] == ["ActionExtension"]
    assert "WFURLContentItem" in wf["WFWorkflowInputContentItemClasses"]
    assert "WFSafariWebPageContentItem" in wf["WFWorkflowInputContentItemClasses"]

    actions = wf["WFWorkflowActions"]
    by_id = {a["WFWorkflowActionIdentifier"]: a for a in actions}

    # An Ask-for-Input step runs first so the user can label/annotate the sound.
    ask = actions[0]
    assert ask["WFWorkflowActionIdentifier"] == "is.workflow.actions.ask"
    ask_uuid = ask["WFWorkflowActionParameters"]["UUID"]

    post = by_id["is.workflow.actions.downloadurl"]
    params = post["WFWorkflowActionParameters"]
    assert params["WFURL"] == "https://api.soundcache.io/v1/inbox/submit"
    assert params["WFHTTPMethod"] == "POST"

    body_items = params["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
    keys = {item["WFKey"]["Value"]["string"]: item for item in body_items}
    assert set(keys) == {"pair_code", "url", "source", "note"}
    # pair code is normalized to upper-case
    assert keys["pair_code"]["WFValue"]["Value"]["string"] == "RIVER-7421"
    assert keys["source"]["WFValue"]["Value"]["string"] == "ios_shortcut"
    # the shared link is bound to the Share-Sheet ExtensionInput
    assert keys["url"]["WFValue"]["Value"]["Type"] == "ExtensionInput"
    # the note carries the Ask action's captured output into the POST body
    note_value = keys["note"]["WFValue"]["Value"]
    assert note_value["Type"] == "ActionOutput"
    assert note_value["OutputUUID"] == ask_uuid

    # branding in the confirmation toast
    notify = by_id["is.workflow.actions.shownotification"]["WFWorkflowActionParameters"]
    assert notify["WFNotificationActionTitle"] == "Sound Cache"


def test_workflow_plist_bytes_roundtrips():
    raw = sb.workflow_plist_bytes("https://api.soundcache.io", "ABC-1")
    parsed = plistlib.loads(raw)
    assert parsed["WFWorkflowTypes"] == ["ActionExtension"]


def test_setup_url_keeps_pairing_in_fragment_not_query():
    url = sb.setup_url("https://api.soundcache.io", "river-7421")
    base, _, fragment = url.partition("#")
    # no query params -> nothing sensitive sent to the web server
    assert "?" not in base
    assert base == "https://soundcache.io/shortcut"
    params = dict(part.split("=", 1) for part in fragment.split("&"))
    assert unquote(params["relay"]) == "https://api.soundcache.io"
    assert unquote(params["code"]) == "RIVER-7421"


def test_setup_url_honors_custom_site_base():
    url = sb.setup_url("https://r.example", "x", site_base="http://localhost:4317/")
    assert url.startswith("http://localhost:4317/shortcut#")


@pytest.mark.skipif(not sb.qrcode_available(), reason="qrcode not installed")
def test_qr_matrix_and_svg():
    matrix = sb.qr_matrix("hello")
    assert matrix and all(len(row) == len(matrix) for row in matrix)  # square
    assert any(any(row) for row in matrix)  # has dark modules

    svg = sb.qr_svg("hello", scale=4, quiet_zone=2)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<path" in svg
    # quiet zone applied to both sides at the chosen scale
    expected_dim = (len(matrix) + 2 * 2) * 4
    assert f'width="{expected_dim}"' in svg
