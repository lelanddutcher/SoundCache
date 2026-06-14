from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys
import traceback

from sound_vault.diagnostics import exception_fields, write_event
from sound_vault.settings import AppSettings, default_index_path, index_path_for_vault, user_config_dir, user_data_dir
from sound_vault.vault.indexer import resolve_vault_root


def _app_log_path() -> Path:
    return user_data_dir() / "app.log"


def _write_launch_failure(exc: BaseException) -> Path:
    log_path = _app_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "Sound Cache GUI launch failed\n\n" + "".join(traceback.format_exception(exc)),
        encoding="utf-8",
    )
    return log_path


def _print_diagnostics(vault_root: Path) -> None:
    print("Sound Cache diagnostics")
    print(f"python: {sys.executable}")
    print(f"version: {sys.version.replace(chr(10), ' ')}")
    print(f"platform: {platform.platform()} {platform.machine()}")
    print(f"config dir: {user_config_dir()}")
    print(f"data dir: {user_data_dir()}")
    print(f"legacy index path: {default_index_path()}")
    print(f"vault index path: {index_path_for_vault(vault_root)}")
    print(f"vault root: {vault_root}")
    print(f"vault exists: {vault_root.exists()}")
    index_path_for_vault(vault_root).parent.mkdir(parents=True, exist_ok=True)
    print("data dir writable: yes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sound Cache desktop app")
    parser.add_argument("--vault", type=Path, default=None, help="Path to a Sound Cache folder")
    parser.add_argument("--cli", action="store_true", help="Print index count instead of opening the GUI")
    parser.add_argument("--diagnose", action="store_true", help="Print launch diagnostics without importing Qt")
    parser.add_argument(
        "--import-favorite-sounds",
        type=Path,
        default=None,
        help="Normalize a TikTok favorite sounds data-export file into catalog/imports",
    )
    parser.add_argument(
        "--enrich-favorite-sounds-oembed",
        type=Path,
        default=None,
        help="Enrich a normalized favorite-sounds JSON through public TikTok oEmbed",
    )
    parser.add_argument(
        "--package-imported-sounds",
        type=Path,
        default=None,
        help="Package normalized/enriched favorite-sound rows into metadata-only vault folders",
    )
    parser.add_argument("--import-date-label", default=None, help="Date/version label for import output files")
    parser.add_argument("--oembed-delay", type=float, default=0.6, help="Delay between oEmbed requests in seconds")
    parser.add_argument("--diagnose-dependencies", action="store_true", help="Print ffmpeg/local ASR/model/Demucs dependency diagnostics")
    parser.add_argument("--verify-vault", action="store_true", help="Rebuild/search the disposable index and verify durable vault files")
    parser.add_argument("--verify-search", action="append", default=[], help="Search term that must be found during --verify-vault; may be repeated")
    parser.add_argument(
        "--run-import-workflow",
        type=Path,
        default=None,
        help="Run the full local import workflow: preview, normalize, optional oEmbed, package, rebuild index, verify",
    )
    parser.add_argument("--skip-oembed", action="store_true", help="Skip public oEmbed during --run-import-workflow")
    args = parser.parse_args()
    vault_root = resolve_vault_root(args.vault or AppSettings().vault_root())
    write_event(
        "app.start",
        cli=args.cli,
        diagnose=args.diagnose,
        import_favorite_sounds=bool(args.import_favorite_sounds),
        enrich_favorite_sounds_oembed=bool(args.enrich_favorite_sounds_oembed),
        package_imported_sounds=bool(args.package_imported_sounds),
        vault_root=str(vault_root),
    )
    if args.diagnose:
        _print_diagnostics(vault_root)
        write_event("app.diagnose_complete", vault_root=str(vault_root))
        return
    if args.diagnose_dependencies:
        from dataclasses import asdict
        from sound_vault.dependency_diagnostics import diagnose_dependencies

        config = AppSettings().transcription_config()
        report = diagnose_dependencies(
            model_cache_dir=Path(str(config.get("model_cache_dir") or "")).expanduser() if config.get("model_cache_dir") else None,
            local_model=str(config.get("local_model") or "base"),
        )
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
        write_event("app.dependency_diagnostics_complete", vault_root=str(vault_root))
        return
    if args.run_import_workflow:
        from sound_vault.workflows.import_wizard import ImportWizard

        wizard = ImportWizard(vault_root=vault_root, date_label=args.import_date_label, oembed_delay_seconds=args.oembed_delay)
        preview = wizard.select_export(args.run_import_workflow)
        print(
            "Preview TikTok favorite sounds export: "
            f"{preview.record_count} rows, {preview.unique_music_ids} unique IDs, "
            f"{preview.already_in_vault} already in vault, {preview.new_to_vault} new"
        )
        normalized = wizard.normalize()
        print(f"Normalized JSON: {normalized.json_path}")
        if not args.skip_oembed:
            enriched = wizard.enrich()
            print(f"oEmbed enriched JSON: {enriched.json_path} ({enriched.summary.ok_count} ok, {enriched.summary.error_count} errors)")
        packaged = wizard.package()
        print(f"Packaged imported sounds: {packaged.summary.created_count} created, {packaged.summary.updated_count} updated")
        report = wizard.rebuild_index_and_verify(search_terms=args.verify_search)
        print(f"Verification: {report.status} {report.counts}")
        print(f"Verification summary: {report.outputs['summary_json']}")
        write_event("app.import_workflow_complete", vault_root=str(vault_root), status=report.status, counts=report.counts)
        return
    if args.verify_vault:
        from sound_vault.workflows.import_wizard import VerificationReport, _write_report, _search_hits
        from sound_vault.vault.indexer import build_index
        from sound_vault.workers.result import WorkerRunResult, verify_durable_outputs, write_worker_run

        records = build_index(vault_root, load_sidecars=True)
        sound_folders = [path for path in (vault_root / "sounds").glob("* - *") if path.is_dir()]
        required = [vault_root / "catalog" / "sounds.jsonl", vault_root / "catalog" / "sounds.csv"]
        required.extend(folder / "metadata.json" for folder in sound_folders)
        checks = verify_durable_outputs(required)
        search_hits = _search_hits(records, args.verify_search)
        status = "ok" if not checks.missing and (not args.verify_search or search_hits > 0) else "partial"
        counts = {"metadata_records": len(records), "sound_folders": len(sound_folders), "verified_outputs": len(checks.present), "missing_outputs": len(checks.missing), "search_hits": search_hits}
        summary_path = vault_root / "workers" / "verification" / "latest_manual_verification.json"
        report = VerificationReport(status=status, counts=counts, outputs={"summary_json": str(summary_path)}, missing_outputs=checks.missing, next_actions=[])
        _write_report(summary_path, report)
        write_worker_run(vault_root, WorkerRunResult(worker="verification", status=status, counts=counts, outputs=report.outputs, verified_outputs=checks.present, missing_outputs=checks.missing))
        print(f"Verification: {status} {counts}")
        print(f"Verification summary: {summary_path}")
        write_event("app.verify_vault_complete", vault_root=str(vault_root), status=status, counts=counts)
        return
    if args.import_favorite_sounds:
        from sound_vault.importers.tiktok_archive import write_normalized_favorite_sounds_import

        result = write_normalized_favorite_sounds_import(
            args.import_favorite_sounds,
            vault_root / "catalog" / "imports",
            date_label=args.import_date_label,
            vault_root=vault_root,
        )
        summary = result.summary
        print(
            "Imported TikTok favorite sounds export: "
            f"{summary.record_count} rows, {summary.unique_music_ids} unique IDs, "
            f"{summary.blank_ids} blank IDs, {summary.duplicate_music_ids} duplicate rows, "
            f"{summary.malformed_rows} malformed rows, "
            f"{summary.already_in_vault} already in vault, {summary.new_to_vault} new"
        )
        print(f"JSON: {result.json_path}")
        print(f"CSV: {result.csv_path}")
        print(f"Summary: {result.summary_path}")
        write_event(
            "app.favorite_sounds_import_complete",
            vault_root=str(vault_root),
            source_file=str(args.import_favorite_sounds),
            records=summary.record_count,
            unique_music_ids=summary.unique_music_ids,
            blank_ids=summary.blank_ids,
            duplicate_music_ids=summary.duplicate_music_ids,
            malformed_rows=summary.malformed_rows,
            already_in_vault=summary.already_in_vault,
            new_to_vault=summary.new_to_vault,
            ambiguous_matches=summary.ambiguous_matches,
        )
        return
    if args.enrich_favorite_sounds_oembed:
        from sound_vault.workers.oembed import enrich_favorite_sounds_oembed

        result = enrich_favorite_sounds_oembed(
            args.enrich_favorite_sounds_oembed,
            vault_root / "catalog" / "imports",
            date_label=args.import_date_label,
            delay_seconds=args.oembed_delay,
        )
        summary = result.summary
        print(
            "Enriched TikTok favorite sounds through public oEmbed: "
            f"{summary.record_count} rows, {summary.ok_count} ok, "
            f"{summary.error_count} errors, {summary.resumed_count} resumed"
        )
        print(f"JSON: {result.json_path}")
        print(f"CSV: {result.csv_path}")
        write_event(
            "app.favorite_sounds_oembed_complete",
            vault_root=str(vault_root),
            source_file=str(args.enrich_favorite_sounds_oembed),
            records=summary.record_count,
            ok=summary.ok_count,
            errors=summary.error_count,
            resumed=summary.resumed_count,
        )
        return
    if args.package_imported_sounds:
        from sound_vault.vault.package_writer import package_imported_sounds

        result = package_imported_sounds(args.package_imported_sounds, vault_root)
        summary = result.summary
        print(
            "Packaged imported TikTok sounds: "
            f"{summary.created_count} created, {summary.updated_count} updated, "
            f"{summary.metadata_only_count} metadata-only catalog rows, "
            f"{summary.failed_count} failures"
        )
        print(f"Catalog JSONL: {result.catalog_jsonl}")
        print(f"Catalog CSV: {result.catalog_csv}")
        print(f"Failure log: {result.failure_log}")
        write_event(
            "app.favorite_sounds_package_complete",
            vault_root=str(vault_root),
            source_file=str(args.package_imported_sounds),
            created=summary.created_count,
            updated=summary.updated_count,
            metadata_only=summary.metadata_only_count,
            failures=summary.failed_count,
        )
        return
    if args.cli:
        from sound_vault.vault.indexer import build_index

        records = build_index(vault_root, load_sidecars=False)
        print(f"Sound Cache loaded {len(records)} records from {vault_root}")
        write_event("app.cli_index_complete", vault_root=str(vault_root), records=len(records))
        return
    try:
        write_event("gui.import_start", vault_root=str(vault_root))
        from sound_vault.ui.desktop import run_desktop

        write_event("gui.import_complete", vault_root=str(vault_root))
        raise SystemExit(run_desktop(vault_root))
    except Exception as exc:
        log_path = _write_launch_failure(exc)
        write_event("gui.launch_exception", log_path=str(log_path), **exception_fields(exc))
        print(f"Sound Cache failed to launch. Details: {log_path}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
