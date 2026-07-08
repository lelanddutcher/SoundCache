# Signing & notarizing Sound Cache for macOS

Gatekeeper accepts a downloaded app when it is (1) signed with a **Developer ID
Application** certificate, (2) built with a **secure timestamp**, and (3) has the
**Hardened Runtime** enabled, then notarized and stapled. This works for **any**
Mach-O bundle, not just Swift/Xcode apps: a PySide6/Python app notarizes the same
way (BeeWare, Electron, and PyInstaller apps all do). The only wrinkle for Python
is the large number of nested `.dylib`/`.so` files, which each must be signed.

## Credentials

Already in place (verified via `security find-identity -v -p codesigning`):

- **Developer ID Application: LELAND ANDREW DUTCHER (PKUE74YS72)** — the signing cert.
- **Apple Developer Program membership** — implied (you cannot hold a Developer ID
  cert without it).
- **Team ID = `PKUE74YS72`** (the 10-char string in the cert name is the Team ID).

The one thing still needed: a way for `notarytool` to authenticate. It does **not**
use the signing cert for this. Create ONE of:

| Option | Create it | Notes |
| --- | --- | --- |
| **App Store Connect API key** (recommended) | appstoreconnect.apple.com -> Users and Access -> Integrations -> App Store Connect API -> Team Keys -> generate a key with **Developer** access -> download the `.p8` once, copy Key ID + Issuer ID | never expires, no 2FA friction, CI-friendly |
| **App-specific password** | appleid.apple.com -> Sign-In and Security -> App-Specific Passwords | simplest for one machine; use with your Apple ID + Team ID |

Store it once into a keychain profile so secrets never touch scripts:

```bash
# API key:
xcrun notarytool store-credentials "SC_NOTARY" \
  --key /path/AuthKey_XXXXXXXXXX.p8 --key-id XXXXXXXXXX \
  --issuer aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
# ...or app-specific password:
xcrun notarytool store-credentials "SC_NOTARY" \
  --apple-id you@example.com --team-id PKUE74YS72 --password abcd-efgh-ijkl-mnop
```

You do **not** need a Developer ID *Installer* cert unless you ship a `.pkg`. For a
`.zip` or `.dmg`, the Developer ID *Application* cert is enough.

## Packaging path

Today the app is an editable `pip install` into a venv plus a hand-made launcher
`.app` that shells out to that venv — there is nothing self-contained to sign. To
notarize you first need a self-contained `.app`:

**Recommended: PyInstaller `--windowed`, onedir/BUNDLE (not `--onefile`), built with
an arm64 Python.** Its Qt/PySide6 hooks are actively maintained for signable
bundles; onefile unpacks to a temp dir and breaks signing/library-validation.
Verify the result is arm64 before signing: `file "dist/Sound Cache.app/Contents/MacOS/"*`.

The heavyweight extras (node, Playwright's Chromium, ffmpeg, yt-dlp) are large
third-party Mach-O trees. Either bundle them into `Contents/Resources/` and let the
signing script below sign every Mach-O in them, or keep them out of the bundle and
download them on first run into Application Support (unsigned code *outside* the
bundle is not notary-checked).

## Run it

```bash
# 1. build the self-contained .app (see above), then:
packaging/sign_and_notarize.sh "dist/Sound Cache.app"
```

[`sign_and_notarize.sh`](sign_and_notarize.sh) signs every nested Mach-O inside-out,
signs the app with [`entitlements.plist`](entitlements.plist) + hardened runtime +
timestamp, notarizes via `notarytool --wait`, and staples. On an `Invalid` result it
dumps `notary-log.json` with the exact offending file paths.

## Entitlements

See [`entitlements.plist`](entitlements.plist). A bundled-Python + Qt app needs
`com.apple.security.cs.disable-library-validation` (load third-party native wheels)
and `com.apple.security.cs.allow-unsigned-executable-memory` (CPython/Qt/mlx W+X).
Each entitlement you can drop is hardening you keep, so add `allow-jit` /
`allow-dyld-environment-variables` only if something actually faults.

## Common notarization failures

- **Unsigned nested `.so`/`.dylib`** (the #1 cause) — the inside-out `find` loop in
  the script handles this; PyInstaller's own signing routinely misses files and
  never signs node/Chromium/ffmpeg.
- **Missing secure timestamp** — `--timestamp` on every `codesign` call (needs network).
- **Hardened runtime not enabled** — `--options runtime` on every executable.
- **Wrong cert** — must be *Developer ID Application*, not *Apple Development* / *Apple Distribution*.
- **`--deep` signing** — fine to *verify* with `--deep --strict`, but sign inside-out
  manually; `--deep` leaves helpers mis-signed.
- **`get-task-allow`** (debug entitlement) present — must not be in the shipped signature.

## For this release

## The actual release pipeline (what ships)

The `.app` here is fully self-contained — it bundles Python, the GPU ASR engines
(MLX + faster-whisper), the Playwright JS driver, and portable **node + ffmpeg +
ffprobe** (`packaging/vendor/bin`, downloaded by nothing in git — see below), so an
end user needs **no Homebrew**. Chromium (~1.5 GB) is NOT bundled; the app downloads
it on first "Connect TikTok" via Playwright.

```bash
# 1. one-time: fetch the portable binaries into packaging/vendor/bin/
#    node (nodejs.org, arm64) + ffmpeg/ffprobe (eugeneware/ffmpeg-static, arm64)
# 2. build:
~/venvs/sound-vault/bin/pyinstaller packaging/SoundCache.spec \
    --distpath dist --workpath build/pyi --noconfirm
chmod +x "dist/Sound Cache.app/Contents/Frameworks/bin/"*   # datas drops the +x bit
# 3. sign + notarize the app, then wrap it in a notarized DMG:
packaging/sign_and_notarize.sh "dist/Sound Cache.app"
packaging/make_dmg.sh "dist/Sound Cache.app" 0.3.1
# 4. gh release create v0.3.1 dist/SoundCache-0.3.1-arm64.dmg ...
```

## Bundled third-party binaries & licenses

- **node** (`nodejs.org`, arm64) — MIT/ISC-style; ships its own OpenSSL/ICU.
- **ffmpeg + ffprobe** (`github.com/eugeneware/ffmpeg-static`, arm64) — these are
  **GPL** static builds. Distributing them means the release must honor the GPL:
  keep the license/notice and be able to point to corresponding source. If that's a
  concern, swap in an LGPL/BSD ffmpeg build.
- **Chromium** — downloaded at runtime by Playwright (BSD-3); not redistributed by us.

`packaging/vendor/` is gitignored (these are downloaded binaries, not source).
