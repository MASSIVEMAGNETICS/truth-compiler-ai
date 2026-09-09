"""CSV/JSON export and checksum receipt generation."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .database import CatalogDatabase
from .scanner import utc_now


class UnsafeReportLocation(ValueError):
    pass


CATALOG_COLUMNS = (
    "relative_path",
    "file_name",
    "extension",
    "size_bytes",
    "mtime_ns",
    "sha256",
    "status",
    "title",
    "artist",
    "album",
    "album_artist",
    "track_number",
    "disc_number",
    "date",
    "genre",
    "composer",
    "copyright",
    "isrc",
    "duration_seconds",
    "bitrate",
    "sample_rate",
    "channels",
    "bits_per_sample",
    "container",
    "error_code",
    "error_message",
    "scanned_at",
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _atomic_write(
    path: Path, writer: Callable[[Any], None], *, newline: str | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:  # noqa: BLE001 - cleanup must also run for cancellation signals
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write(
        path,
        lambda handle: json.dump(
            payload, handle, ensure_ascii=False, indent=2, sort_keys=True
        ),
    )


def _write_csv(
    path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]
) -> None:
    def spreadsheet_safe(value: Any) -> Any:
        if isinstance(value, str):
            first = value.lstrip()[:1]
            if first in {"=", "+", "-", "@"}:
                return "'" + value
        return value

    def writer(handle: Any) -> None:
        output = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        output.writeheader()
        output.writerows(
            {
                column: spreadsheet_safe(row.get(column))
                for column in columns
            }
            for row in rows
        )

    _atomic_write(path, writer, newline="")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_reports(
    database_path: Path,
    scan_id: int,
    output_directory: Path,
    *,
    source_label: str | None = None,
) -> dict[str, Path]:
    """Export a completed or partial scan outside the source catalog tree."""
    database_path = Path(database_path).expanduser().resolve()
    with CatalogDatabase(database_path) as database:
        scan = database.get_scan(scan_id)
        files = database.list_files(scan_id)
        duplicates = database.duplicate_groups(scan_id)
        issues = database.list_issues(scan_id)

    root = Path(scan["root_path"]).resolve()
    exported_scan = {
        key: scan[key]
        for key in (
            "id",
            "status",
            "tool_version",
            "started_at",
            "updated_at",
            "completed_at",
            "files_discovered",
            "files_processed",
            "files_reused",
            "files_with_errors",
            "bytes_hashed",
            "error_message",
        )
    }
    exported_scan["root_path"] = source_label or scan["root_path"]
    output = Path(output_directory).expanduser().resolve()
    if _is_within(output, root):
        raise UnsafeReportLocation(
            "Reports must be written outside the scanned source folder to preserve read-only operation."
        )
    output.mkdir(parents=True, exist_ok=True)

    duplicate_index: dict[str, dict[str, Any]] = {
        group["sha256"]: group for group in duplicates
    }
    catalog_rows: list[dict[str, Any]] = []
    for row in files:
        exported = {column: row.get(column) for column in CATALOG_COLUMNS}
        group = duplicate_index.get(row.get("sha256", ""))
        exported["duplicate_group"] = group["group"] if group else ""
        exported["duplicate_count"] = group["file_count"] if group else 0
        exported["metadata"] = json.loads(row["metadata_json"])
        catalog_rows.append(exported)

    duplicate_rows: list[dict[str, Any]] = []
    for group in duplicates:
        for member in group["files"]:
            duplicate_rows.append(
                {
                    "duplicate_group": group["group"],
                    "sha256": group["sha256"],
                    "size_bytes": group["size_bytes"],
                    "file_count": group["file_count"],
                    "reclaimable_bytes": group["reclaimable_bytes"],
                    "relative_path": member["relative_path"],
                    "status": member["status"],
                }
            )

    issue_rows = [
        {
            key: issue.get(key)
            for key in ("relative_path", "stage", "code", "message", "recorded_at")
        }
        for issue in issues
    ]
    for file_row in files:
        if file_row["status"] not in {"ok", "missing"}:
            issue_rows.append(
                {
                    "relative_path": file_row["relative_path"],
                    "stage": "file",
                    "code": file_row["error_code"] or file_row["status"],
                    "message": file_row["error_message"] or "",
                    "recorded_at": file_row["scanned_at"],
                }
            )

    catalog_csv = output / "catalog.csv"
    duplicates_csv = output / "exact-duplicates.csv"
    issues_csv = output / "issues.csv"
    catalog_json = output / "catalog.json"
    _write_csv(
        catalog_csv,
        CATALOG_COLUMNS + ("duplicate_group", "duplicate_count"),
        catalog_rows,
    )
    _write_csv(
        duplicates_csv,
        (
            "duplicate_group",
            "sha256",
            "size_bytes",
            "file_count",
            "reclaimable_bytes",
            "relative_path",
            "status",
        ),
        duplicate_rows,
    )
    _write_csv(
        issues_csv,
        ("relative_path", "stage", "code", "message", "recorded_at"),
        issue_rows,
    )
    _write_json(
        catalog_json,
        {
            "schema_version": "1.0.0",
            "generated_at": utc_now(),
            "tool": {
                "name": "Massive Magnetics Catalog Recovery",
                "version": __version__,
            },
            "scan": exported_scan,
            "summary": {
                "catalog_rows": len(files),
                "exact_duplicate_groups": len(duplicates),
                "exact_duplicate_files": sum(
                    group["file_count"] for group in duplicates
                ),
                "potential_reclaimable_bytes": sum(
                    group["reclaimable_bytes"] for group in duplicates
                ),
                "issues": len(issue_rows),
            },
            "files": catalog_rows,
            "exact_duplicate_groups": duplicates,
            "issues": issue_rows,
            "limitations": [
                "Exact duplicates require byte-for-byte identity and share one SHA-256 hash.",
                "Similar-sounding or transcoded audio is not classified as an exact duplicate.",
                "Source audio is inventoried read-only; no deletion, rename, retag, or move is performed.",
            ],
        },
    )

    report_paths = [catalog_csv, duplicates_csv, issues_csv, catalog_json]
    receipt = output / "scan-receipt.json"
    _write_json(
        receipt,
        {
            "schema_version": "1.0.0",
            "generated_at": utc_now(),
            "tool_version": __version__,
            "scan_id": scan_id,
            "scan_status": scan["status"],
            "source_root": source_label or scan["root_path"],
            "operation_policy": {
                "source_open_mode": "read-only binary",
                "source_mutations": "none",
                "symlinks_followed": False,
            },
            "reports": {
                path.name: {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
                for path in report_paths
            },
        },
    )
    return {
        "catalog-csv": catalog_csv,
        "exact-duplicates-csv": duplicates_csv,
        "issues-csv": issues_csv,
        "catalog-json": catalog_json,
        "scan-receipt": receipt,
    }


def verify_receipt(receipt_path: Path) -> tuple[bool, list[str]]:
    path = Path(receipt_path).expanduser().resolve(strict=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for name, expected in payload.get("reports", {}).items():
        report = path.parent / name
        if not report.is_file():
            errors.append(f"missing report: {name}")
            continue
        if report.stat().st_size != expected.get("size_bytes"):
            errors.append(f"size mismatch: {name}")
        if _sha256(report) != expected.get("sha256"):
            errors.append(f"SHA-256 mismatch: {name}")
    return not errors, errors
