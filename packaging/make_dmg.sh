#!/usr/bin/env bash
# Build a signed + notarized, drag-to-Applications DMG from an already
# notarized Sound Cache.app.
#
# Usage:  packaging/make_dmg.sh "dist/Sound Cache.app" [version]
set -euo pipefail

APP="${1:?usage: make_dmg.sh <path-to-.app> [version]}"
VERSION="${2:-0.3.0}"
IDENTITY="${SC_SIGN_IDENTITY:-Developer ID Application: LELAND ANDREW DUTCHER (PKUE74YS72)}"
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
