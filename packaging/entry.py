"""PyInstaller entry point for the Sound Cache desktop app.

Kept tiny and side-effect-free at import time so the frozen bootstrap just calls
into the real app. See packaging/SoundCache.spec for the build.
"""
from sound_vault.app import main

if __name__ == "__main__":
    main()
