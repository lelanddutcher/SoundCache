#!/usr/bin/env bash
# Copy the Chromium build Playwright expects into an already-built Sound Cache.app.
#
# Run AFTER pyinstaller and BEFORE sign_and_notarize.sh.
#
# Why not in the .spec: PyInstaller passes every Mach-O in `datas` through
# process_collected_binary(), which ad-hoc re-signs it. That fails on Chromium's nested
# "Google Chrome for Testing.app" and aborts the build.
#
# Why bundle at all: Playwright otherwise reads the shared ~/Library/Caches/ms-playwright,
# which is not ours. A fresh Mac has no browser, and any other tool installing a newer
# Playwright prunes the exact revision we need. Both leave TikTok capture dead with
# "Executable doesn't exist". At runtime factory.ensure_browsers_path() points
# PLAYWRIGHT_BROWSERS_PATH at the copy inside the app.
#
# Only the FULL chromium build ships: the capture scripts pass channel:"chromium" so one
# browser serves headed login and headless capture, instead of also carrying the ~190MB
# chromium_headless_shell.
#
# WHICH build: not Playwright's. Playwright pins a Chromium revision that lags Chrome's
# security releases (1.64.1 pins 156.0.8078.4, missing the late-October 2026 fixes). On
# macOS that revision IS a "Google Chrome for Testing" build, so we put the current Chrome
# for Testing STABLE build (pinned + sha256-verified in chrome-for-testing.json) at the
# path Playwright launches: ms-playwright/chromium-<rev>/chrome-mac-arm64/...app.
# Playwright supports driving current stable Chrome; we refuse a build whose major is
# older than Playwright's or more than two majors newer.
set -euo pipefail

APP="${1:?usage: add_browser.sh <path-to .app>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIN="$ROOT/packaging/chrome-for-testing.json"

read -r REV PW_MAJOR < <(node -e "
const b=require('$ROOT/node_modules/playwright-core/browsers.json');
const c=b.browsers.find(x => x.name === 'chromium');
// Trailing newline: without it \`read\` hits EOF, returns 1, and set -e aborts silently.
process.stdout.write(c.revision + ' ' + String(c.browserVersion).split('.')[0] + '\\n');
")
read -r VERSION URL SHA < <(python3 -c "
import json,sys; p=json.load(open('$PIN')); print(p['version'], p['url'], p['sha256'])")
MAJOR="${VERSION%%.*}"
if [ "$MAJOR" -lt "$PW_MAJOR" ] || [ "$MAJOR" -gt $((PW_MAJOR + 2)) ]; then
  echo "Chrome for Testing $VERSION is not compatible with Playwright's Chromium $PW_MAJOR." >&2
  echo "Update packaging/chrome-for-testing.json or the playwright version." >&2
  exit 1
fi

CACHE="${SOUND_CACHE_BUILD_CACHE:-$HOME/Library/Caches/sound-cache-build}"
ZIP="$CACHE/chrome-for-testing-$VERSION-mac-arm64.zip"
mkdir -p "$CACHE"
if [ ! -f "$ZIP" ]; then
  echo ">> Downloading Chrome for Testing $VERSION"
  curl -fL --retry 3 -o "$ZIP.part" "$URL"
  mv "$ZIP.part" "$ZIP"
fi
GOT="$(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
if [ "$GOT" != "$SHA" ]; then
  echo "sha256 mismatch for $ZIP" >&2
  echo "  expected $SHA" >&2
  echo "  got      $GOT" >&2
  rm -f "$ZIP"
  exit 1
fi

# Contents/Resources, NOT Contents/Frameworks. Frameworks may hold only frameworks and
# dylibs; Chromium ships loose data files (ABOUT, INSTALLATION_COMPLETE) and codesign
# then refuses to seal the outer app ("In subcomponent: .../ABOUT"). Resources is the
# correct home for payload. sys._MEIPASS points at Frameworks, so
# factory.bundled_browsers_dir() also checks the sibling Resources — without that the
# app silently falls back to the shared cache, which is how 0.4.2 and 0.4.3 shipped the
# browser as dead weight.
DEST="$APP/Contents/Resources/ms-playwright/chromium-$REV"
echo ">> Bundling Chrome for Testing $VERSION as chromium-$REV"
rm -rf "$APP/Contents/Frameworks/ms-playwright" "$APP/Contents/Resources/ms-playwright"
mkdir -p "$DEST"
/usr/bin/ditto -x -k "$ZIP" "$DEST"
touch "$DEST/INSTALLATION_COMPLETE"
EXE="$DEST/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
if [ ! -x "$EXE" ]; then
  echo "Unexpected archive layout: $EXE is missing" >&2
  exit 1
fi
SHIPPED="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$DEST/chrome-mac-arm64/Google Chrome for Testing.app/Contents/Info.plist")"
if [ "$SHIPPED" != "$VERSION" ]; then
  echo "Bundled Chrome reports $SHIPPED, expected $VERSION" >&2
  exit 1
fi
echo ">> Bundled Chrome for Testing $SHIPPED ($(du -sh "$DEST" | cut -f1))"
