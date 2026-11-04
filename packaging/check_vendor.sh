#!/usr/bin/env bash
# Refuse to package unless packaging/vendor/bin holds exactly the pinned versions in
# vendor.json (catches a stale node/ffmpeg left over from an older build).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIN="$ROOT/packaging/vendor.json"
BIN="$ROOT/packaging/vendor/bin"
want() { python3 -c "import json; print(json.load(open('$PIN'))['$1']['version'])"; }

fail=0
node_have="$("$BIN/node" --version 2>/dev/null | sed 's/^v//')" || node_have="missing"
if [ "$node_have" != "$(want node)" ]; then
  echo "node: have $node_have, pinned $(want node)" >&2; fail=1
fi
for tool in ffmpeg ffprobe; do
  # "ffmpeg version 9.0.2-https://www.martin-riedl.de Copyright ..." -> 9.0.2
  have="$("$BIN/$tool" -version 2>/dev/null | head -1 | sed -E 's/^[a-z]+ version n?([0-9]+(\.[0-9]+)*).*/\1/')" || have="missing"
  if [ "$have" != "$(want "$tool")" ]; then
    echo "$tool: have $have, pinned $(want "$tool")" >&2; fail=1
  fi
done
if [ "$fail" -ne 0 ]; then
  echo "Run packaging/fetch_vendor.sh to fetch the pinned, patched binaries." >&2
  exit 1
fi
echo ">> vendor binaries match vendor.json: node $(want node), ffmpeg $(want ffmpeg)"
