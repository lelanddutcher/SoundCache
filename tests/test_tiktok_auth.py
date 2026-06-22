from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sound_vault.ingest import tiktok_auth


def _write_state(path, cookies):
    path.write_text(json.dumps({"cookies": cookies, "origins": []}), encoding="utf-8")


def _session_cookie(expires):
    return {"name": "sessionid", "value": "abc123def456", "domain": ".tiktok.com", "expires": expires}


def test_status_not_connected_when_missing(tmp_path):
    st = tiktok_auth.connection_status(tmp_path / "nope.json")
    assert st.connected is False
    assert st.days_left is None
    assert "no tiktok login" in st.reason.lower()


def test_status_connected_with_session_cookie(tmp_path):
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    exp = (now + timedelta(days=30)).timestamp()
    p = tmp_path / "state.json"
    _write_state(p, [_session_cookie(exp), {"name": "ttwid", "value": "x", "expires": exp}])
    st = tiktok_auth.connection_status(p, now=now)
    assert st.connected is True
    assert st.days_left == 30
    assert st.expiring_soon is False
    assert "active" in st.headline.lower()


def test_status_expiring_soon(tmp_path):
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    p = tmp_path / "state.json"
    _write_state(p, [_session_cookie((now + timedelta(days=3)).timestamp())])
    st = tiktok_auth.connection_status(p, now=now)
    assert st.connected is True
    assert st.expiring_soon is True


def test_status_expired(tmp_path):
    now = datetime(2026, 6, 22, tzinfo=timezone.utc)
    p = tmp_path / "state.json"
    _write_state(p, [_session_cookie((now - timedelta(days=1)).timestamp())])
    st = tiktok_auth.connection_status(p, now=now)
    assert st.connected is False
    assert "expired" in st.reason.lower()


def test_status_no_session_cookie(tmp_path):
    p = tmp_path / "state.json"
    _write_state(p, [{"name": "ttwid", "value": "x", "expires": 9999999999}])
    st = tiktok_auth.connection_status(p)
    assert st.connected is False
    assert "session" in st.reason.lower()


def test_status_unreadable(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    st = tiktok_auth.connection_status(p)
    assert st.connected is False
    assert "unreadable" in st.reason.lower()


def test_state_path_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom.json"
    monkeypatch.setenv("SOUND_VAULT_TIKTOK_STATE", str(custom))
    assert tiktok_auth.state_path() == custom


def test_is_valid_state_file(tmp_path):
    now = datetime.now(timezone.utc)
    good = tmp_path / "good.json"
    _write_state(good, [_session_cookie((now + timedelta(days=10)).timestamp())])
    bad = tmp_path / "bad.json"
    _write_state(bad, [{"name": "ttwid", "value": "x"}])
    assert tiktok_auth.is_valid_state_file(good) is True
    assert tiktok_auth.is_valid_state_file(bad) is False


def test_disconnect_removes_file(tmp_path):
    p = tmp_path / "state.json"
    _write_state(p, [_session_cookie(9999999999)])
    assert tiktok_auth.disconnect(p) is True
    assert not p.exists()
    assert tiktok_auth.disconnect(p) is False  # already gone


def test_login_command_points_at_script():
    cmd = tiktok_auth.login_command(out_path="/tmp/x.json")
    assert cmd[0] == "node"
    assert cmd[1].endswith("scripts/tiktok_login.cjs")
    assert cmd[2] == "/tmp/x.json"
