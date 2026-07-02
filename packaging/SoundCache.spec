# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Sound Cache desktop app (macOS, arm64, onedir .app).

Build:  ~/venvs/sound-vault/bin/pyinstaller packaging/SoundCache.spec \
            --distpath dist --workpath build/pyi --noconfirm
Then sign + notarize with packaging/sign_and_notarize.sh.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # packaging/ -> repo root
SRC = ROOT / "src"

datas = []
binaries = []
hiddenimports = []

# App resources are loaded at runtime via Path(__file__).parent / {fonts,assets},
# so they must land at sound_vault/ui/{fonts,assets} in the frozen tree.
datas += [
    (str(SRC / "sound_vault" / "ui" / "fonts"), "sound_vault/ui/fonts"),
    (str(SRC / "sound_vault" / "ui" / "assets"), "sound_vault/ui/assets"),
]

# Node assets for the TikTok login + capture (Playwright's JS driver). At runtime
# tiktok_auth/factory resolve these from sys._MEIPASS when frozen. Chromium itself
# is NOT bundled (~1.5GB) — Playwright reuses the ms-playwright browser cache, or a
# fresh machine runs `playwright install chromium` once.
for _cjs in ("tiktok_login.cjs", "capture_tiktok_audio.cjs", "capture_usage_count.cjs"):
    datas += [(str(ROOT / "scripts" / _cjs), "scripts")]
datas += [(str(ROOT / "package.json"), ".")]
if (ROOT / "node_modules").is_dir():
    datas += [(str(ROOT / "node_modules"), "node_modules")]

# Native / data-carrying deps that PyInstaller's static analysis under-collects
# (ctranslate2 + PyAV ffmpeg dylibs, mlx's Metal lib, whisper VAD assets, etc.).
for pkg in ("faster_whisper", "ctranslate2", "av", "mlx", "mlx_whisper",
            "tokenizers", "huggingface_hub", "yt_dlp", "certifi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # a missing optional dep should not abort the build
        print(f"[spec] collect_all({pkg}) skipped: {exc}")

# Our own package: collect submodules (many are imported lazily) but drop the
# server-only relay/agent trees so we don't drag fastapi/uvicorn/psycopg in.
hiddenimports += [
    m for m in collect_submodules("sound_vault")
    if not m.startswith(("sound_vault.relay", "sound_vault.agent"))
]
hiddenimports += ["Foundation"]  # pyobjc: macOS app-menu-name patch in app.py
hiddenimports += ["sniffio"]     # optional/delayed httpx+anyio dep the graph misses

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "PyQt5", "PyQt6", "IPython", "pytest",
        "fastapi", "uvicorn", "starlette", "psycopg", "psycopg2",
        # torch is only referenced by the optional dependency-diagnostics probe; the real
        # ASR path is ctranslate2 + mlx, so drop the whole torch family (~300MB) + friends.
        "torch", "torchvision", "torchaudio", "torchgen", "functorch",
        "triton", "tensorboard",
        # Qt modules the app never imports (it uses only QtCore/Gui/Widgets/Multimedia/Network)
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtWebChannel", "PySide6.QtQuick3D", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
        "PySide6.Qt3DExtras", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner",
        "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
        "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtWebSockets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Sound Cache",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI / windowed
    disable_windowed_traceback=False,
    argv_emulation=False,   # avoid Carbon/Qt event-loop contention at launch
    target_arch=None,       # match the running interpreter (arm64)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="Sound Cache")

app = BUNDLE(
    coll,
    name="Sound Cache.app",
    icon=str(SRC / "sound_vault" / "ui" / "assets" / "AppIcon.icns"),
    bundle_identifier="io.soundcache.app",
    version="0.3.0",
    info_plist={
        "CFBundleName": "Sound Cache",
        "CFBundleDisplayName": "Sound Cache",
        "CFBundleShortVersionString": "0.3.0",
        "CFBundleVersion": "0.3.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.music",
        "NSRequiresAquaSystemAppearance": False,
        "CFBundleURLTypes": [
            {"CFBundleURLName": "io.soundcache.deeplink", "CFBundleURLSchemes": ["soundcache"]},
        ],
    },
)
