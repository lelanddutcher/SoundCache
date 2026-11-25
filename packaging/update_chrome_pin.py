"""Pin the current Chrome for Testing stable build (mac-arm64) in chrome-for-testing.json.

    python packaging/update_chrome_pin.py            # latest stable
    python packaging/update_chrome_pin.py 157.0.8103.38

Downloads the archive once to compute its sha256, then rewrites the pin. Run it before
each release (Chrome ships security fixes every week or two) and commit the change; CI's
security-audit "release inputs" job then bundles and launches exactly that build.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

PIN = Path(__file__).resolve().parent / "chrome-for-testing.json"
INDEX = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
LATEST = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
PLATFORM = "mac-arm64"


def _json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URLs
        return json.load(response)


def _download_url(version: str | None) -> tuple[str, str]:
    if version is None:
        stable = _json(LATEST)["channels"]["Stable"]
        version, downloads = stable["version"], stable["downloads"]["chrome"]
    else:
        match = next((v for v in _json(INDEX)["versions"] if v["version"] == version), None)
        if match is None:
            raise SystemExit(f"Chrome for Testing {version} not found")
        downloads = match["downloads"]["chrome"]
    url = next(d["url"] for d in downloads if d["platform"] == PLATFORM)
    return version, url


def main(argv: list[str]) -> int:
    version, url = _download_url(argv[0] if argv else None)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as response:  # noqa: S310
        for chunk in iter(lambda: response.read(1 << 20), b""):
            digest.update(chunk)
    pin = json.loads(PIN.read_text())
    pin.update(version=version, platform=PLATFORM, url=url, sha256=digest.hexdigest(), pinned=date.today().isoformat())
    PIN.write_text(json.dumps(pin, indent=2) + "\n")
    print(f"pinned Chrome for Testing {version} ({digest.hexdigest()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
