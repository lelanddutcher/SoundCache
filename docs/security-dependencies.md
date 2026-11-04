# Shipped dependencies: versions and security

Audited 2026-11-04 after the October 2026 wave of Node.js and Chromium CVEs. Everything
Sound Cache ships is listed here with the version it is pinned to and why.

## What ships, and where it is pinned

| Component | Pinned to | Where | Why this version |
|-----------|-----------|-------|------------------|
| Chromium (TikTok capture + login) | Chrome for Testing **157.0.8103.38** | `packaging/chrome-for-testing.json` (sha256) | Playwright 1.64.1's own build (156.0.8078.4) misses the late-October fixes (CVE-2026-118306, -118331, -118344, -118348; fixed in 156.0.8078.73+). We were on Playwright 1.60's Chromium 148. |
| Chromium sandbox | **on** (macOS) | `scripts/launch_chromium.cjs` | Playwright launches `--no-sandbox` by default. Capture loads pages from the open web. |
| Playwright (JS driver) | **1.64.1** | `package.json` (exact) | Latest; supports driving current stable Chrome. |
| Node.js (runs the capture scripts) | **24.22.2** (LTS) | `packaging/vendor.json` (sha256) | October 20–21 security release: 10 CVEs incl. CVE-2026-111497 (Brotli decoder RCE, on CISA KEV). Patched floors: 22.23.4 / 24.22.1 / 26.12.1. |
| FFmpeg + ffprobe (decodes every saved sound) | **9.0.2** | `packaging/vendor.json` (sha256) | The previous source, `ffmpeg-static`, ships FFmpeg 6.1.1 (2023). 9.0.2 is upstream's newest release. |
| yt-dlp | ≥ **2026.10.26** | `pyproject.toml` | Runs on every shared link. |
| PySide6 / Qt | ≥ **6.12** | `pyproject.toml` | Current Qt with its security fixes. |
| certifi (CA store bundled in the app) | ≥ **2026.10.19** | `pyproject.toml` | Current root store. |
| Relay worker tooling (wrangler, vitest plugin) | wrangler **4.157.0**, plugin **1.5.1** | `relay-worker/package.json` | Older versions pulled vulnerable `undici` (DoS, SRI bypass) and `zod` (memory exhaustion). Dev/deploy tooling, not runtime: the Worker itself has no dependencies. |
| Relay self-host (FastAPI) | fastapi ≥ 0.162, uvicorn ≥ 0.54, httpx ≥ 0.28 | `pyproject.toml`, `requirements.txt` | Current releases; `python:3.13-slim` base picks up OS patches on rebuild. |

The website repo (`soundcache-web`) ships only static files. Its `puppeteer` dev
dependency (OG-card rendering) was bumped to clear a vulnerable `zod`.

## How it stays patched

- **`security-audit` workflow** (every PR, every push to main, and weekly on Monday):
  - `pip-audit` on the installed app, on Linux and on macOS (including the Mac-only extras).
  - `npm audit --audit-level=high` for the capture scripts and the relay worker.
  - **Release inputs on macOS:** fetches and sha256-verifies the pinned node/ffmpeg/ffprobe, bundles the pinned Chrome for Testing into a stub app, launches it through Playwright with the sandbox on (fails if any renderer runs `--no-sandbox`), and decodes audio with the bundled ffmpeg.
- **Build guards:**
  - `packaging/check_vendor.sh` refuses to package node/ffmpeg that don't match the pins.
  - `packaging/add_browser.sh` refuses a Chrome archive whose sha256 or version doesn't match, or whose major is outside Playwright's supported range.
- **Release step 0:** run `python packaging/update_chrome_pin.py` before each release. Chrome ships security fixes every week or two.

## Residual risks and notes

- **Chrome support range:** Chrome for Testing 157 is one major ahead of Playwright 1.64.1's Chromium (156). Playwright supports current stable Chrome, and the CI launch check exercises it. The add_browser guard allows up to two majors ahead. When Playwright ships a Chromium 157+ build, bump Playwright.
- **Sandbox fallback:** if a Mac can't start a sandboxed Chromium, the capture scripts log `[launch] sandboxed Chromium failed…` and retry without the sandbox, so capture doesn't break. Watch for that line in the logs during the founder test.
- **Supply chain:** npm packages are installed with `npm ci` from the lockfile (exact versions). No package in our lockfiles matches the 2026 compromised-package lists (Axios 1.14.1/0.30.4, the ChainDrop/keyv wave, the Red Hat packages) at the time of this audit.
- **GPL:** the FFmpeg builds are GPL; the release must carry the license and a pointer to the source (see `packaging/README.md`).
