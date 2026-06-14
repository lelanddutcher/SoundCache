"""macOS background fetch agent (launchd).

Installs a LaunchAgent that runs ``sound-vault-ingest --watch --poll-relay`` so
shares download into the vault even when the desktop GUI is closed. The agent
reads relay credentials + vault path from the app's saved settings, so it stays
idle until the desktop has been paired.

CLI: ``sound-vault-agent install|uninstall|status``.
"""
from __future__ import annotations

import argparse
import os
import plistlib
from pathlib import Path
import subprocess
import sys

LABEL = "com.soundvault.ingest"
# launchd hands agents a bare PATH; include Homebrew so ffmpeg/node/yt-dlp resolve.
_AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# The TikTok Playwright fallback is configured via these env vars (read by
# ingest/factory.py). launchd gives the agent a clean environment, so they must
# be baked into the plist or the agent silently can't download TikTok audio.
_TIKTOK_ENV_VARS = (
    "SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT",
    "SOUND_VAULT_TIKTOK_STATE",
    "SOUND_VAULT_TIKTOK_CAPTURE_CWD",
)


def capture_tiktok_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Pick up the TikTok-capture env vars from the installing shell, if set."""
    env = environ if environ is not None else os.environ
    return {key: env[key] for key in _TIKTOK_ENV_VARS if env.get(key)}


def plist_path(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def default_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "sound-vault-ingest.log"


def build_plist(
    *,
    python_executable: str,
    interval: int = 180,
    label: str = LABEL,
    log_path: str | None = None,
    vault: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    log = str(log_path or default_log_path())
    args = [
        python_executable, "-m", "sound_vault.ingest.cli",
        "--watch", "--poll-relay", "--interval", str(interval),
    ]
    if vault:
        args += ["--vault", str(vault)]
    return {
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": _AGENT_PATH, **(extra_env or {})},
    }


def render_plist(**kwargs) -> bytes:
    return plistlib.dumps(build_plist(**kwargs))


def install(
    *,
    python_executable: str | None = None,
    interval: int = 180,
    vault: str | None = None,
    extra_env: dict[str, str] | None = None,
    run=subprocess.run,
) -> Path:
    python_executable = python_executable or sys.executable
    if extra_env is None:
        extra_env = capture_tiktok_env()
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    default_log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        render_plist(python_executable=python_executable, interval=interval, vault=vault, extra_env=extra_env)
    )
    domain = f"gui/{os.getuid()}"
    run(["launchctl", "bootout", domain, str(path)], capture_output=True, text=True)
    run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True, check=True)
    return path


def uninstall(*, run=subprocess.run) -> None:
    path = plist_path()
    run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True, text=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def status(*, run=subprocess.run) -> str:
    result = run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    return result.stdout or result.stderr or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the Sound Cache background fetch agent (macOS launchd)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    install_cmd = sub.add_parser("install", help="install + load the agent")
    install_cmd.add_argument("--interval", type=int, default=180, help="seconds between relay polls")
    install_cmd.add_argument("--vault", default=None, help="vault path override (default: app setting)")
    sub.add_parser("uninstall", help="unload + remove the agent")
    sub.add_parser("status", help="show agent status")
    args = parser.parse_args(argv)

    if args.cmd == "install":
        captured = capture_tiktok_env()
        path = install(interval=args.interval, vault=args.vault, extra_env=captured)
        print(f"installed + loaded: {path}")
        print(f"logs: {default_log_path()}")
        if captured:
            print(f"TikTok Playwright fallback baked in: {', '.join(sorted(captured))}")
        else:
            print(
                "WARNING: TikTok Playwright fallback NOT configured — set "
                "SOUND_VAULT_TIKTOK_CAPTURE_SCRIPT / _STATE / _CAPTURE_CWD in this shell "
                "before installing, or TikTok audio won't download in the background."
            )
        print("the agent stays idle until the desktop app is paired with a relay.")
    elif args.cmd == "uninstall":
        uninstall()
        print("agent uninstalled")
    elif args.cmd == "status":
        print(status() or "(agent not loaded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
