# Sound Vault macOS launch failure audit — 2026-05-13

Context: Leland reports the Mac launcher still fails to launch. This repo is being built on Linux as a Python wheel plus unsigned macOS `.app` launcher wrapper, not as a signed/notarized standalone Mac app. Linux tests can prove package shape, but cannot prove Finder/Gatekeeper/PySide launch on the target Mac.

Current repo state during audit:

- Dirty files: `scripts/update_mac_launcher.py`, `tests/test_mac_launcher_packaging.py`.
- Current documented artifacts restored to top-level `dist/`:
  - `dist/SoundVault-mac-launcher-0.3.0-20260513d.tar.gz`
  - `dist/SoundVault-mac-launcher-0.3.0-20260513d.zip`
- Verified archive structure on Linux:
  - tar.gz sha256: `2db49d2fe8e0a4588ecd45bef90def79dba4eb6a92852b66baf83ca55e90e29c`
  - zip sha256: `74412f3462c1ec096208b7b42becb4a23e473120a5d2e68272b4f0bf4251544a`
  - zip directory entries for root, `.app/`, `Contents/`, `MacOS/`, `Resources/`, `wheelhouse/` are real directories.
  - launcher and `Open Sound Vault.command` are executable in zip/tar.
  - plist has `CFBundleExecutable=SoundVault`, `CFBundlePackageType=APPL`, `CFBundleShortVersionString=0.3.0`, `LSMinimumSystemVersion=12.0`.

## ranked likely causes

### 1. Gatekeeper/quarantine blocks the unsigned app before the launcher runs

Likelihood: high.

Evidence:

- The app is unsigned and unnotarized by design.
- If downloaded via browser/Slack, macOS can attach `com.apple.quarantine` to the archive or extracted `.app`.
- Finder launch can fail before `Contents/MacOS/SoundVault` writes logs.

Mac checks:

```zsh
cd /path/to/extracted/SoundVault-mac-launcher-0.3.0-20260513d
xattr -lr "Sound Vault.app" | head -80
spctl --assess --type execute -vv "Sound Vault.app"
codesign -dv --verbose=4 "Sound Vault.app" 2>&1
open "Sound Vault.app"
```

Signals:

- If no `~/Library/Logs/Sound Vault/launcher.log` entry appears after Finder launch, Gatekeeper/LaunchServices may be stopping it before script execution.
- If right-click → Open changes behavior, Gatekeeper/quarantine was involved.

### 2. target Mac has no Python 3.11+ where the launcher can find it

Likelihood: high on a clean Mac.

Evidence:

- Launcher is not standalone. It searches hardcoded paths plus `command -v python3` and rejects versions `<3.11`.
- Stock macOS does not guarantee Python 3.11+.

Launcher paths:

```zsh
/opt/homebrew/bin/python3
/usr/local/bin/python3
/Library/Frameworks/Python.framework/Versions/Current/bin/python3
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3
command -v python3
```

Mac checks:

```zsh
for py in \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
  "$(command -v python3 2>/dev/null)"; do
  [ -n "$py" ] && [ -x "$py" ] && "$py" -c 'import sys; print(sys.executable, sys.version)'
done

tail -200 "$HOME/Library/Logs/Sound Vault/launcher.log"
```

Failure marker:

```text
ERROR: no Python 3.11+ found
```

### 3. first-launch venv install fails: pip/PyPI/network/certs/PySide6 wheel resolution

Likelihood: high.

Evidence:

- The bundle includes only `sound_vault_desktop-0.3.0-py3-none-any.whl`.
- It does not vendor PySide6, PySide6_Addons, PySide6_Essentials, shiboken6, or watchdog.
- Launcher does:

```zsh
"$PYTHON" -m venv "$APP_SUPPORT/venv"
"$APP_SUPPORT/venv/bin/python" -m pip install --upgrade pip
"$APP_SUPPORT/venv/bin/python" -m pip install "${WHEEL}[gui]"
```

- `pyproject.toml` has `watchdog>=4.0` as a required dependency and `PySide6>=6.7` only under the `gui` extra.
- If the Mac is offline, behind SSL inspection, has blocked PyPI, or uses a Python/architecture combo without matching wheels, launch dies before UI.

Mac checks:

```zsh
tail -300 "$HOME/Library/Logs/Sound Vault/launcher.log"

APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
"$APP_SUPPORT/venv/bin/python" -m pip show sound-vault-desktop PySide6 PySide6_Essentials PySide6_Addons shiboken6 watchdog
"$APP_SUPPORT/venv/bin/python" -m pip check
```

Common log markers:

```text
Could not find a version that satisfies the requirement PySide6
No matching distribution found
CERTIFICATE_VERIFY_FAILED
Temporary failure in name resolution
Connection refused
```

### 4. PySide6/Qt native plugin or dylib load failure

Likelihood: high once Python and pip install succeed.

Evidence:

- GUI imports PySide6 at module import time in `src/sound_vault/ui/desktop.py`:

```python
from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication, ...
```

- Any Qt platform plugin/native dylib problem aborts before the window appears.
- Linux proxy import with PySide6 installed hit a native-lib class of failure (`libEGL.so.1` missing). macOS analogs include `cocoa` plugin failures, `@rpath` dylib failures, or quarantine/codesign rejection inside PySide6 wheels.

Mac checks:

```zsh
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
export QT_DEBUG_PLUGINS=1
"$APP_SUPPORT/venv/bin/python" - <<'PY'
from PySide6.QtCore import QLibraryInfo
print("Qt version:", QLibraryInfo.version().toString())
print("Plugins:", QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
from PySide6.QtWidgets import QApplication
app = QApplication([])
print("QApplication OK")
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
print("QtMultimedia OK")
PY
```

Look for:

```text
Could not load the Qt platform plugin "cocoa"
Library not loaded: @rpath/...
image not found
code signature invalid
no suitable image found
```

### 5. default vault path points to `~/Documents/Sound Vault`, which may not exist or be writable

Likelihood: medium.

Evidence:

- Launcher sets:

```zsh
export SOUND_VAULT_DEFAULT_VAULT="${SOUND_VAULT_DEFAULT_VAULT:-$HOME/Documents/Sound Vault}"
```

- On Mac the real NAS path `/nas/TikTok Sound Vault` probably does not exist.
- Startup constructs `SoundVaultWindow`, `LibraryViewModel`, `DuplicateDecisionStore`, and `IndexDatabase` early.
- `DuplicateDecisionStore(vault_root / "reports" / "duplicate-decisions.jsonl")` creates parent directories immediately.
- `IndexDatabase(default_index_path())` creates `~/Library/Application Support/Sound Vault/index.sqlite3` and enables WAL.
- Permissions/TCC/home-folder issues can raise before a visible window.

Mac checks:

```zsh
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
"$APP_SUPPORT/venv/bin/python" - <<'PY'
from pathlib import Path
from sound_vault.settings import default_vault_root, default_index_path
from sound_vault.db.index_db import IndexDatabase
print("vault:", default_vault_root())
print("index:", default_index_path())
(default_vault_root() / "reports").mkdir(parents=True, exist_ok=True)
IndexDatabase(default_index_path())
print("paths/db OK")
PY
```

### 6. stale or broken cached venv with matching `.launcher-version`

Likelihood: medium after repeated failed builds.

Evidence:

- Launcher rebuilds venv only if missing or if `.launcher-version` differs from `VERSION`.
- It does not currently verify that `venv/bin/sound-vault` exists, `pip check` passes, or PySide imports before `exec`.
- Build stamps improved cache invalidation, but if a previous launch wrote `.launcher-version` after a partial/bad install or the venv was externally damaged, it can still fail.

Mac checks:

```zsh
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
cat "$APP_SUPPORT/.launcher-version" 2>/dev/null
test -x "$APP_SUPPORT/venv/bin/sound-vault"; echo $?
"$APP_SUPPORT/venv/bin/python" -m pip show sound-vault-desktop PySide6 watchdog
"$APP_SUPPORT/venv/bin/python" -m pip check
```

Temporary verification reset:

```zsh
rm -rf "$HOME/Library/Application Support/Sound Vault/venv"
rm -f "$HOME/Library/Application Support/Sound Vault/.launcher-version"
./Open\ Sound\ Vault.command
```

### 7. diagnostic alert in 20260513d generated launcher has malformed AppleScript quoting

Likelihood as primary launch blocker: low. Likelihood as reason failure is invisible: medium.

Evidence in current 20260513d expanded launcher:

```zsh
osascript -e "display alert "Sound Vault failed to launch" message "Exit code: $code. Details are in ~/Library/Logs/Sound Vault/launcher.log"" >/dev/null 2>&1 || true
```

This is bad nested quoting. Because it ends with `|| true`, it probably will not cause the original failure, but it can prevent the intended macOS alert from appearing. The `Open Sound Vault.command` helper should still print the last log lines.

Repo action taken during audit:

- Patched `scripts/update_mac_launcher.py` so future generated launchers use safe quoting.
- Added packaging test assertions for the diagnostic path.
- Verified:
  - `ruff check .` clean
  - `pytest tests/test_mac_launcher_packaging.py -q` → `5 passed`

Note: existing `20260513d` artifacts were not rebuilt as part of this audit, so they still contain the malformed alert line.

### 8. archive extraction / package structure issues

Likelihood for `20260513a`: confirmed. Likelihood for `20260513d`: low based on Linux archive inspection, but still needs macOS extraction test.

Evidence:

- `20260513a` zip had `.app` directory paths without trailing slash directory entries. That can make Finder/Archive Utility extract a broken bundle.
- `20260513d` zip has correct directory entries, and tar.gz preserves executable bits.
- Still untested on target Mac with Finder/Archive Utility/ditto.

Mac checks:

```zsh
tar -xzf SoundVault-mac-launcher-0.3.0-20260513d.tar.gz
unzip SoundVault-mac-launcher-0.3.0-20260513d.zip
plutil -lint "Sound Vault.app/Contents/Info.plist"
test -x "Sound Vault.app/Contents/MacOS/SoundVault"; echo $?
test -x "Open Sound Vault.command"; echo $?
zsh -n "Sound Vault.app/Contents/MacOS/SoundVault"
zsh -n "Open Sound Vault.command"
```

### 9. zsh syntax/runtime not validated in Linux CI

Likelihood: medium as a verification blind spot.

Evidence:

- Launcher scripts use zsh-specific `${0:A}` and `${path:h}` expansions.
- Linux build environment here lacks `zsh`, so previous checks did not run `zsh -n`.
- Tests are mostly string assertions.

Mac checks:

```zsh
zsh --version
zsh -n "Sound Vault.app/Contents/MacOS/SoundVault"
zsh -n "Open Sound Vault.command"
"Sound Vault.app/Contents/MacOS/SoundVault"
```

### 10. app imports or runtime code can crash before window construction

Likelihood: medium.

Evidence:

- `sound_vault.app:main` imports GUI only after parsing CLI args, but normal launch calls `from sound_vault.ui.desktop import run_desktop`.
- `desktop.py` imports Qt multimedia, widgets, icons, pixmaps, URL services at top level.
- `SoundVaultWindow.__init__` immediately creates persistent settings, view model, duplicate decision store, index database, timers, menus, player/audio output.
- Tests include many source-level assertions and CLI checks, but no real macOS GUI construction test.

Mac check with safer temp dirs:

```zsh
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
SOUND_VAULT_CONFIG_DIR="$(mktemp -d)" \
SOUND_VAULT_DATA_DIR="$(mktemp -d)" \
SOUND_VAULT_DEFAULT_VAULT="$HOME/SoundVaultTest" \
"$APP_SUPPORT/venv/bin/python" - <<'PY'
from pathlib import Path
from PySide6.QtWidgets import QApplication
from sound_vault.ui.desktop import SoundVaultWindow
Path.home().joinpath("SoundVaultTest/catalog").mkdir(parents=True, exist_ok=True)
app = QApplication([])
w = SoundVaultWindow(vault_root=Path.home() / "SoundVaultTest")
print("Window constructed:", w.windowTitle())
w.close()
PY
```

## lower-likelihood causes

### `ffprobe` missing

`ffprobe` absence should not prevent launch. `vault/indexer.py` catches `OSError`, timeout, and nonzero return codes when probing duration. It may reduce duration metadata or slow indexing, but should not kill startup.

### GUI event-loop premature garbage collection

`run_desktop()` stores `window` local while `app.exec()` runs, so this is not an obvious lifetime bug.

### missing relay dependencies

`fastapi`, `uvicorn`, `pydantic`, `httpx` are relay extras. The GUI path does not import the relay server on normal launch.

## current verification blind spots

Linux checks currently prove:

- package build works on Linux
- source tests pass
- archive contains expected files/modes
- CLI can load NAS vault on Linux

They do not prove:

- macOS can pass Gatekeeper/quarantine
- Finder can launch the unsigned app
- zsh scripts parse/execute on Mac
- target Mac has Python 3.11+
- target Mac can create venv and download PySide6
- PySide6 Qt `cocoa` and multimedia plugins load
- default Mac paths are writable
- the window actually appears
- audio playback works

## minimum target-Mac triage packet needed

Ask for these outputs from the failing Mac. This is the shortest path from guessing to root cause:

```zsh
cd /path/to/extracted/SoundVault-mac-launcher-0.3.0-20260513d
pwd
ls -la
xattr -lr "Sound Vault.app" | head -80
spctl --assess --type execute -vv "Sound Vault.app" || true
zsh -n "Sound Vault.app/Contents/MacOS/SoundVault"
zsh -n "Open Sound Vault.command"
./Open\ Sound\ Vault.command

echo '--- launcher log ---'
tail -300 "$HOME/Library/Logs/Sound Vault/launcher.log" 2>/dev/null || true

echo '--- python candidates ---'
for py in \
  /opt/homebrew/bin/python3 \
  /usr/local/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
  "$(command -v python3 2>/dev/null)"; do
  [ -n "$py" ] && [ -x "$py" ] && "$py" -c 'import sys, platform; print(sys.executable, sys.version, platform.machine(), platform.mac_ver())'
done

echo '--- venv package state ---'
APP_SUPPORT="$HOME/Library/Application Support/Sound Vault"
cat "$APP_SUPPORT/.launcher-version" 2>/dev/null || true
"$APP_SUPPORT/venv/bin/python" -m pip show sound-vault-desktop PySide6 PySide6_Essentials PySide6_Addons shiboken6 watchdog 2>/dev/null || true
"$APP_SUPPORT/venv/bin/python" -m pip check 2>/dev/null || true
```

## release recommendation

Do not label another Linux-produced archive as “fixed” until target-Mac evidence identifies the failing layer. The next packaging improvement should be a diagnostic build that vendors wheels or at least separates and logs each stage:

1. preflight: Gatekeeper/quarantine hints, Python discovery, macOS version/arch
2. venv creation
3. pip install with full log
4. PySide import/plugin smoke
5. app window construction smoke
6. real GUI launch

Longer-term, the honest fix is a real Mac-built app (`pyinstaller`, `briefcase`, or `py2app`) built and smoke-tested on macOS, then signed/notarized if this needs normal-user launch behavior.
