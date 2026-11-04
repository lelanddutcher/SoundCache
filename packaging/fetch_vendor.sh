#!/usr/bin/env bash
# Download the portable node + ffmpeg + ffprobe (macOS arm64) pinned in vendor.json into
# packaging/vendor/bin/, verifying every archive's sha256. Run before pyinstaller.
# The .spec bundles whatever is in packaging/vendor/bin, so this is the only way binaries
# get into a release, and each one is a version we chose and checked.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIN="$ROOT/packaging/vendor.json"
OUT="$ROOT/packaging/vendor/bin"
CACHE="${SOUND_CACHE_BUILD_CACHE:-$HOME/Library/Caches/sound-cache-build}"
mkdir -p "$OUT" "$CACHE"

field() { python3 -c "import json,sys; print(json.load(open('$PIN'))['$1']['$2'])"; }

fetch() {  # name -> verified archive path on stdout
  local name="$1" url sha file got
  url="$(field "$name" url)"; sha="$(field "$name" sha256)"
  file="$CACHE/$name-$(field "$name" version)-$(basename "$url")"
  if [ ! -f "$file" ]; then
    echo ">> downloading $name $(field "$name" version)" >&2
    curl -fL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  got="$(shasum -a 256 "$file" | cut -d' ' -f1)"
  if [ "$got" != "$sha" ]; then
    echo "sha256 mismatch for $name ($file): expected $sha, got $got" >&2
    rm -f "$file"
    exit 1
  fi
  echo "$file"
}

NODE_TGZ="$(fetch node)"
tar -xzf "$NODE_TGZ" -C "$CACHE" "$(field node member)"
install -m 0755 "$CACHE/$(field node member)" "$OUT/node"

for tool in ffmpeg ffprobe; do
  ZIP="$(fetch "$tool")"
  rm -rf "$CACHE/$tool-unzip" && mkdir -p "$CACHE/$tool-unzip"
  /usr/bin/ditto -x -k "$ZIP" "$CACHE/$tool-unzip"
  install -m 0755 "$CACHE/$tool-unzip/$tool" "$OUT/$tool"
done

"$ROOT/packaging/check_vendor.sh"
