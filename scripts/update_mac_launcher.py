from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
import plistlib
import re
import shutil
import stat
import tarfile
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class PackageResult:
    wheel: Path
    zip_path: Path
    tar_path: Path
    launcher_mode: int


def _project_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise FileNotFoundError(f"missing pyproject.toml at {pyproject}")
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"missing [project].version in {pyproject}")
    return version


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _zip_directory(bundle: Path, *, dist: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle.rglob("*")):
            arc = path.relative_to(dist)
            info = zipfile.ZipInfo(str(arc))
            info.create_system = 3
            mode = path.stat().st_mode
            if path.is_dir():
                info.external_attr = ((mode | stat.S_IFDIR) & 0xFFFF) << 16
                zf.writestr(info, b"")
            else:
                info.external_attr = (mode & 0xFFFF) << 16
                zf.writestr(info, path.read_bytes())


def _launcher_template(*, wheel_name: str, cache_version: str) -> str:
    return f'''#!/bin/zsh
set -u

LAUNCHER="${{0:A}}"
CONTENTS="${{LAUNCHER:h:h}}"
APP_DIR="${{CONTENTS:h}}"
RESOURCES="$CONTENTS/Resources"
WHEEL="$RESOURCES/wheelhouse/{wheel_name}"
VERSION="{cache_version}"
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
LOG_DIR="$HOME/Library/Logs/Sound Vault"
LOG="$LOG_DIR/launcher.log"
mkdir -p "$APP_SUPPORT" "$LOG_DIR"
exec >> "$LOG" 2>&1

echo "---- Sound Vault launch $(date -u '+%Y-%m-%dT%H:%M:%SZ') ----"
echo "launcher: $LAUNCHER"
echo "app dir: $APP_DIR"
echo "contents: $CONTENTS"
echo "resources: $RESOURCES"
echo "wheel: $WHEEL"
echo "cache version: $VERSION"

if [[ ! -f "$WHEEL" ]]; then
  osascript -e 'display alert "Sound Vault package is missing its wheel" message "The launcher could not find the bundled app wheel. Details are in ~/Library/Logs/Sound Vault/launcher.log"'
  echo "ERROR: bundled wheel is missing: $WHEEL"
  exit 66
fi

find_python() {{
  for py in \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"; do
    if [[ -x "$py" ]]; then
      "$py" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      if [[ $? -eq 0 ]]; then
        echo "$py"
        return 0
      fi
    fi
  done
  if command -v python3 >/dev/null 2>&1; then
    local found="$(command -v python3)"
    "$found" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    if [[ $? -eq 0 ]]; then
      echo "$found"
      return 0
    fi
  fi
  return 1
}}

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
  osascript -e 'display alert "Sound Vault needs Python 3.11+" message "Install Python 3 from python.org or Homebrew, then open Sound Vault again. Details are in ~/Library/Logs/Sound Vault/launcher.log"'
  echo "ERROR: no Python 3.11+ found"
  exit 127
fi

echo "python: $PYTHON"
if [[ ! -d "$APP_SUPPORT/venv" || ! -f "$APP_SUPPORT/.launcher-version" || "$(cat "$APP_SUPPORT/.launcher-version" 2>/dev/null)" != "$VERSION" ]]; then
  echo "creating/updating venv"
  rm -rf "$APP_SUPPORT/venv"
  "$PYTHON" -m venv "$APP_SUPPORT/venv"
  "$APP_SUPPORT/venv/bin/python" -m pip install --upgrade pip
  "$APP_SUPPORT/venv/bin/python" -m pip install "${{WHEEL}}[gui]"
  echo "$VERSION" > "$APP_SUPPORT/.launcher-version"
fi

export SOUND_VAULT_DEFAULT_VAULT="${{SOUND_VAULT_DEFAULT_VAULT:-$HOME/Documents/Sound Vault}}"
export QT_MAC_WANTS_LAYER=1
exec "$APP_SUPPORT/venv/bin/sound-vault"
'''


def _command_template() -> str:
    return '''#!/bin/zsh
set -u
SCRIPT_DIR="${0:A:h}"
APP="$SCRIPT_DIR/Sound Vault.app"
LOG="$HOME/Library/Logs/Sound Vault/launcher.log"
echo "Opening Sound Vault.app ..."
open "$APP"
echo "If it closes immediately, inspect: $LOG"
'''


def _readme_template() -> str:
    return """Sound Vault Mac launcher

This is an unsigned Python launcher bundle, not a signed/notarized standalone Mac app.

How to open:
1. unzip this folder locally.
2. right-click `Sound Vault.app` -> Open.
3. if macOS blocks it, use the included `Open Sound Vault.command` so Terminal shows the log hint.

Requirements:
- Python 3.11+ installed from python.org or Homebrew.
- internet on first launch so the local venv can install PySide6.

Logs:
~/Library/Logs/Sound Vault/launcher.log

Cache:
~/Library/Application Support/Sound Vault/venv

If a same-version test build does not pick up changes, delete:
~/Library/Application Support/Sound Vault/.launcher-version
or the whole venv folder above.
"""


def _write_info_plist(path: Path, *, version: str) -> None:
    payload = {
        "CFBundleName": "Sound Vault",
        "CFBundleDisplayName": "Sound Vault",
        "CFBundleIdentifier": "com.lelanddutcher.sound-vault",
        "CFBundleVersion": version,
        "CFBundleShortVersionString": version,
        "CFBundleExecutable": "SoundVault",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(payload))


def _ensure_bundle(bundle: Path, *, version: str, wheel_name: str, cache_version: str) -> None:
    launcher = bundle / "Sound Vault.app/Contents/MacOS/SoundVault"
    command = bundle / "Open Sound Vault.command"
    info_plist = bundle / "Sound Vault.app/Contents/Info.plist"
    resources = bundle / "Sound Vault.app/Contents/Resources"
    resources.mkdir(parents=True, exist_ok=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(_launcher_template(wheel_name=wheel_name, cache_version=cache_version), encoding="utf-8")
    command.write_text(_command_template(), encoding="utf-8")
    rewrite_plist = not info_plist.exists() or info_plist.stat().st_size == 0
    if not rewrite_plist:
        try:
            current = plistlib.loads(info_plist.read_bytes())
            rewrite_plist = (
                current.get("CFBundleExecutable") != "SoundVault"
                or current.get("CFBundlePackageType") != "APPL"
                or current.get("CFBundleShortVersionString") != version
                or current.get("CFBundleVersion") != version
            )
        except Exception:
            rewrite_plist = True
    if rewrite_plist:
        _write_info_plist(info_plist, version=version)
    readme = bundle / "README.txt"
    readme.write_text(_readme_template(), encoding="utf-8")


def _sync_launcher_metadata(launcher: Path, *, wheel_name: str, version: str, cache_version: str) -> None:
    text = launcher.read_text(encoding="utf-8")
    updated = re.sub(
        r'^WHEEL="\$RESOURCES/wheelhouse/sound_vault_desktop-[^"]+-py3-none-any\.whl"$',
        f'WHEEL="$RESOURCES/wheelhouse/{wheel_name}"',
        text,
        flags=re.MULTILINE,
    )
    updated = re.sub(
        r'^VERSION="[^"]+"$',
        f'VERSION="{cache_version}"',
        updated,
        flags=re.MULTILINE,
    )
    if updated != text:
        launcher.write_text(updated, encoding="utf-8")


def build_launcher_package(*, root: Path, version: str, stamp: str) -> PackageResult:
    """Copy the current wheel into a Mac launcher bundle and rebuild archives."""

    root = root.resolve()
    dist = root / "dist"
    wheel = dist / f"sound_vault_desktop-{version}-py3-none-any.whl"
    bundle = dist / f"SoundVault-mac-launcher-{version}-{stamp}"
    cache_version = f"{version}+{stamp}"
    launcher = bundle / "Sound Vault.app/Contents/MacOS/SoundVault"
    command = bundle / "Open Sound Vault.command"
    info_plist = bundle / "Sound Vault.app/Contents/Info.plist"
    wheelhouse = bundle / "Sound Vault.app/Contents/Resources/wheelhouse"

    if not wheel.exists():
        raise FileNotFoundError(str(wheel))

    _ensure_bundle(bundle, version=version, wheel_name=wheel.name, cache_version=cache_version)
    for required in (launcher, info_plist):
        if not required.exists():
            raise FileNotFoundError(str(required))

    wheelhouse.mkdir(parents=True, exist_ok=True)
    for old_wheel in wheelhouse.glob("sound_vault_desktop-*.whl"):
        old_wheel.unlink()
    shutil.copy2(wheel, wheelhouse / wheel.name)
    _sync_launcher_metadata(launcher, wheel_name=wheel.name, version=version, cache_version=cache_version)

    for path in (launcher, command):
        if path.exists():
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    zip_path = dist / f"SoundVault-mac-launcher-{version}-{stamp}.zip"
    tar_path = dist / f"SoundVault-mac-launcher-{version}-{stamp}.tar.gz"
    _zip_directory(bundle, dist=dist, zip_path=zip_path)
    if tar_path.exists():
        tar_path.unlink()
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(bundle, arcname=bundle.name)

    prefix = f"SoundVault-mac-launcher-{version}-{stamp}"
    with zipfile.ZipFile(zip_path) as zf:
        launcher_info = zf.getinfo(f"{prefix}/Sound Vault.app/Contents/MacOS/SoundVault")
        launcher_mode = (launcher_info.external_attr // 65536) & 0o777
        plist = plistlib.loads(zf.read(f"{prefix}/Sound Vault.app/Contents/Info.plist"))
        if not launcher_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise RuntimeError(f"launcher is not executable in zip: {oct(launcher_mode)}")
        if plist.get("CFBundleExecutable") != "SoundVault":
            raise RuntimeError("Info.plist CFBundleExecutable must be SoundVault")
        if plist.get("CFBundlePackageType") != "APPL":
            raise RuntimeError("Info.plist CFBundlePackageType must be APPL")
        expected_wheel = f"{prefix}/Sound Vault.app/Contents/Resources/wheelhouse/{wheel.name}"
        if expected_wheel not in zf.namelist():
            raise RuntimeError(f"wheel missing from zip: {expected_wheel}")

    return PackageResult(wheel=wheel, zip_path=zip_path, tar_path=tar_path, launcher_mode=launcher_mode)


def main() -> None:
    parser = argparse.ArgumentParser(description="rebuild Sound Vault Mac launcher archives")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--version", default=None)
    parser.add_argument("--stamp", default=None, help="artifact stamp/date, e.g. 20260507")
    args = parser.parse_args()

    root = args.root.resolve()
    version = args.version or _project_version(root)
    stamp = args.stamp
    if not stamp:
        candidates = sorted((root / "dist").glob(f"SoundVault-mac-launcher-{version}-*"))
        candidates = [path for path in candidates if path.is_dir()]
        stamp = candidates[-1].name.rsplit("-", 1)[-1] if candidates else "manual"

    result = build_launcher_package(root=root, version=version, stamp=stamp)
    for path in (result.wheel, result.zip_path, result.tar_path):
        print(path)
        print("size", path.stat().st_size, "sha256", _sha256(path))
    print("launcher mode", oct(result.launcher_mode))


if __name__ == "__main__":
    main()
