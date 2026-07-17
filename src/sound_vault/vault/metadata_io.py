"""Durable, corruption-tolerant JSON I/O for vault metadata.

metadata.json is read-modify-written from several places that can run at once —
the background transcription worker, the re-enrich pass, and the user-notes
editor. Two failure modes have bitten us, both because the vault lives on an NFS
mount (``/Volumes/zpool``):

* **Torn writes** — a plain ``write_text`` can be observed half-written (a reader
  gets a truncated file that fails to parse, losing the sound). We write to a
  temp file + fsync + ``os.replace`` (atomic on POSIX) and then fsync the parent
  directory, so the rename itself reaches the server before a reader looks.

* **NUL-padded / stale tails** — even after an atomic replace, NFS close-to-open
  consistency can hand a reader valid JSON followed by a run of ``\\x00`` padding
  or stale trailing bytes. ``json.loads`` then dies with "Extra data" and the
  sound reads as broken. ``read_json_lenient`` strips such a tail so the file is
  still recovered (and callers that rewrite it — e.g. the transcription worker —
  then heal it on disk).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_RAISE = object()


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
        # Flush the rename to the (possibly NFS) server so a subsequent open()+
        # read() can't observe a stale, short, or NUL-padded view of the entry.
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _fsync_dir(directory: Path) -> None:
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _strip_corrupt_tail(text: str) -> str:
    """Drop a trailing run of NULs / Unicode replacement chars / whitespace that
    an NFS readback can append after otherwise-valid JSON."""
    return text.rstrip("\x00� \t\r\n\f\v")


def read_json_lenient(path: Path | str, *, default: Any = _RAISE) -> Any:
    """Read a single JSON value from a vault file, tolerating a corrupt tail.

    Order of attempts: clean parse → parse after stripping a NUL/garbage tail →
    ``raw_decode`` of the first complete value (ignoring trailing bytes). If all
    fail, raise the decode error — unless ``default`` was supplied, in which case
    return it. Use this for whole-file JSON (metadata.json, manifests, transcript
    sidecars); it is NOT for JSONL, where each line is parsed independently.
    """
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stripped = _strip_corrupt_tail(text)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    try:
        obj, _end = json.JSONDecoder().raw_decode(stripped.lstrip())
        return obj
    except json.JSONDecodeError:
        if default is not _RAISE:
            return default
        raise
