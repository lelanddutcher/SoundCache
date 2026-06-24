"""Atomic JSON writes for vault metadata.

metadata.json is read-modify-written from several places that can run at once —
the background transcription worker, the re-enrich pass, and the user-notes
editor. A plain ``write_text`` can be observed half-written (a reader gets a
truncated file that fails to parse, losing the sound). Writing to a temp file +
fsync + ``os.replace`` makes every write atomic on POSIX, so a reader always
sees either the old or the new complete file — never a torn one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path | str, data: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
