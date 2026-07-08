"""Durable, append-only chain-of-custody ledger for ingested sounds.

The relay poll is DESTRUCTIVE — the server deletes an item the moment it hands it
to a device — so a crash between "polled" and "safely queued" loses the share for
good. This ledger is the machine's permanent record of every item the relay
delivered and what became of it.

Unlike the inbox queue (rewritten in full on every status change, so it forgets
history), the ledger is append-only and fsync'd: each line is one immutable event.
It is the source of truth for reconciliation ("did everything the relay sent land
in the vault?") and for recovery (the retained URL lets us re-queue a stranded or
phantom-imported sound). It is never pruned or rewritten, so it cannot lose data.

Event kinds (one JSON object per line):
  received  {relay_id, url, source, note}         — the relay delivered this
  imported  {relay_id, url, music_id, folder}     — it landed in the vault
  failed    {relay_id, url, error, terminal}      — an ingest attempt failed
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReceiptEvent:
    event: str  # received | imported | failed
    at: str
    relay_id: str | None = None
    url: str = ""
    source: str = ""
    note: str = ""
    music_id: str | None = None
    folder: str | None = None
    error: str | None = None
    terminal: bool = False

    @property
    def key(self) -> str:
        """The identity used to correlate a delivery with its later outcome."""
        return self.relay_id or self.url


class ReceiptLedger:
    """Append-only JSONL of ingestion custody events (never pruned or rewritten)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def beside(cls, inbox_path: Path) -> "ReceiptLedger":
        """The ledger that lives next to an inbox JSONL (``receipts.jsonl``)."""
        return cls(Path(inbox_path).with_name("receipts.jsonl"))

    # ---- writes (durable) -------------------------------------------------

    def _append_lines(self, records: list[dict[str, Any]]) -> None:
        """Append records as JSONL, flushed + fsync'd so a recorded line survives a
        crash or power loss. Best-effort: a ledger-write failure must never abort
        ingestion (the ledger is an audit/recovery aid, not the primary store)."""
        if not records:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            pass

    def record_received_many(self, deliveries: list[dict[str, Any]], *, now: str | None = None) -> None:
        """Record a batch of relay deliveries in a SINGLE fsync'd append. This is the
        durable receipt that must land BEFORE the destructive poll's items are handed
        to the inbox — so a crash right after leaves proof of what was delivered."""
        stamp = now or _now_iso()
        self._append_lines(
            [
                {
                    "event": "received",
                    "at": stamp,
                    "relay_id": d.get("relay_id"),
                    "url": str(d.get("url") or ""),
                    "source": str(d.get("source") or ""),
                    "note": str(d.get("note") or ""),
                }
                for d in deliveries
            ]
        )

    def record_imported(
        self, *, relay_id: str | None, url: str, music_id: str | None, folder: str | None, now: str | None = None
    ) -> None:
        self._append_lines(
            [
                {
                    "event": "imported",
                    "at": now or _now_iso(),
                    "relay_id": relay_id,
                    "url": url,
                    "music_id": music_id,
                    "folder": folder,
                }
            ]
        )

    def record_failed(
        self, *, relay_id: str | None, url: str, error: str, terminal: bool = False, now: str | None = None
    ) -> None:
        self._append_lines(
            [
                {
                    "event": "failed",
                    "at": now or _now_iso(),
                    "relay_id": relay_id,
                    "url": url,
                    "error": (error or "")[:2000],
                    "terminal": bool(terminal),
                }
            ]
        )

    # ---- reads ------------------------------------------------------------

    def read_events(self) -> list[ReceiptEvent]:
        """All events in append order. Unparseable lines are skipped (the ledger is
        never rewritten, so a bad line is inert — it can't corrupt the history)."""
        if not self.path.exists():
            return []
        events: list[ReceiptEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                events.append(
                    ReceiptEvent(
                        event=str(data.get("event") or ""),
                        at=str(data.get("at") or ""),
                        relay_id=(str(data["relay_id"]) if data.get("relay_id") else None),
                        url=str(data.get("url") or ""),
                        source=str(data.get("source") or ""),
                        note=str(data.get("note") or ""),
                        music_id=(str(data["music_id"]) if data.get("music_id") else None),
                        folder=(str(data["folder"]) if data.get("folder") else None),
                        error=(str(data["error"]) if data.get("error") else None),
                        terminal=bool(data.get("terminal")),
                    )
                )
        return events

    def deliveries(self) -> dict[str, ReceiptEvent]:
        """Every item the relay ever delivered, keyed by relay_id (or url). The
        universe reconciliation checks against the vault. Latest received wins."""
        out: dict[str, ReceiptEvent] = {}
        for event in self.read_events():
            if event.event == "received" and event.key:
                out[event.key] = event
        return out

    def latest_outcome(self) -> dict[str, ReceiptEvent]:
        """The most recent imported/failed event per delivery key — the current
        recorded fate of each sound (before verifying against the vault)."""
        out: dict[str, ReceiptEvent] = {}
        for event in self.read_events():
            if event.event in ("imported", "failed") and event.key:
                out[event.key] = event
        return out
