#!/bin/bash
# Build + sign the distributable "Save to Sound Cache" shortcut (the one-tap,
# import-question variant) and drop it into the website so the /shortcut page's
# "Add the Shortcut ✦" button serves a signed AEA1 file.
#
# macOS-only (needs the `shortcuts` CLI + an iCloud-signed-in Apple ID).
#
# GOTCHA (cost us an afternoon once): `shortcuts sign` detects the input format
# by FILE EXTENSION, not contents. A WorkflowKit plist named *.plist is rejected
# with "isn't in the correct format"; the SAME bytes named *.shortcut sign fine.
# So we always stage the unsigned plist with a .shortcut extension before signing.
# (The "Unrecognized attribute string flag" lines `shortcuts sign` prints are
# harmless ObjC-runtime noise — signing still succeeds; check the exit code.)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELAY="${1:-https://api.soundcache.io}"
WEB_DIR="${2:-$HOME/Developer/soundcache-web/shortcut}"

command -v shortcuts >/dev/null || { echo "error: 'shortcuts' CLI not found (macOS only)"; exit 1; }

STAGE="$(mktemp -d)/SoundCache.unsigned.shortcut"   # .shortcut extension is load-bearing
OUT="$WEB_DIR/SoundCache.signed.shortcut"

# Generate the import-question variant (relay baked in; pair code asked at install).
PYTHONPATH="$REPO_ROOT/src" python3 -c "
import sys
from sound_vault.ingest.shortcut_builder import import_question_plist_bytes
open('$STAGE','wb').write(import_question_plist_bytes('$RELAY'))
"

shortcuts sign --mode anyone --input "$STAGE" --output "$OUT"

# AEA1 magic == a real signed archive.
if [ "$(head -c 4 "$OUT")" = "AEA1" ]; then
  echo "✓ signed shortcut written → $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes, relay=$RELAY)"
else
  echo "error: output is not a signed AEA1 archive"; exit 1
fi
echo "Next: redeploy soundcache-web (vercel --prod) so /shortcut serves the new file."
