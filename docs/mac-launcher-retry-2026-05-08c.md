# Mac launcher retry — 2026-05-08c

User correctly called out that repeated `0.1.0` handoff builds were not trustworthy. The app also still failed to open, but Finder visibly tried.

## Root cause found

The launcher path math was wrong for a real macOS `.app` launch.

Previous launcher logic:

```zsh
APP_DIR="${0:A:h:h}"
CONTENTS="$APP_DIR/Contents"
```

For `Sound Vault.app/Contents/MacOS/SoundVault`, `${0:A:h:h}` resolves to `Sound Vault.app/Contents`, not `Sound Vault.app`. That made resources resolve under:

```text
Sound Vault.app/Contents/Contents/Resources/...
```

So the bundled wheel could not be found. This matches the symptom: Finder tries to open the app, then it dies.

## Fixes

- Bumped package/app version from `0.1.0` to `0.2.0`.
- Fixed launcher path math:

```zsh
LAUNCHER="${0:A}"
CONTENTS="${LAUNCHER:h:h}"
APP_DIR="${CONTENTS:h}"
RESOURCES="$CONTENTS/Resources"
```

- Added explicit missing-wheel alert/logging if the bundle path is wrong again.
- Rewrites launcher script every package build instead of preserving stale shell content.
- Writes `Info.plist` version fields from the actual project version.
- Added regression test rejecting the old `Contents/Contents` path bug.

## Verification

```bash
/opt/data/venvs/sound-vault-desktop/bin/python -m pytest tests/test_mac_launcher_packaging.py -q
# 5 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .
# All checks passed

/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q
# 88 passed

/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation
# built sound_vault_desktop-0.2.0.tar.gz and sound_vault_desktop-0.2.0-py3-none-any.whl

/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py --stamp 20260508c
# launcher mode 0o755
```

Archive verification:

```json
{
  "launcher_mode": "0o755",
  "plist_exec": "SoundVault",
  "plist_type": "APPL",
  "plist_short_version": "0.2.0",
  "plist_version": "0.2.0",
  "has_fixed_path_math": true,
  "has_bad_old_path_math": false,
  "cache_version_line": "VERSION=\"0.2.0+20260508c\"",
  "wheel_line": "WHEEL=\"$RESOURCES/wheelhouse/sound_vault_desktop-0.2.0-py3-none-any.whl\""
}
```

## New artifact

- `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.2.0-20260508c.zip`
  - size: `38728`
  - sha256: `7ff7a21629a6ec0a5f6714b5ad706fa8cc4ccd9d0c3a7ad8db07320520072b36`
- `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/SoundVault-mac-launcher-0.2.0-20260508c.tar.gz`
  - size: `33117`
  - sha256: `bfb46a585e8f4fb441f093cc0ac7ea740f2e62ac168925a88ade3e5c84716404`
- wheel: `/nas/TikTok Sound Vault/product/sound-vault-desktop/dist/sound_vault_desktop-0.2.0-py3-none-any.whl`
  - size: `32123`
  - sha256: `fdd833603fd0064d8c51eb3a529bea87ea99131d285908481bd19ecc02390f7e`

## Caveat

Still Linux-built, unsigned, and not notarized. Real macOS click-through remains target-machine verification.
