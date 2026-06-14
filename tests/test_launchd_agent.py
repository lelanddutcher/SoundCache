import plistlib

from sound_vault.agent import launchd


def test_build_plist_runs_the_watch_poller():
    p = launchd.build_plist(python_executable="/venv/bin/python", interval=240)
    assert p["Label"] == launchd.LABEL
    assert p["RunAtLoad"] is True
    assert p["KeepAlive"] is True
    args = p["ProgramArguments"]
    assert args[0] == "/venv/bin/python"
    assert "sound_vault.ingest.cli" in args
    assert "--watch" in args and "--poll-relay" in args
    assert args[args.index("--interval") + 1] == "240"
    assert "/opt/homebrew/bin" in p["EnvironmentVariables"]["PATH"]


def test_build_plist_vault_override():
    p = launchd.build_plist(python_executable="/p", vault="/Volumes/x/Vault")
    args = p["ProgramArguments"]
    assert args[args.index("--vault") + 1] == "/Volumes/x/Vault"


def test_render_plist_is_valid_plist():
    raw = launchd.render_plist(python_executable="/p", interval=60)
    parsed = plistlib.loads(raw)
    assert parsed["Label"] == launchd.LABEL
    assert parsed["ProgramArguments"][0] == "/p"


class _FakeRun:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, **kwargs):
        self.cmds.append(cmd)

        class R:
            stdout = ""
            stderr = ""
        return R()


def test_install_writes_plist_and_bootstraps(tmp_path, monkeypatch):
    monkeypatch.setattr(launchd.Path, "home", classmethod(lambda cls: tmp_path))
    fake = _FakeRun()
    path = launchd.install(python_executable="/venv/bin/python", interval=90, run=fake)
    assert path.exists()
    parsed = plistlib.loads(path.read_bytes())
    assert "--interval" in parsed["ProgramArguments"]
    # bootout (reload) then bootstrap (load)
    verbs = [c[1] for c in fake.cmds]
    assert verbs == ["bootout", "bootstrap"]
    assert fake.cmds[-1][0] == "launchctl"


def test_uninstall_removes_plist_and_boots_out(tmp_path, monkeypatch):
    monkeypatch.setattr(launchd.Path, "home", classmethod(lambda cls: tmp_path))
    fake = _FakeRun()
    launchd.install(python_executable="/p", run=fake)
    assert launchd.plist_path().exists()
    launchd.uninstall(run=fake)
    assert not launchd.plist_path().exists()
    assert any(c[1] == "bootout" for c in fake.cmds)
