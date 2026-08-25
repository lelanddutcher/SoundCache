#!/usr/bin/env bash
# Build a signed + notarized, drag-to-Applications DMG from an already
# notarized Sound Cache.app.
#
# Usage:  packaging/make_dmg.sh "dist/Sound Cache.app" [version]
set -euo pipefail

APP="${1:?usage: make_dmg.sh <path-to-.app> [version]}"
VERSION="${2:-0.3.0}"
# Resolve the signing identity at run time instead of naming it here. The identity
# string embeds the Apple Team ID, and this repo is public — keep the developer's
# identifiers out of it. Override with SC_SIGN_IDENTITY to pick a specific cert.
IDENTITY="${SC_SIGN_IDENTITY:-$(security find-identity -v -p codesigning \
  | awk -F'"' '/Developer ID Application/{print $2; exit}')}"
if [ -z "$IDENTITY" ]; then
  echo "No 'Developer ID Application' identity in the keychain." >&2
  echo "Check: security find-identity -v -p codesigning" >&2
  echo "Or set SC_SIGN_IDENTITY to the exact certificate name." >&2
  exit 1
fi
NOTARY_PROFILE="${SC_NOTARY_PROFILE:-SC_NOTARY}"
VOLNAME="Sound Cache"
OUT="dist/SoundCache-${VERSION}-arm64.dmg"

STAGE="$(mktemp -d)/Sound Cache"
mkdir -p "$STAGE"
echo ">> Staging app + /Applications shortcut"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo ">> Building compressed DMG"
rm -f "$OUT"
hdiutil create -volname "$VOLNAME" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$OUT" >/dev/null
rm -rf "$(dirname "$STAGE")"

echo ">> Signing the DMG"
codesign --force --timestamp --sign "$IDENTITY" "$OUT"

echo ">> Notarizing the DMG (waits for the result)"
xcrun notarytool submit "$OUT" --keychain-profile "$NOTARY_PROFILE" --wait

echo ">> Stapling"
xcrun stapler staple "$OUT"
xcrun stapler validate "$OUT"

echo ">> DONE: $OUT ($(du -h "$OUT" | awk '{print $1}'))"
