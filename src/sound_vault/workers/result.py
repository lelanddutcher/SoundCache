from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DurableOutputCheck:
    status: str
    present: list[str]
    missing: list[str]


@dataclass(frozen=True)
class WorkerRunResult:
    worker: str
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    verified_outputs: list[str] = field(default_factory=list)
    missing_outputs: list[str] = field(default_factory=list)
    input_manifest: str = ""
    next_actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    notes: list[str] = field(default_factory=list)

    def normalized(self) -> "WorkerRunResult":
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        started = self.started_at or now
        finished = self.finished_at or now
        status = self.status
        if self.missing_outputs and status == "ok":
            status = "partial"
        if self.errors and status == "ok":
            status = "partial"
        return WorkerRunResult(
            worker=self.worker,
            status=status,
            counts=dict(self.counts),
            outputs=dict(self.outputs),
            verified_outputs=list(self.verified_outputs),
            missing_outputs=list(self.missing_outputs),
            input_manifest=self.input_manifest,
            next_actions=list(self.next_actions),
            errors=list(self.errors),
            started_at=started,
            finished_at=finished,
            notes=list(self.notes),
        )


@dataclass(frozen=True)
class WrittenWorkerRun:
    summary_path: Path
    events_path: Path
    failed_path: Path
    result: WorkerRunResult


def verify_durable_outputs(paths: list[str | Path]) -> DurableOutputCheck:
    present: list[str] = []
    missing: list[str] = []
    for path_like in paths:
        path = Path(path_like)
        if path.exists():
            present.append(str(path))
        else:
            missing.append(str(path))
    return DurableOutputCheck(status="ok" if not missing else "partial", present=present, missing=missing)


def write_worker_run(vault_root: Path, result: WorkerRunResult, *, events: list[dict[str, Any]] | None = None) -> WrittenWorkerRun:
    clean = result.normalized()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    worker_root = vault_root / "workers" / clean.worker
    runs_root = worker_root / "runs"
    failed_root = worker_root / "failed"
    summary_path = runs_root / f"{stamp}.json"
    events_path = runs_root / f"{stamp}.jsonl"
    failed_path = failed_root / f"{stamp}.json"
    payload = asdict(clean)
    _write_json_atomic(summary_path, payload)
    _write_jsonl_atomic(events_path, events or [{"event": "worker.finished", **payload}])
    if clean.errors or clean.missing_outputs:
        _write_json_atomic(failed_path, {"errors": clean.errors, "missing_outputs": clean.missing_outputs})
    else:
        failed_root.mkdir(parents=True, exist_ok=True)
    return WrittenWorkerRun(summary_path=summary_path, events_path=events_path, failed_path=failed_path, result=clean)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)
