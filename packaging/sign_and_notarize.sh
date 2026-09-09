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
#          --apple-id you@example.com --team-id YOUR_TEAM_ID --password abcd-efgh-ijkl-mnop
#   3. A self-contained .app already built (PyInstaller --windowed, onedir/BUNDLE, arm64).
#
# Usage:  packaging/sign_and_notarize.sh "dist/Sound Cache.app"
set -euo pipefail

APP="${1:?usage: sign_and_notarize.sh <path-to-.app>}"
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
ENTITLEMENTS="${SC_ENTITLEMENTS:-$(dirname "$0")/entitlements.plist}"
NOTARY_PROFILE="${SC_NOTARY_PROFILE:-SC_NOTARY}"
ZIP="${APP%.app}-notarize.zip"

# codesign occasionally throws "internal error in Code Signing subsystem" on large
# binaries (a transient Apple timestamp-server hiccup). Retry a few times.
csign() {
  local n=0
  until codesign "$@"; do
    n=$((n + 1))
    [ "$n" -ge 4 ] && { echo "   codesign failed after $n tries: $*" >&2; return 1; }
    echo "   (codesign retry $n) ${*: -1}" >&2
  done
}

echo ">> Stripping extended attributes"
xattr -cr "$APP"

echo ">> Signing nested dylibs / .so (inside-out)"
find "$APP" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | while IFS= read -r -d '' f; do
      csign --force --timestamp --options runtime --sign "$IDENTITY" "$f"
    done

echo ">> Signing other nested Mach-O executables (node / ffmpeg / yt-dlp / Chromium helpers)"
find "$APP" -type f -perm -u+x -print0 \
  | while IFS= read -r -d '' f; do
      ftype="$(file -b "$f")"
      case "$ftype" in
        *Mach-O*executable*)
          # Standalone executables (node, ffmpeg, helpers) get the entitlements so
          # e.g. node's V8 JIT isn't killed by the hardened runtime (Trace/BPT trap).
          csign --force --timestamp --options runtime \
                   --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$f" ;;
        *Mach-O*)
          # Other exec-bit Mach-O (a dylib/bundle) — sign without entitlements.
          csign --force --timestamp --options runtime --sign "$IDENTITY" "$f" ;;
      esac
    done

echo ">> Signing nested .framework / helper .app bundles (deepest first)"
# Order matters once a real browser is bundled: Chromium.app contains its own helper
# .app bundles and a versioned .framework. A containing bundle must be sealed AFTER
# everything inside it, or sealing the parent invalidates the children's signatures and
# notarization rejects the app. `find` yields parents before children, so sort by path
# depth descending.
while IFS= read -r -d '' b; do
  printf '%d\t%s\0' "$(printf '%s' "$b" | tr -cd '/' | wc -c)" "$b"
done < <(find "$APP" -type d \( -name "*.framework" -o -name "*.app" \) -not -path "$APP" -print0) \
  | sort -z -rn -k1,1 \
  | while IFS=$'\t' read -r -d '' _depth b; do
      case "$b" in
        *.app)
          # A nested .app keeps the entitlements. Bundled Chromium's helper processes
          # (renderer, GPU) run V8, which JITs — under the hardened runtime that is
          # killed without allow-jit + allow-unsigned-executable-memory, and the browser
          # dies the moment a page opens. Signing the bundle without entitlements silently
          # OVERWRITES the entitled signature applied to its inner binary above, so the
          # headed TikTok login window beachballed while headless capture still worked.
          csign --force --timestamp --options runtime \
                --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$b" ;;
        *)
          # Frameworks take no entitlements.
          csign --force --timestamp --options runtime --sign "$IDENTITY" "$b" ;;
      esac
    done

echo ">> Signing the outer .app (with entitlements + hardened runtime + timestamp)"
csign --force --timestamp --options runtime \
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
