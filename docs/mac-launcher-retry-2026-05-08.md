# Mac launcher retry — 2026-05-08

User reported the app failed to open.

## Root cause found

The previous launcher refresh depended on a pre-existing `dist/SoundVault-mac-launcher-*` bundle. After a clean build/cleanup, `dist/` no longer had the bundle files, so the packaging path was too brittle and could ship/point to stale or missing launcher material.

## Fix

Updated `scripts/update_mac_launcher.py` so the packager can create the Mac launcher bundle from scratch:

- creates `Sound Vault.app/Contents/MacOS/SoundVault` when missing;
- creates `Info.plist` with `CFBundleExecutable=SoundVault` and `CFBundlePackageType=APPL`;
- creates `Open Sound Vault.command`;
- writes `README.txt` with open/log/cache instructions;
- preserves executable bits in zip;
- logs launch output to `~/Library/Logs/Sound Vault/launcher.log`;
- shows a macOS alert if Python 3.11+ is missing;
- forces cache invalidation with stamp `0.1.0+20260508b`.

## Verification

```bash
/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_mac_launcher_packaging.py -q
# 5 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
# All checks passed

/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
# 88 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation
# built wheel + sdist

/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py --stamp 20260508b
# launcher mode 0o755
```

Archive verification:

```json
{
  "launcher_mode": "0o755",
  "plist_exec": "SoundVault",
  "plist_type": "APPL",
  "wheel_count": 1,
  "has_python_alert": true,
  "has_log_path": true,
  "has_exec_sound_vault": true,
  "readme_has_log": true
}
```

## New artifact

- `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.1.0-20260508b.zip`
  - size: `38344`
  - sha256: `f2ce44337e680cac34ccb3ad13749981ae9f040a82a193e0bc2859307817ee19`
- `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.1.0-20260508b.tar.gz`
  - size: `32993`
  - sha256: `0faeb34538992afd330c672a7d0dc787b74e11f28dfeef78e9892f25b403f6e3`

## Caveat

Still unsigned/not notarized and built from Linux. Real macOS click-through is still user-machine verification.
