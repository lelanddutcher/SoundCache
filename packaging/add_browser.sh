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
set -euo pipefail

APP="${1:?usage: add_browser.sh <path-to .app>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

REV="$(node -e "
const b=require('$ROOT/node_modules/playwright-core/browsers.json');
process.stdout.write(String(b.browsers.find(x => x.name === 'chromium').revision));
")"
CACHE="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/Library/Caches/ms-playwright}"
SRC="$CACHE/chromium-$REV"

if [ ! -d "$SRC" ]; then
  echo "Chromium $REV is not installed at $CACHE" >&2
  echo "Run: npx playwright install chromium" >&2
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
echo ">> Bundling chromium-$REV into the app"
rm -rf "$APP/Contents/Frameworks/ms-playwright" "$APP/Contents/Resources/ms-playwright"
mkdir -p "$(dirname "$DEST")"
/usr/bin/ditto "$SRC" "$DEST"
echo ">> Bundled $(du -sh "$DEST" | cut -f1)"
