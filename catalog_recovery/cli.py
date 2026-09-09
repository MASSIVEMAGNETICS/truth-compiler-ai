"""Command-line interface for repeatable and support-friendly scans."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import default_database_path, default_report_base
from .database import CatalogDatabase
from .logging_config import configure_logging
from .reports import export_reports, verify_receipt
from .scanner import CatalogScanner, ScanProgress

LOGGER = logging.getLogger(__name__)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catalog-recovery",
        description="Inventory audio, identify exact duplicates, and export a searchable catalog.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="write detailed diagnostic logs"
    )
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="scan an audio folder read-only")
    scan.add_argument("root", type=_path, help="folder containing the source audio")
    scan.add_argument("--database", type=_path, default=default_database_path())
    scan.add_argument(
        "--output", type=_path, help="report folder (must be outside source root)"
    )
    scan.add_argument(
        "--fresh", action="store_true", help="start a new scan instead of resuming"
    )
    scan.add_argument(
        "--no-export", action="store_true", help="retain only the SQLite catalog"
    )

    search = subparsers.add_parser(
        "search", help="search a completed or partial catalog"
    )
    search.add_argument(
        "query", help="text to match in path, title, artist, album, or hash"
    )
    search.add_argument("--database", type=_path, default=default_database_path())
    search.add_argument("--scan-id", type=int)
    search.add_argument("--limit", type=int, default=100)

    duplicates = subparsers.add_parser("duplicates", help="list exact duplicate groups")
    duplicates.add_argument("--database", type=_path, default=default_database_path())
    duplicates.add_argument("--scan-id", type=int)

    verify = subparsers.add_parser(
        "verify-receipt", help="verify exported report checksums"
    )
    verify.add_argument("receipt", type=_path)
    return parser


def _progress(event: ScanProgress) -> None:
    if event.stage == "scanning":
        print(
            f"\rScanned {event.processed}/{event.discovered} audio files "
            f"({event.errors} issue(s))",
            end="",
            file=sys.stderr,
            flush=True,
        )
    elif event.stage in {"completed", "cancelled"}:
        print(file=sys.stderr)


def _scan(args: argparse.Namespace) -> int:
    scanner = CatalogScanner(args.database)
    result = scanner.scan(
        args.root,
        resume=not args.fresh,
        progress_callback=_progress,
    )
    report_paths: dict[str, str] = {}
    if result.status == "completed" and not args.no_export:
        output = args.output or (default_report_base() / f"scan-{result.scan_id:06d}")
        report_paths = {
            name: str(path)
            for name, path in export_reports(
                args.database, result.scan_id, output
            ).items()
        }
    payload = {
        "scan_id": result.scan_id,
        "status": result.status,
        "resumed": result.resumed,
        "root_path": str(result.root_path),
        "database_path": str(result.database_path),
        "files_discovered": result.files_discovered,
        "files_processed": result.files_processed,
        "files_reused": result.files_reused,
        "files_with_errors": result.files_with_errors,
        "bytes_hashed": result.bytes_hashed,
        "reports": report_paths,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.status == "completed" else 2


def _resolve_scan_id(database: CatalogDatabase, requested: int | None) -> int:
    scan_id = requested if requested is not None else database.latest_scan_id()
    if scan_id is None:
        raise ValueError("the catalog database does not contain a scan")
    return scan_id


def _open_existing_database(path: Path) -> CatalogDatabase:
    expanded = path.expanduser().resolve()
    if not expanded.is_file():
        raise FileNotFoundError(f"catalog database not found: {expanded}")
    return CatalogDatabase(expanded)


def _search(args: argparse.Namespace) -> int:
    if args.limit < 1 or args.limit > 10_000:
        raise ValueError("--limit must be between 1 and 10000")
    with _open_existing_database(args.database) as database:
        scan_id = _resolve_scan_id(database, args.scan_id)
        rows = database.search(scan_id, args.query, limit=args.limit)
    print(
        json.dumps({"scan_id": scan_id, "results": rows}, indent=2, ensure_ascii=False)
    )
    return 0


def _duplicates(args: argparse.Namespace) -> int:
    with _open_existing_database(args.database) as database:
        scan_id = _resolve_scan_id(database, args.scan_id)
        groups = database.duplicate_groups(scan_id)
    print(json.dumps({"scan_id": scan_id, "exact_duplicate_groups": groups}, indent=2))
    return 0


def _verify(args: argparse.Namespace) -> int:
    valid, errors = verify_receipt(args.receipt)
    print(json.dumps({"valid": valid, "errors": errors}, indent=2))
    return 0 if valid else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    if args.command is None:
        from .gui import run_gui

        run_gui()
        return 0
    handlers = {
        "scan": _scan,
        "search": _search,
        "duplicates": _duplicates,
        "verify-receipt": _verify,
    }
    try:
        return handlers[args.command](args)
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.exception("Catalog recovery command failed")
        print(f"Catalog recovery could not continue: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
