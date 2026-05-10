from __future__ import annotations

import plistlib
import stat
import zipfile
from pathlib import Path

from scripts.update_mac_launcher import build_launcher_package


def _write_fake_bundle(root: Path, version: str, stamp: str, *, launcher_text: str | None = None) -> tuple[Path, Path]:
    dist = root / "dist"
    bundle = dist / f"SoundVault-mac-launcher-{version}-{stamp}"
    macos = bundle / "Sound Vault.app/Contents/MacOS"
    resources = bundle / "Sound Vault.app/Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    (macos / "SoundVault").write_text(launcher_text or "#!/bin/sh\necho launch\n", encoding="utf-8")
    (bundle / "Open Sound Vault.command").write_text("#!/bin/sh\necho open\n", encoding="utf-8")
    (bundle / "Sound Vault.app/Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": "SoundVault"})
    )
    wheel = dist / f"sound_vault_desktop-{version}-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    return bundle, wheel


def test_mac_launcher_packager_uses_supplied_project_root_version_and_stamp(tmp_path):
    version = "9.8.7"
    stamp = "20991231"
    _write_fake_bundle(tmp_path, version, stamp)

    result = build_launcher_package(root=tmp_path, version=version, stamp=stamp)

    assert result.wheel.name == f"sound_vault_desktop-{version}-py3-none-any.whl"
    assert result.zip_path == tmp_path / "dist" / f"SoundVault-mac-launcher-{version}-{stamp}.zip"
    assert result.tar_path == tmp_path / "dist" / f"SoundVault-mac-launcher-{version}-{stamp}.tar.gz"
    assert result.zip_path.exists()
    assert result.tar_path.exists()

    expected_prefix = f"SoundVault-mac-launcher-{version}-{stamp}"
    with zipfile.ZipFile(result.zip_path) as zf:
        launcher_info = zf.getinfo(f"{expected_prefix}/Sound Vault.app/Contents/MacOS/SoundVault")
        launcher_mode = (launcher_info.external_attr // 65536) & 0o777
        assert launcher_mode & stat.S_IXUSR
        plist = plistlib.loads(zf.read(f"{expected_prefix}/Sound Vault.app/Contents/Info.plist"))
        assert plist["CFBundleExecutable"] == "SoundVault"
        assert plist["CFBundlePackageType"] == "APPL"
        assert plist["CFBundleShortVersionString"] == version
        assert plist["CFBundleVersion"] == version
        launcher_text = zf.read(f"{expected_prefix}/Sound Vault.app/Contents/MacOS/SoundVault").decode("utf-8")
        assert 'LAUNCHER="${0:A}"' in launcher_text
        assert 'CONTENTS="${LAUNCHER:h:h}"' in launcher_text
        assert 'APP_DIR="${CONTENTS:h}"' in launcher_text
        assert 'CONTENTS="$APP_DIR/Contents"' not in launcher_text
        assert "ERROR: bundled wheel is missing" in launcher_text
        assert (
            f"{expected_prefix}/Sound Vault.app/Contents/Resources/wheelhouse/"
            f"sound_vault_desktop-{version}-py3-none-any.whl"
        ) in zf.namelist()


def test_mac_launcher_packager_rewrites_stale_launcher_version_lines(tmp_path):
    version = "9.8.7"
    stamp = "20991231"
    _write_fake_bundle(
        tmp_path,
        version,
        stamp,
        launcher_text=(
            "#!/bin/zsh\n"
            'WHEEL="$RESOURCES/wheelhouse/sound_vault_desktop-0.1.0-py3-none-any.whl"\n'
            'VERSION="0.1.0"\n'
        ),
    )

    result = build_launcher_package(root=tmp_path, version=version, stamp=stamp)

    with zipfile.ZipFile(result.zip_path) as zf:
        launcher = zf.read(
            f"SoundVault-mac-launcher-{version}-{stamp}/Sound Vault.app/Contents/MacOS/SoundVault"
        ).decode("utf-8")
    assert f'sound_vault_desktop-{version}-py3-none-any.whl' in launcher
    assert f'VERSION="{version}+{stamp}"' in launcher
    assert "0.1.0" not in launcher


def test_mac_launcher_cache_version_includes_stamp_so_same_project_version_reinstalls(tmp_path):
    version = "0.1.0"
    first_stamp = "20260507"
    second_stamp = "20260507b"
    _write_fake_bundle(
        tmp_path,
        version,
        first_stamp,
        launcher_text=(
            "#!/bin/zsh\n"
            'WHEEL="$RESOURCES/wheelhouse/sound_vault_desktop-0.1.0-py3-none-any.whl"\n'
            'VERSION="0.1.0"\n'
        ),
    )
    _write_fake_bundle(
        tmp_path,
        version,
        second_stamp,
        launcher_text=(
            "#!/bin/zsh\n"
            'WHEEL="$RESOURCES/wheelhouse/sound_vault_desktop-0.1.0-py3-none-any.whl"\n'
            'VERSION="0.1.0"\n'
        ),
    )

    first = build_launcher_package(root=tmp_path, version=version, stamp=first_stamp)
    second = build_launcher_package(root=tmp_path, version=version, stamp=second_stamp)

    with zipfile.ZipFile(first.zip_path) as zf:
        first_launcher = zf.read(
            f"SoundVault-mac-launcher-{version}-{first_stamp}/Sound Vault.app/Contents/MacOS/SoundVault"
        ).decode("utf-8")
    with zipfile.ZipFile(second.zip_path) as zf:
        second_launcher = zf.read(
            f"SoundVault-mac-launcher-{version}-{second_stamp}/Sound Vault.app/Contents/MacOS/SoundVault"
        ).decode("utf-8")
    assert f'VERSION="{version}+{first_stamp}"' in first_launcher
    assert f'VERSION="{version}+{second_stamp}"' in second_launcher
    assert first_launcher != second_launcher


def test_mac_launcher_packager_creates_missing_bundle_from_scratch(tmp_path):
    version = "1.2.3"
    stamp = "20260101"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"sound_vault_desktop-{version}-py3-none-any.whl").write_bytes(b"fake wheel")

    result = build_launcher_package(root=tmp_path, version=version, stamp=stamp)

    bundle = dist / f"SoundVault-mac-launcher-{version}-{stamp}"
    launcher = bundle / "Sound Vault.app/Contents/MacOS/SoundVault"
    readme = bundle / "README.txt"
    assert launcher.exists()
    assert readme.exists()
    assert "exec \"$APP_SUPPORT/venv/bin/sound-vault\"" in launcher.read_text(encoding="utf-8")
    assert "~/Library/Logs/Sound Vault/launcher.log" in readme.read_text(encoding="utf-8")
    assert result.zip_path.exists()


def test_mac_launcher_packager_fails_when_expected_wheel_is_missing(tmp_path):
    (tmp_path / "dist").mkdir()

    try:
        build_launcher_package(root=tmp_path, version="1.2.3", stamp="20260101")
    except FileNotFoundError as exc:
        assert "sound_vault_desktop-1.2.3-py3-none-any.whl" in str(exc)
    else:
        raise AssertionError("missing wheel should fail closed")
