"""Headless ingest worker: optionally poll the relay, then drain the local inbox.

Entry point: ``sound-vault-ingest``. Runs once by default, or ``--watch`` to loop.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import time
from typing import Callable

from sound_vault.ingest.factory import build_ingest_service
from sound_vault.ingest.service import IngestOutcome, IngestService
from sound_vault.ingest.shortcut_inbox import ShortcutInboxItem, ShortcutInboxStore
from sound_vault.settings import AppSettings, default_index_path

OnCycle = Callable[[list[tuple[ShortcutInboxItem, IngestOutcome]]], None]


def run_ingest(
    *,
    service: IngestService,
    store: ShortcutInboxStore,
    relay_client=None,
    once: bool = True,
    interval: float = 20.0,
    max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    on_cycle: OnCycle | None = None,
) -> list[tuple[ShortcutInboxItem, IngestOutcome]]:
    """Poll (optional) + drain. Returns the last cycle's outcomes (once mode)."""
    last: list[tuple[ShortcutInboxItem, IngestOutcome]] = []
    while True:
        if relay_client is not None:
            try:
                relay_client.poll_to_inbox(store.path)
            except Exception as exc:  # noqa: BLE001 - a relay hiccup shouldn't stop the worker
                print(f"relay poll failed: {exc}")
        last = service.drain_inbox(store, max_attempts=max_attempts)
        if on_cycle is not None:
            on_cycle(last)
        if once:
            return last
        sleep(interval)


def _print_cycle(outcomes: list[tuple[ShortcutInboxItem, IngestOutcome]]) -> None:
    if not outcomes:
        print("inbox empty")
        return
    for _item, outcome in outcomes:
        detail = outcome.reason or (outcome.folder.name if outcome.folder else "")
        print(f"  {outcome.status:9} {outcome.music_id or '-'}  {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sound Vault ingest worker (drain inbox -> vault)")
    parser.add_argument("--vault", type=Path, default=None, help="Vault folder (default: app setting)")
    parser.add_argument("--index", type=Path, default=None, help="Index DB path (default: app data dir)")
    parser.add_argument("--watch", action="store_true", help="Loop instead of running once")
    parser.add_argument("--interval", type=float, default=20.0, help="Seconds between cycles in --watch mode")
    parser.add_argument("--poll-relay", action="store_true", help="Poll the relay before draining")
    parser.add_argument("--max-attempts", type=int, default=3, help="Retries before an item is marked failed")
    args = parser.parse_args(argv)

    settings = AppSettings()
    vault_root = args.vault or settings.vault_root()
    index_path = args.index or default_index_path()
    service = build_ingest_service(vault_root=vault_root, index_path=index_path)
    store = ShortcutInboxStore(vault_root / "inbox" / "urls" / "shortcut-inbox.jsonl")

    relay_client = None
    if args.poll_relay:
        from sound_vault.relay.client import RelayClient

        base_url = settings.relay_base_url()
        pair_code = settings.relay_pair_code()
        device_id = settings.relay_device_id()
        device_secret = settings.relay_device_secret()
        if all(value.strip() for value in (base_url, pair_code, device_id, device_secret)):
            relay_client = RelayClient(
                base_url=base_url, pair_code=pair_code, device_id=device_id, device_secret=device_secret
            )
        else:
            print("relay not fully configured; skipping poll")

    print(f"ingest worker | vault={vault_root} | index={index_path} | watch={args.watch}")
    run_ingest(
        service=service,
        store=store,
        relay_client=relay_client,
        once=not args.watch,
        interval=args.interval,
        max_attempts=args.max_attempts,
        on_cycle=_print_cycle,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
