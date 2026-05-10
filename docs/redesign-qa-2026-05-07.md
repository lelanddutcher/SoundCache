# Redesign QA — 2026-05-07

## 2026-05-07T08:34:01Z watchdog pass

- Context read: `docs/redesign-brief-2026-05-07.md`, `docs/redesign-progress-2026-05-07.md`.
- Lint: `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .` → `All checks passed!`.
- Tests: `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q` → `50 passed in 0.55s`.
- Real-vault CLI smoke: `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault '/nas/TikTok Sound Vault'` → `Sound Vault loaded 1543 records from /nas/TikTok Sound Vault`.
- Non-isolated build: `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation` → rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`.
- Mac launcher artifacts: verified one `.app` bundle at `dist/SoundVault-mac-launcher-0.1.0-20260507/Sound Vault.app`; `Info.plist` has `CFBundleExecutable=SoundVault`; `Contents/MacOS/SoundVault` mode `0o755`; `Open Sound Vault.command` mode `0o755`; wheelhouse contains one wheel matching the rebuilt wheel checksum; zip/tar each include the wheel and preserve executable bits.
- Checksums:
  - `dist/sound_vault_desktop-0.1.0-py3-none-any.whl` size `25976`, sha256 `a00601728aa630dfebadf88414a4bbf208372623588054513b5a05afd1ee99c6`.
  - `dist/sound_vault_desktop-0.1.0.tar.gz` size `41928`, sha256 `73d1b1155233f882a90f0bb6dcfc9b35cc5e0677ff5c75fae894112c15587d91`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507.zip` size `32849`, sha256 `8e664792d3ab954c4ec9159e9d6254705f31b46f5b69786dcf75ee3ecd4d9fd3`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz` size `27136`, sha256 `8bf4d2062b578f4465044e4038af848f282fa721c81274676a4ce68c7e8a478b`.
- Git/diff inspection: repo still has broad pre-existing modified/untracked redesign + relay hardening files; no staged changes; no tracked `dist/`, pycache, or obvious generated junk in git status. Secret scan of tracked diff reported no hits. Whole working-tree scan excluding `.git`, caches, and `dist` reported one benign dummy-secret fixture in `tests/test_finish_blockers.py` verifying redaction behavior, not a live credential.
- Blockers/caveats: Linux host still cannot provide a real macOS GUI/multimedia click-through or signed/notarized app verification; final launcher needs macOS open/playback smoke.

## 2026-05-07T11:40:54Z watchdog pass

- Context read: `docs/redesign-brief-2026-05-07.md`, `docs/redesign-progress-2026-05-07.md`.
- Lint: `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .` → `All checks passed!`.
- Tests: `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q` → `52 passed in 0.58s`.
- Real-vault CLI smoke: `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault '/nas/TikTok Sound Vault'` → `Sound Vault loaded 1809 records from /nas/TikTok Sound Vault`.
- Non-isolated build: `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation` → rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`.
- Mac launcher refresh/verification: `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py` refreshed existing Linux-built unsigned launcher artifacts. Verified `.app` exists at `dist/SoundVault-mac-launcher-0.1.0-20260507/Sound Vault.app`; `CFBundleExecutable=SoundVault`; `Contents/MacOS/SoundVault` mode `0o755`; `Open Sound Vault.command` mode `0o755`; bundled wheel checksum matches rebuilt wheel; zip/tar each include exactly one wheel and preserve executable bits (`0o755`).
- Checksums:
  - `dist/sound_vault_desktop-0.1.0-py3-none-any.whl` size `26483`, sha256 `9b5e84cb5303a88b2f9744b9f97fe87c70c7dd774969a8e71b096084a95cc3e5`.
  - `dist/sound_vault_desktop-0.1.0.tar.gz` size `44312`, sha256 `8e9543b828184878b800f8f0f4d7fbd946c751b9dc7580b614d4279910e55416`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507.zip` size `33356`, sha256 `70da507da292abb43a586c8c3f6c338e9e6d148af93a8e264be53ff5448b1803`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz` size `27649`, sha256 `6c39ae668243d2709e82462a674ac480f3fb880ac7c675e3127121efad682c88`.
- Git/diff inspection: working tree still has broad pre-existing modified/untracked redesign + relay hardening files. No tracked `dist/`, pycache, ruff cache, or pytest cache entries appeared in status. Secret regex scan found no live credential values; matches were variable/documentation references such as `device_secret` plus one redaction-test dummy fixture, not a real credential.
- Blockers/caveats: Linux host still cannot perform real macOS GUI/multimedia click-through or verify signing/notarization. Refreshed launcher remains unsigned/not notarized and needs macOS open/playback smoke.

## 2026-05-07T14:44:09Z watchdog pass

- Context read: `docs/redesign-brief-2026-05-07.md`, `docs/redesign-progress-2026-05-07.md`.
- Lint: `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .` → `All checks passed!`.
- Tests: `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q` → `54 passed in 0.58s`.
- Real-vault CLI smoke: `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault '/nas/TikTok Sound Vault'` → `Sound Vault loaded 2036 records from /nas/TikTok Sound Vault`.
- Non-isolated build: `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation` → rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`.
- Mac launcher artifacts: verified `.app` at `dist/SoundVault-mac-launcher-0.1.0-20260507/Sound Vault.app`; `CFBundleExecutable=SoundVault`; `Contents/MacOS/SoundVault` mode `0o755`; `Open Sound Vault.command` mode `0o755`; bundle wheelhouse contains one wheel matching rebuilt wheel; zip/tar each include one wheel and preserve executable bits (`0o755`).
- Checksums:
  - `dist/sound_vault_desktop-0.1.0-py3-none-any.whl` size `27041`, sha256 `558a9440bb13a81fb5c7fdcd9faf216763b251eb5c53d477c1fc6c3ddcdbc3ab`.
  - `dist/sound_vault_desktop-0.1.0.tar.gz` size `47981`, sha256 `9271baca8f39c12f3415eb2afe3eed5a4d14d6ba3c3691344234e691ce3f0cbf`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507.zip` size `33914`, sha256 `980c2b31a68428ae7d7d3f583d7f5e3a1580800dc66556673db805103ea77d5e`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507.tar.gz` size `28204`, sha256 `c699860480b8e64856f3f5bf62ad6613fb7631b91944caa83a35f06541f46263`.
- Git/diff inspection: broad pre-existing modified/untracked redesign + relay files remain; no staged changes; status does not show tracked `dist/`, pycache, ruff cache, or pytest cache artifacts. Tracked diff assignment-style secret scan had no hits. Whole-tree redacted scan found only dummy test fixtures/references such as `device_secret="secret"`, not live credentials.
- Blockers/caveats: Linux host still cannot perform macOS GUI/multimedia click-through or verify signing/notarization. Launcher is unsigned/not notarized and still needs macOS open/playback smoke.

## 2026-05-07T17:50:59Z watchdog pass

- Context read: `docs/redesign-brief-2026-05-07.md`, `docs/redesign-progress-2026-05-07.md`.
- Lint: `/opt/data/venvs/sound-vault-desktop/bin/python -m ruff check .` → `All checks passed!`.
- Tests: `/opt/data/venvs/sound-vault-desktop/bin/python -m pytest -q` → `80 passed in 0.69s`.
- Real-vault CLI smoke: `PYTHONPATH=src /opt/data/venvs/sound-vault-desktop/bin/python -m sound_vault.app --cli --vault '/nas/TikTok Sound Vault'` → `Sound Vault loaded 2036 records from /nas/TikTok Sound Vault`.
- Non-isolated build: `/opt/data/venvs/sound-vault-desktop/bin/python -m build --no-isolation` → rebuilt `sound_vault_desktop-0.1.0.tar.gz` and `sound_vault_desktop-0.1.0-py3-none-any.whl`.
- Mac launcher refresh/verification: `scripts/update_mac_launcher.py --stamp 20260507b` failed recreating the already-used `...20260507b.zip` name on the NAS after unlink (`FileNotFoundError` on open). Tiny artifact workaround: cloned the launcher bundle to stamp `20260507c` and reran `/opt/data/venvs/sound-vault-desktop/bin/python scripts/update_mac_launcher.py --stamp 20260507c` successfully.
- Mac launcher artifacts verified at `dist/SoundVault-mac-launcher-0.1.0-20260507c/Sound Vault.app`: `CFBundleExecutable=SoundVault`; `Contents/MacOS/SoundVault` mode `0o755`; `Open Sound Vault.command` mode `0o755`; bundled wheel checksum matches rebuilt wheel; zip/tar each include the wheel and preserve executable bits (`0o755`).
- Checksums:
  - `dist/sound_vault_desktop-0.1.0-py3-none-any.whl` size `30208`, sha256 `261f501328eaf4a6327c21a591b9426ccf877fdfa3004900f4486ab2a1da4d31`.
  - `dist/sound_vault_desktop-0.1.0.tar.gz` size `57300`, sha256 `119cb316ec0a5883637d29ba4ab5029196bfee2ac8f85e44c4c5abcc0fe9c03c`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507c.zip` size `36692`, sha256 `8252c831cb6e04bcfbb1e26d582deb9dc8ee627dd0a59970692e15871b7ccad9`.
  - `dist/SoundVault-mac-launcher-0.1.0-20260507c.tar.gz` size `31195`, sha256 `0a65aa214ab27be61f572090a0a80a2b123a052e7b0862de4fef18987706197f`.
- Git/diff inspection: `git diff --check` clean; broad pre-existing modified/untracked redesign + relay files remain; no staged changes. Tracked diff secret scan had no hits. Untracked scan matched only setting variable names and dummy test fixtures/references, not live credentials.
- Blockers/caveats: Linux host still cannot perform macOS GUI/multimedia click-through or verify signing/notarization. Launcher remains unsigned/not notarized and needs macOS open/playback smoke.
