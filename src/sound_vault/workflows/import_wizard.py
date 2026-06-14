from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable

from sound_vault.importers.tiktok_archive import FavoriteSoundImportResult, FavoriteSoundImportSummary, load_favorite_sound_rows, write_normalized_favorite_sounds_import
from sound_vault.vault.indexer import build_index
from sound_vault.vault.package_writer import PackageImportResult, package_imported_sounds
from sound_vault.workers.oembed import OEmbedEnrichmentResult, enrich_favorite_sounds_oembed
from sound_vault.workers.result import WorkerRunResult, verify_durable_outputs, write_worker_run


class ImportWizardStage(str, Enum):
    EMPTY = "empty"
    PREVIEWED = "previewed"
    NORMALIZED = "normalized"
    ENRICHED = "enriched"
    PACKAGED = "packaged"
    VERIFIED = "verified"


@dataclass(frozen=True)
class VerificationReport:
    status: str
    counts: dict[str, int]
    outputs: dict[str, str]
    missing_outputs: list[str]
    next_actions: list[str]


class ImportWizard:
    """App-callable state machine for TikTok data-export favorite sounds.

    The wizard wraps the existing deterministic import/enrich/package spine and
    adds preview gating plus durable verification. It has no Qt dependency so the
    desktop UI, CLI, and tests can all run the same workflow.
    """

    def __init__(
        self,
        *,
        vault_root: Path,
        date_label: str | None = None,
        oembed_fetch_json: Callable[[str], dict[str, Any]] | None = None,
        oembed_delay_seconds: float = 0.6,
    ) -> None:
        self.vault_root = vault_root.expanduser()
        self.date_label = date_label
        self.oembed_fetch_json = oembed_fetch_json
        self.oembed_delay_seconds = oembed_delay_seconds
        self.stage = ImportWizardStage.EMPTY
        self.export_path: Path | None = None
        self.preview_summary: FavoriteSoundImportSummary | None = None
        self.normalized_result: FavoriteSoundImportResult | None = None
        self.enriched_result: OEmbedEnrichmentResult | None = None
        self.package_result: PackageImportResult | None = None
        self.verification_report: VerificationReport | None = None

    def select_export(self, export_path: Path) -> FavoriteSoundImportSummary:
        records, malformed_rows = load_favorite_sound_rows(export_path)
        temp_result = write_normalized_favorite_sounds_import(
            export_path,
            self.vault_root / "catalog" / "imports" / ".previews",
            date_label="preview",
            vault_root=self.vault_root,
        )
        # Preview is allowed to write into a hidden scratch dir so it exercises
        # the exact repair/dedupe code path without creating sound packages.
        summary = temp_result.summary
        if malformed_rows != summary.malformed_rows or len(records) != summary.record_count:
            raise RuntimeError("preview summary drifted from parsed rows")
        self.export_path = export_path.expanduser()
        self.preview_summary = summary
        self.stage = ImportWizardStage.PREVIEWED
        return summary

    def normalize(self) -> FavoriteSoundImportResult:
        if self.export_path is None:
            raise RuntimeError("select_export must run before normalize")
        result = write_normalized_favorite_sounds_import(
            self.export_path,
            self.vault_root / "catalog" / "imports",
            date_label=self.date_label,
            vault_root=self.vault_root,
        )
        self.normalized_result = result
        self.stage = ImportWizardStage.NORMALIZED
        return result

    def enrich(self) -> OEmbedEnrichmentResult:
        if self.normalized_result is None:
            raise RuntimeError("normalize must run before enrich")
        result = enrich_favorite_sounds_oembed(
            self.normalized_result.json_path,
            self.vault_root / "catalog" / "imports",
            date_label=self.date_label,
            delay_seconds=self.oembed_delay_seconds,
            fetch_json=self.oembed_fetch_json,
        )
        self.enriched_result = result
        self.stage = ImportWizardStage.ENRICHED
        return result

    def package(self) -> PackageImportResult:
        input_path: Path | None = None
        if self.enriched_result is not None:
            input_path = self.enriched_result.json_path
        elif self.normalized_result is not None:
            input_path = self.normalized_result.json_path
        if input_path is None:
            raise RuntimeError("normalize or enrich must run before package")
        result = package_imported_sounds(input_path, self.vault_root)
        self.package_result = result
        self.stage = ImportWizardStage.PACKAGED
        return result

    def rebuild_index_and_verify(self, *, search_terms: list[str] | None = None) -> VerificationReport:
        if self.package_result is None:
            raise RuntimeError("package must run before verification")
        records = build_index(self.vault_root, load_sidecars=True)
        sound_folders = [path for path in (self.vault_root / "sounds").glob("* - *") if path.is_dir()]
        required = [self.vault_root / "catalog" / "sounds.jsonl", self.vault_root / "catalog" / "sounds.csv"]
        required.extend(folder / "metadata.json" for folder in sound_folders)
        checks = verify_durable_outputs(required)
        search_hits = _search_hits(records, search_terms or [])
        counts = {
            "metadata_records": len(records),
            "sound_folders": len(sound_folders),
            "catalog_files": sum(1 for path in required[:2] if path.exists()),
            "verified_outputs": len(checks.present),
            "missing_outputs": len(checks.missing),
            "search_hits": search_hits,
        }
        status = "ok" if not checks.missing and len(records) >= self.package_result.summary.created_count + self.package_result.summary.updated_count else "partial"
        next_actions: list[str] = []
        if checks.missing:
            next_actions.append("repair missing catalog/metadata files before running media workers")
        if search_terms and search_hits == 0:
            status = "partial"
            next_actions.append("rebuild the SQLite/search index and verify imported titles/transcripts are searchable")
        summary_path = self.vault_root / "workers" / "verification" / "latest_import_verification.json"
        report = VerificationReport(
            status=status,
            counts=counts,
            outputs={"summary_json": str(summary_path), "catalog_jsonl": str(self.vault_root / "catalog" / "sounds.jsonl")},
            missing_outputs=checks.missing,
            next_actions=next_actions,
        )
        _write_report(summary_path, report)
        write_worker_run(
            self.vault_root,
            WorkerRunResult(
                worker="verification",
                status=status,
                counts=counts,
                outputs=report.outputs,
                verified_outputs=checks.present,
                missing_outputs=checks.missing,
                next_actions=next_actions,
            ),
        )
        self.verification_report = report
        self.stage = ImportWizardStage.VERIFIED
        return report


def _search_hits(records: list[Any], terms: list[str]) -> int:
    if not terms:
        return 0
    hits = 0
    needles = [term.casefold() for term in terms if term]
    for record in records:
        haystack = json.dumps(getattr(record, "__dict__", record), ensure_ascii=False, default=str).casefold()
        if any(needle in haystack for needle in needles):
            hits += 1
    return hits


def _write_report(path: Path, report: VerificationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": report.status, "counts": report.counts, "outputs": report.outputs, "missing_outputs": report.missing_outputs, "next_actions": report.next_actions}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
