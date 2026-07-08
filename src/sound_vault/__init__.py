__all__ = ["__version__"]

# Single source of truth = the installed package metadata (driven by pyproject's
# [project].version). Falls back to the literal for an uninstalled source checkout.
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    try:
        __version__ = _pkg_version("sound-vault-desktop")
    except PackageNotFoundError:
        __version__ = "0.3.1"
except Exception:  # noqa: BLE001 - never let version lookup break import
    __version__ = "0.3.1"
