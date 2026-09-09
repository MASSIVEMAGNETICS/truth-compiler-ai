#!/usr/bin/env python3
"""Generate and scan a synthetic, rights-safe audio catalog for product verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import tempfile
import wave
from pathlib import Path

from mutagen.id3 import TALB, TDRC, TIT2, TPE1
from mutagen.wave import WAVE

from catalog_recovery.database import CatalogDatabase
from catalog_recovery.reports import export_reports, verify_receipt
from catalog_recovery.scanner import CatalogScanner, utc_now


def write_wave(path: Path, amplitude: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = b"".join(
        struct.pack("<h", amplitude if index % 2 else -amplitude)
        for index in range(8_000)
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(samples)


def add_tags(path: Path) -> None:
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=["North Coast Signal"]))
    audio.tags.add(TPE1(encoding=3, text=["Synthetic Demo Creator"]))
    audio.tags.add(TALB(encoding=3, text=["Recovered Sessions"]))
    audio.tags.add(TDRC(encoding=3, text=["2026"]))
    audio.save()


def manifest(root: Path) -> dict[str, dict[str, int | str]]:
    rows: dict[str, dict[str, int | str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        value = path.stat()
        rows[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": value.st_size,
            "mtime_ns": value.st_mtime_ns,
        }
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "examples" / "sample-report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="catalog-recovery-sample-") as temporary:
        workspace = Path(temporary)
        source = workspace / "authorized-synthetic-audio"
        source.mkdir()

        master = source / "Sessions" / "North Coast Signal - master.wav"
        write_wave(master, 900)
        add_tags(master)
        duplicate = source / "Backups" / "North Coast Signal - master COPY.wav"
        duplicate.parent.mkdir()
        shutil.copyfile(master, duplicate)
        write_wave(source / "Sessions" / "North Coast Signal - alt mix.wav", 450)
        (source / "Damaged Export.mp3").write_bytes(b"synthetic corrupt audio fixture")

        before = manifest(source)
        database_path = workspace / "state" / "catalog.sqlite3"
        result = CatalogScanner(database_path).scan(source)
        after = manifest(source)
        report_paths = export_reports(
            database_path,
            result.scan_id,
            output,
            source_label="AUTHORIZED_SYNTHETIC_SAMPLE",
        )
        receipt_valid, receipt_errors = verify_receipt(report_paths["scan-receipt"])
        with CatalogDatabase(database_path) as database:
            groups = database.duplicate_groups(result.scan_id)
            rows = database.list_files(result.scan_id)

        verification = {
            "schema_version": "1.0.0",
            "generated_at": utc_now(),
            "authorization": "Synthetic audio generated locally for this verification run.",
            "scan_id": result.scan_id,
            "scan_status": result.status,
            "source_file_count": len(before),
            "source_files_unchanged": before == after,
            "source_manifest_before": before,
            "source_manifest_after": after,
            "catalog_rows": len(rows),
            "files_with_errors": result.files_with_errors,
            "exact_duplicate_groups": len(groups),
            "exact_duplicate_files": sum(group["file_count"] for group in groups),
            "potential_reclaimable_bytes": sum(
                group["reclaimable_bytes"] for group in groups
            ),
            "report_receipt_valid": receipt_valid,
            "report_receipt_errors": receipt_errors,
        }
        verification_path = output / "sample-verification.json"
        verification_path.write_text(
            json.dumps(verification, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(verification, indent=2, sort_keys=True))
        return (
            0
            if result.status == "completed" and before == after and receipt_valid
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
