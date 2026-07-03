from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Callable


@dataclass(frozen=True)
class ToolStatus:
    name: str
    installed: bool
    path: str = ""
    version: str = ""
    install_help: str = ""


@dataclass(frozen=True)
class PackageStatus:
    name: str
    installed: bool
    install_help: str = ""


@dataclass(frozen=True)
class ModelStatus:
    name: str
    installed: bool
    cache_dir: str
    expected_paths: list[str]
    install_help: str = ""


@dataclass(frozen=True)
class DemucsStatus:
    installed: bool
    path: str = ""
    acceleration: str = "cpu"
    warning: str = ""
    install_help: str = ""


@dataclass(frozen=True)
class DependencyReport:
    tools: dict[str, ToolStatus]
    python_packages: dict[str, PackageStatus]
    models: dict[str, ModelStatus]
    demucs: DemucsStatus
    notes: list[str]


def diagnose_dependencies(
    *,
    model_cache_dir: Path | None = None,
    local_model: str = "base",
    which: Callable[[str], str | None] = shutil.which,
    package_available: Callable[[str], bool] | None = None,
    version_runner: Callable[[list[str]], str] | None = None,
    torch_info: Callable[[], dict[str, object]] | None = None,
) -> DependencyReport:
    package_available = package_available or _package_available
    version_runner = version_runner or _run_version
    torch_info = torch_info or _torch_info
    # Put the bundled/Homebrew bin dirs on PATH first so the report reflects the tools
    # the app actually uses (the packaged .app ships its own node/ffmpeg/ffprobe).
    try:
        from sound_vault.ingest.factory import ensure_media_tools_on_path

        ensure_media_tools_on_path()
    except Exception:  # noqa: BLE001 - diagnostics must never hard-fail
        pass
    cache_dir = (model_cache_dir or Path.home() / ".cache" / "huggingface" / "hub").expanduser()
    tools = {
        name: _tool_status(name, which=which, version_runner=version_runner)
        for name in ("ffmpeg", "ffprobe")
    }
    packages = {
        "faster_whisper": PackageStatus(
            name="faster_whisper",
            installed=package_available("faster_whisper"),
            install_help='python -m pip install -e ".[asr]" or python -m pip install faster-whisper',
        ),
        "openai_whisper": PackageStatus(
            name="openai_whisper",
            installed=package_available("whisper"),
            install_help="python -m pip install openai-whisper",
        ),
    }
    models = {local_model: _model_status(local_model, cache_dir)}
    demucs_path = which("demucs") or ""
    acceleration = _detect_acceleration(torch_info())
    warning = ""
    if acceleration == "cpu":
        warning = "Demucs/source separation will be slow on CPU; use as a recovery/variant path, not mandatory default."
    demucs = DemucsStatus(
        installed=bool(demucs_path) or package_available("demucs"),
        path=demucs_path,
        acceleration=acceleration,
        warning=warning,
        install_help="python -m pip install demucs; GPU acceleration requires working CUDA on NVIDIA or MPS-capable PyTorch on Apple Silicon.",
    )
    return DependencyReport(
        tools=tools,
        python_packages=packages,
        models=models,
        demucs=demucs,
        notes=[
            "PATH executable checks only prove shell tools such as ffmpeg/ffprobe/demucs; Python packages and model caches are separate checks.",
            "Cloud ASR is the recommended default for most editors; local ASR is available when dependencies and models are installed.",
        ],
    )


def _tool_status(name: str, *, which: Callable[[str], str | None], version_runner: Callable[[list[str]], str]) -> ToolStatus:
    path = which(name) or ""
    version = ""
    if path:
        try:
            version = version_runner([name, "-version"]).splitlines()[0][:160]
        except Exception as exc:  # noqa: BLE001
            version = f"version check failed: {exc!r}"
    return ToolStatus(name=name, installed=bool(path), path=path, version=version, install_help=_install_help(name))


def _install_help(name: str) -> str:
    system = platform.system()
    if name in {"ffmpeg", "ffprobe"}:
        if system == "Darwin":
            return "brew install ffmpeg"
        if system == "Windows":
            return "winget install Gyan.FFmpeg"
        return "sudo apt install ffmpeg  # or your distro package manager"
    if name == "demucs":
        return "python -m pip install demucs"
    return "install with your OS package manager"


def _package_available(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def _run_version(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8).stdout


def _model_status(model: str, cache_dir: Path) -> ModelStatus:
    candidates = [
        cache_dir / f"models--Systran--faster-whisper-{model}",
        cache_dir / f"models--openai--whisper-{model}",
        cache_dir / model,
    ]
    installed = any(path.exists() for path in candidates)
    return ModelStatus(
        name=model,
        installed=installed,
        cache_dir=str(cache_dir),
        expected_paths=[str(path) for path in candidates],
        install_help=f"Download by running a local ASR test for model '{model}' with cache dir {cache_dir}",
    )


def _torch_info() -> dict[str, object]:
    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001
        return {"cuda_available": False, "mps_available": False, "platform": platform.system(), "machine": platform.machine()}
    mps_available = bool(getattr(getattr(torch.backends, "mps", None), "is_available", lambda: False)())
    return {
        "cuda_available": bool(torch.cuda.is_available()),
        "mps_available": mps_available,
        "platform": platform.system(),
        "machine": platform.machine(),
    }


def _detect_acceleration(info: dict[str, object]) -> str:
    if info.get("cuda_available"):
        return "cuda"
    if info.get("mps_available") and str(info.get("platform")) == "Darwin" and str(info.get("machine", "")).lower() in {"arm64", "aarch64"}:
        return "mps"
    return "cpu"
