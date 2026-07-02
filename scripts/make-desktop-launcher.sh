#!/bin/bash
# Build "Sound Cache.app" — a tiny wrapper that always launches the LATEST code
# (it execs the editable-install console script, so there's no frozen build to go
# stale), and drop a Finder alias to it on the Desktop. Idempotent: re-run any time.
set -euo pipefail

APP="$HOME/Applications/Sound Cache.app"
VENV_BIN="$HOME/venvs/sound-vault/bin/sound-vault"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Bundle the app icon (full-bleed squircle) so Finder/Dock show the real logo.
ICNS_SRC="$(cd "$(dirname "$0")/.." && pwd)/src/sound_vault/ui/assets/AppIcon.icns"
[ -f "$ICNS_SRC" ] && cp "$ICNS_SRC" "$APP/Contents/Resources/AppIcon.icns"

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
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSBackgroundOnly</key><false/>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key><string>io.soundcache.deeplink</string>
      <key>CFBundleURLSchemes</key>
      <array><string>soundcache</string></array>
    </dict>
  </array>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launch" <<'SH'
#!/bin/bash
# Finder/launchd hand GUI apps a stripped PATH (often just /usr/bin:/bin), so
# node (the TikTok sound capture) and ffmpeg (yt-dlp's audio post-processor)
# wouldn't resolve and ingestion would silently fail. Prepend the usual
# Homebrew/MacPorts/local bins, then run the latest code from the editable
# install (no rebuild needed). Forward any args (e.g. a soundcache:// deep link
# passed on the command line) through to the app.
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$PATH"
# On Apple Silicon, force the NATIVE arm64 slice. The universal2 framework Python has
# an x86_64 slice; if macOS launches this bundle under Rosetta, the arm64-only native
# wheels (mlx, ctranslate2/av) can't dlopen and transcription silently has no backend
# (PySide6 is universal so the app still opens). hw.optional.arm64 reports the HARDWARE
# even when the launcher itself is running translated, so it's the correct gate.
if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] && command -v arch >/dev/null 2>&1; then
  exec arch -arm64 "$HOME/venvs/sound-vault/bin/sound-vault" "$@"
fi
exec "$HOME/venvs/sound-vault/bin/sound-vault" "$@"
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
