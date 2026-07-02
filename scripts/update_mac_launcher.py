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
        for path in [bundle, *sorted(bundle.rglob("*"))]:
            arc = path.relative_to(dist)
            mode = path.stat().st_mode
            if path.is_dir():
                # Directory entries in zip archives must end in / for macOS Archive Utility,
                # Finder, and ZipInfo.is_dir() to treat .app bundle paths as directories.
                info = zipfile.ZipInfo(f"{arc}/")
                info.create_system = 3
                info.external_attr = ((mode | stat.S_IFDIR) & 0xFFFF) << 16
                zf.writestr(info, b"")
            else:
                info = zipfile.ZipInfo(str(arc))
                info.create_system = 3
                info.external_attr = (mode & 0xFFFF) << 16
                zf.writestr(info, path.read_bytes())


def _launcher_template(*, wheel_name: str, cache_version: str) -> str:
    return f'''#!/bin/zsh
set -eu

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

phase() {{
  echo ""
  echo "---- phase: $1 ----"
}}

run_logged() {{
  echo "+ $*"
  "$@"
}}

alert_failure() {{
  local code="$?"
  if [[ "$code" -ne 0 ]]; then
    echo "ERROR: Sound Vault launcher failed with exit code $code"
    echo "Log: $LOG"
    osascript -e 'display alert "Sound Vault failed to launch" message "Exit code: '"$code"'. Details are in ~/Library/Logs/Sound Vault/launcher.log"' >/dev/null 2>&1 || true
  fi
}}
trap alert_failure EXIT

echo "==== Sound Vault launcher $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===="
phase "preflight artifact paths"
echo "launcher: $LAUNCHER"
echo "app dir: $APP_DIR"
echo "contents: $CONTENTS"
echo "resources: $RESOURCES"
echo "wheel: $WHEEL"
echo "cache version: $VERSION"
echo "shell: $SHELL"
uname -a || true
sw_vers 2>/dev/null || true

if [[ ! -f "$WHEEL" ]]; then
  osascript -e 'display alert "Sound Vault package is missing its wheel" message "The launcher could not find the bundled app wheel. Details are in ~/Library/Logs/Sound Vault/launcher.log"'
  echo "ERROR: bundled wheel is missing: $WHEEL"
  exit 66
fi
ls -la "$APP_DIR" "$CONTENTS" "$CONTENTS/MacOS" "$RESOURCES" "$RESOURCES/wheelhouse" || true

APPLE_SILICON="$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || echo 0)"
REQUIRE_NATIVE_PYTHON="${{SOUND_VAULT_REQUIRE_NATIVE_PYTHON:-0}}"
ALLOW_TRANSLATED_PYTHON="${{SOUND_VAULT_ALLOW_TRANSLATED_PYTHON:-0}}"

python_candidates() {{
  for py in \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3" \
    "$(command -v python3 2>/dev/null || true)"; do
    if [[ -n "$py" ]]; then
      echo "$py"
    fi
  done
}}

python_ok() {{
  local py="$1"
  local require_native="${{2:-0}}"
  SOUND_VAULT_APPLE_SILICON="$APPLE_SILICON" SOUND_VAULT_CHECK_NATIVE="$require_native" "$py" - <<'PY'
import os
import platform
import sys

if sys.version_info < (3, 11):
    raise SystemExit(1)
if os.environ.get("SOUND_VAULT_CHECK_NATIVE") == "1":
    if os.environ.get("SOUND_VAULT_APPLE_SILICON") == "1" and platform.machine() != "arm64":
        raise SystemExit(2)
raise SystemExit(0)
PY
}}

find_python() {{
  local py
  if [[ "$APPLE_SILICON" == "1" ]]; then
    for py in $(python_candidates); do
      if [[ -x "$py" ]] && python_ok "$py" 1; then
        echo "$py"
        return 0
      fi
    done
    if [[ "$REQUIRE_NATIVE_PYTHON" == "1" && "$ALLOW_TRANSLATED_PYTHON" != "1" ]]; then
      return 1
    fi
  fi
  for py in $(python_candidates); do
    if [[ -x "$py" ]] && python_ok "$py" 0; then
      echo "$py"
      return 0
    fi
  done
  return 1
}}

phase "python discovery"
echo "apple silicon: $APPLE_SILICON"
echo "require native python: $REQUIRE_NATIVE_PYTHON"
echo "allow translated python: $ALLOW_TRANSLATED_PYTHON"
for py in $(python_candidates); do
  if [[ -x "$py" ]]; then
    "$py" - <<'PY' || true
import platform, sys
print(sys.executable, sys.version.replace(chr(10), " "), platform.machine(), platform.mac_ver())
PY
  else
    echo "not executable/missing: $py"
  fi
done

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
  if [[ "$APPLE_SILICON" == "1" && "$REQUIRE_NATIVE_PYTHON" == "1" && "$ALLOW_TRANSLATED_PYTHON" != "1" ]]; then
    osascript -e 'display alert "Sound Vault needs native arm64 Python" message "Install Apple Silicon Python 3.11+ from python.org or Homebrew, or set SOUND_VAULT_ALLOW_TRANSLATED_PYTHON=1 for a translated fallback. Details are in ~/Library/Logs/Sound Vault/launcher.log"' >/dev/null 2>&1 || true
    echo "ERROR: no native arm64 Python 3.11+ found"
    exit 127
  fi
  osascript -e 'display alert "Sound Vault needs Python 3.11+" message "Install Python 3 from python.org or Homebrew, then open Sound Vault again. Details are in ~/Library/Logs/Sound Vault/launcher.log"'
  echo "ERROR: no Python 3.11+ found"
  exit 127
fi

echo "selected python: $PYTHON"
SELECTED_PYTHON_MACHINE="$("$PYTHON" - <<'PY'
import platform
print(platform.machine())
PY
)"
echo "selected python architecture: $SELECTED_PYTHON_MACHINE"
if [[ "$APPLE_SILICON" == "1" && "$SELECTED_PYTHON_MACHINE" != "arm64" ]]; then
  echo "WARNING: selected Python is translated/non-native ($SELECTED_PYTHON_MACHINE). Native arm64 Python is preferred for PySide stability."
  osascript -e 'display notification "Using translated Python; install arm64 Python for best stability." with title "Sound Vault"' >/dev/null 2>&1 || true
fi

phase "venv install/update"
needs_rebuild=0
if [[ ! -d "$APP_SUPPORT/venv" || ! -f "$APP_SUPPORT/.launcher-version" || "$(cat "$APP_SUPPORT/.launcher-version" 2>/dev/null)" != "$VERSION" ]]; then
  needs_rebuild=1
elif [[ ! -x "$APP_SUPPORT/venv/bin/sound-vault" ]]; then
  echo "cache invalid: console script missing"
  needs_rebuild=1
elif ! "$APP_SUPPORT/venv/bin/python" -m pip check >/dev/null 2>&1; then
  echo "cache invalid: pip check failed"
  needs_rebuild=1
fi

if [[ "$needs_rebuild" -eq 1 ]]; then
  echo "creating/updating venv"
  rm -rf "$APP_SUPPORT/venv"
  run_logged "$PYTHON" -m venv "$APP_SUPPORT/venv"
  run_logged "$APP_SUPPORT/venv/bin/python" -m pip --version
  run_logged "$APP_SUPPORT/venv/bin/python" -m pip install --upgrade pip
  run_logged "$APP_SUPPORT/venv/bin/python" -m pip install "${{WHEEL}}[gui]"
else
  echo "using cached venv: $APP_SUPPORT/venv"
fi

phase "dependency smoke"
run_logged "$APP_SUPPORT/venv/bin/python" -m pip --version
run_logged "$APP_SUPPORT/venv/bin/python" -m pip show sound-vault-desktop PySide6 PySide6_Essentials PySide6_Addons shiboken6 watchdog
echo "python -m pip check"
run_logged "$APP_SUPPORT/venv/bin/python" -m pip check
"$APP_SUPPORT/venv/bin/python" - <<'PY'
import importlib.metadata as md
import platform
import sys
print("python:", sys.executable)
print("version:", sys.version.replace(chr(10), " "))
print("platform:", platform.platform(), platform.machine(), platform.mac_ver())
for dist in ("sound-vault-desktop", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "watchdog"):
    try:
        print(dist, md.version(dist))
    except md.PackageNotFoundError:
        print(dist, "MISSING")
from PySide6.QtCore import QLibraryInfo
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
print("qt:", QLibraryInfo.version().toString())
print("qt plugins:", QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
print("PySide import smoke OK")
PY
if [[ ! -x "$APP_SUPPORT/venv/bin/sound-vault" ]]; then
  echo "ERROR: console script missing after install: $APP_SUPPORT/venv/bin/sound-vault"
  rm -f "$APP_SUPPORT/.launcher-version"
  exit 70
fi
echo "$VERSION" > "$APP_SUPPORT/.launcher-version"

phase "app diagnostics"
run_logged "$APP_SUPPORT/venv/bin/sound-vault" --diagnose

export SOUND_VAULT_DEFAULT_VAULT="${{SOUND_VAULT_DEFAULT_VAULT:-$HOME/Documents/Sound Vault}}"
export QT_MAC_WANTS_LAYER=1
export QT_DEBUG_PLUGINS="${{QT_DEBUG_PLUGINS:-1}}"
phase "app exec"
echo "SOUND_VAULT_DEFAULT_VAULT=$SOUND_VAULT_DEFAULT_VAULT"
echo "QT_DEBUG_PLUGINS=$QT_DEBUG_PLUGINS"
trap - EXIT
# On Apple Silicon force the native arm64 slice, else a Rosetta launch can't dlopen
# the arm64-only native wheels (mlx/ctranslate2) and transcription has no backend.
if [[ "$APPLE_SILICON" == "1" ]] && command -v arch >/dev/null 2>&1; then
  exec arch -arm64 "$APP_SUPPORT/venv/bin/sound-vault"
fi
exec "$APP_SUPPORT/venv/bin/sound-vault"
'''


def _command_template() -> str:
    return '''#!/bin/zsh
set -u
SCRIPT_DIR="${0:A:h}"
LAUNCHER="$SCRIPT_DIR/Sound Vault.app/Contents/MacOS/SoundVault"
LOG_DIR="$HOME/Library/Logs/Sound Vault"
LOG="$LOG_DIR/launcher.log"
HARNESS_LOG="$LOG_DIR/launch-harness.log"
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$HARNESS_LOG") 2>&1

section() {
  echo ""
  echo "==== $1 ===="
}

dump_tail() {
  local file="$1"
  local lines="$2"
  if [[ -f "$file" ]]; then
    echo "---- tail -$lines $file ----"
    tail -"$lines" "$file" 2>/dev/null || true
  else
    echo "missing log: $file"
  fi
}

echo "==== Sound Vault launch harness $(date -u '+%Y-%m-%dT%H:%M:%SZ') ===="
echo "script: $0"
echo "script dir: $SCRIPT_DIR"
echo "launcher: $LAUNCHER"
echo "launcher log: $LOG"
echo "harness log: $HARNESS_LOG"

section "macOS / shell environment"
uname -a || true
sw_vers 2>/dev/null || true
echo "shell: $SHELL"
echo "path: $PATH"
echo "home: $HOME"

section "artifact structure"
ls -la "$SCRIPT_DIR" || true
ls -la "$SCRIPT_DIR/Sound Vault.app" "$SCRIPT_DIR/Sound Vault.app/Contents" "$SCRIPT_DIR/Sound Vault.app/Contents/MacOS" "$SCRIPT_DIR/Sound Vault.app/Contents/Resources/wheelhouse" 2>&1 || true
/usr/libexec/PlistBuddy -c 'Print CFBundleExecutable' "$SCRIPT_DIR/Sound Vault.app/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c 'Print CFBundlePackageType' "$SCRIPT_DIR/Sound Vault.app/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$SCRIPT_DIR/Sound Vault.app/Contents/Info.plist" 2>/dev/null || true

echo "launcher executable?"
test -x "$LAUNCHER" && echo yes || echo no

section "quarantine / gatekeeper hints"
xattr -lr "$SCRIPT_DIR/Sound Vault.app" 2>&1 | head -120 || true
spctl --assess --type execute -vv "$SCRIPT_DIR/Sound Vault.app" 2>&1 || true
codesign -dv --verbose=4 "$SCRIPT_DIR/Sound Vault.app" 2>&1 || true

section "python candidates"
for py in \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
  "$(command -v python3 2>/dev/null || true)"; do
  if [[ -n "$py" ]]; then
    if [[ -x "$py" ]]; then
      "$py" - <<'PY' || true
import platform, sys
print(sys.executable, sys.version.replace(chr(10), " "), platform.machine(), platform.mac_ver())
PY
    else
      echo "missing/not executable: $py"
    fi
  fi
done

section "cached venv state before launch"
cat "$APP_SUPPORT/.launcher-version" 2>/dev/null || echo "no launcher-version"
if [[ -x "$APP_SUPPORT/venv/bin/python" ]]; then
  "$APP_SUPPORT/venv/bin/python" -m pip --version 2>&1 || true
  "$APP_SUPPORT/venv/bin/python" -m pip show sound-vault-desktop PySide6 PySide6_Essentials PySide6_Addons shiboken6 watchdog 2>&1 || true
  "$APP_SUPPORT/venv/bin/python" -m pip check 2>&1 || true
else
  echo "no cached venv python yet"
fi

section "previous launcher log"
dump_tail "$LOG" 160

section "direct launcher execution"
"$LAUNCHER"
code="$?"
echo "launcher exited with code $code"
if [[ "$code" -ne 0 ]]; then
  echo "Sound Vault failed with exit code $code. Last launcher log lines:"
  tail -80 "$LOG" 2>/dev/null || true
  dump_tail "$LOG" 160
  echo ""
  echo "Harness log saved at: $HARNESS_LOG"
  read "?Press return to close this window..."
  exit "$code"
fi
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
        "CFBundleName": "Sound Cache",
        "CFBundleDisplayName": "Sound Cache",
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
