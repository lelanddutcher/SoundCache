#!/usr/bin/env bash
# Sign + notarize + staple a self-contained Sound Cache .app for Gatekeeper.
#
# Prereqs (one-time):
#   1. A "Developer ID Application" cert in your login keychain
#      (check: security find-identity -v -p codesigning).
#   2. A notarytool credential profile. Create ONE of:
#        # App Store Connect API key (recommended):
#        xcrun notarytool store-credentials "SC_NOTARY" \
#          --key /path/AuthKey_XXXXXXXXXX.p8 --key-id XXXXXXXXXX \
#          --issuer aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
#        # ...or an app-specific password (appleid.apple.com):
#        xcrun notarytool store-credentials "SC_NOTARY" \
#          --apple-id you@example.com --team-id PKUE74YS72 --password abcd-efgh-ijkl-mnop
#   3. A self-contained .app already built (PyInstaller --windowed, onedir/BUNDLE, arm64).
#
# Usage:  packaging/sign_and_notarize.sh "dist/Sound Cache.app"
set -euo pipefail

APP="${1:?usage: sign_and_notarize.sh <path-to-.app>}"
IDENTITY="${SC_SIGN_IDENTITY:-Developer ID Application: LELAND ANDREW DUTCHER (PKUE74YS72)}"
ENTITLEMENTS="${SC_ENTITLEMENTS:-$(dirname "$0")/entitlements.plist}"
NOTARY_PROFILE="${SC_NOTARY_PROFILE:-SC_NOTARY}"
ZIP="${APP%.app}-notarize.zip"

echo ">> Stripping extended attributes"
xattr -cr "$APP"

echo ">> Signing nested dylibs / .so (inside-out)"
find "$APP" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | while IFS= read -r -d '' f; do
      codesign --force --timestamp --options runtime --sign "$IDENTITY" "$f"
    done

echo ">> Signing other nested Mach-O executables (node / ffmpeg / yt-dlp / Chromium helpers)"
find "$APP" -type f -perm -u+x -print0 \
  | while IFS= read -r -d '' f; do
      if file -b "$f" | grep -q 'Mach-O'; then
        codesign --force --timestamp --options runtime --sign "$IDENTITY" "$f"
      fi
    done

echo ">> Signing nested .framework / helper .app bundles"
find "$APP" -type d \( -name "*.framework" -o -name "*.app" \) -not -path "$APP" -print0 \
  | while IFS= read -r -d '' b; do
      codesign --force --timestamp --options runtime --sign "$IDENTITY" "$b"
    done

echo ">> Signing the outer .app (with entitlements + hardened runtime + timestamp)"
codesign --force --timestamp --options runtime \
         --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$APP"

echo ">> Verifying signature locally"
codesign --verify --deep --strict --verbose=2 "$APP"

echo ">> Zipping for notary submission"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo ">> Submitting to the notary service (this waits for the result)"
OUT="$(xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait 2>&1)"
echo "$OUT"
SUBID="$(echo "$OUT" | awk '/id:/{print $2; exit}')"

if echo "$OUT" | grep -qi "status: Invalid"; then
  echo ">> Notarization INVALID — fetching the log (shows the exact offending files):"
  xcrun notarytool log "$SUBID" --keychain-profile "$NOTARY_PROFILE" notary-log.json
  cat notary-log.json
  exit 1
fi

echo ">> Stapling the ticket"
xcrun stapler staple "$APP"

echo ">> Final Gatekeeper check"
xcrun stapler validate "$APP"
spctl -a -vvv -t exec "$APP"   # expect: accepted, source=Notarized Developer ID
echo ">> DONE."
