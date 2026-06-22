#!/bin/bash
# Build "Sound Cache.app" — a tiny wrapper that always launches the LATEST code
# (it execs the editable-install console script, so there's no frozen build to go
# stale), and drop a Finder alias to it on the Desktop. Idempotent: re-run any time.
set -euo pipefail

APP="$HOME/Applications/Sound Cache.app"
VENV_BIN="$HOME/venvs/sound-vault/bin/sound-vault"

mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Sound Cache</string>
  <key>CFBundleDisplayName</key><string>Sound Cache</string>
  <key>CFBundleIdentifier</key><string>io.soundcache.launcher</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSBackgroundOnly</key><false/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launch" <<'SH'
#!/bin/bash
# always run the latest code from the editable install — no rebuild needed.
exec "$HOME/venvs/sound-vault/bin/sound-vault"
SH
chmod +x "$APP/Contents/MacOS/launch"

# register with Launch Services so Finder recognizes it
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

# Desktop alias (fall back to a symlink if Finder automation is blocked)
rm -f "$HOME/Desktop/Sound Cache" "$HOME/Desktop/Sound Cache.app" "$HOME/Desktop/Sound Cache.app alias" 2>/dev/null || true
if osascript -e "tell application \"Finder\" to make alias file to POSIX file \"$APP\" at desktop" >/dev/null 2>&1; then
  echo "✓ Desktop alias created → $APP"
else
  ln -s "$APP" "$HOME/Desktop/Sound Cache.app"
  echo "✓ Desktop symlink created (Finder automation unavailable) → $APP"
fi

[ -x "$VENV_BIN" ] || echo "note: $VENV_BIN not found — install the app (pip install -e .) so the launcher resolves."
echo "Done. Double-click the Desktop alias to launch the newest Sound Cache build."
