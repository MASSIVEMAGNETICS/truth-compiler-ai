import csv
import hashlib
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from mutagen.id3 import TALB, TCON, TCOP, TDRC, TIT2, TPE1, TSRC
from mutagen.wave import WAVE

from catalog_recovery.database import CatalogDatabase
from catalog_recovery.metadata import AudioMetadata
from catalog_recovery.reports import (
    UnsafeReportLocation,
    export_reports,
    verify_receipt,
)
from catalog_recovery.scanner import CatalogScanner


def write_wave(path: Path, amplitude: int = 500, frames: int = 800) -> None:
    samples = b"".join(
        struct.pack("<h", amplitude if index % 2 else -amplitude)
        for index in range(frames)
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(samples)


def add_tags(path: Path) -> None:
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=["Recovered Song"]))
    audio.tags.add(TPE1(encoding=3, text=["Test Creator"]))
    audio.tags.add(TALB(encoding=3, text=["Archive Box 7"]))
    audio.tags.add(TDRC(encoding=3, text=["2026"]))
    audio.tags.add(TCON(encoding=3, text=["Demo"]))
    audio.tags.add(TCOP(encoding=3, text=["Test Creator 2026"]))
    audio.tags.add(TSRC(encoding=3, text=["US-AAA-26-00001"]))
    audio.save()


def file_snapshot(root: Path) -> dict[str, tuple[str, int, int, int]]:
    snapshot = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        value = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            value.st_size,
            value.st_mtime_ns,
            value.st_mode,
        )
    return snapshot


class CatalogRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source-audio"
        self.workspace = self.base / "catalog-state"
        self.reports = self.base / "reports"
        self.source.mkdir()
        self.workspace.mkdir()
        self.database_path = self.workspace / "catalog.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_duplicate_detection_and_non_destructive_scan(self) -> None:
        original = self.source / "original.wav"
        duplicate = self.source / "backup" / "original-copy.wav"
        different = self.source / "different-master.wav"
        duplicate.parent.mkdir()
        write_wave(original, amplitude=300)
        shutil.copyfile(original, duplicate)
        write_wave(different, amplitude=700)
        before = file_snapshot(self.source)

        result = CatalogScanner(self.database_path).scan(self.source)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.files_discovered, 3)
        self.assertEqual(file_snapshot(self.source), before)
        with CatalogDatabase(self.database_path) as database:
            groups = database.duplicate_groups(result.scan_id)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["file_count"], 2)
        self.assertEqual(
            {row["relative_path"] for row in groups[0]["files"]},
            {"original.wav", "backup/original-copy.wav"},
        )
        self.assertEqual(groups[0]["sha256"], before["original.wav"][0])

    def test_corrupt_audio_is_recorded_without_aborting(self) -> None:
        write_wave(self.source / "healthy.wav")
        corrupt = self.source / "damaged.mp3"
        corrupt.write_bytes(b"not an audio file")
        before = file_snapshot(self.source)

        result = CatalogScanner(self.database_path).scan(self.source)

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.files_with_errors, 1)
        self.assertEqual(file_snapshot(self.source), before)
        with CatalogDatabase(self.database_path) as database:
            rows = {
                row["relative_path"]: row for row in database.list_files(result.scan_id)
            }
        self.assertEqual(rows["healthy.wav"]["status"], "ok")
        self.assertEqual(rows["damaged.mp3"]["status"], "metadata_error")
        self.assertEqual(rows["damaged.mp3"]["error_code"], "metadata_unreadable")
        self.assertEqual(len(rows["damaged.mp3"]["sha256"]), 64)

    def test_metadata_is_extracted_and_searchable(self) -> None:
        tagged = self.source / "unknown-filename.wav"
        write_wave(tagged)
        add_tags(tagged)

        result = CatalogScanner(self.database_path).scan(self.source)

        with CatalogDatabase(self.database_path) as database:
            rows = database.search(result.scan_id, "Archive Box Creator")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "Recovered Song")
        self.assertEqual(row["artist"], "Test Creator")
        self.assertEqual(row["album"], "Archive Box 7")
        self.assertEqual(row["copyright"], "Test Creator 2026")
        self.assertEqual(row["isrc"], "US-AAA-26-00001")
        self.assertEqual(row["sample_rate"], 8_000)
        self.assertEqual(row["channels"], 1)

    def test_cancelled_scan_resumes_and_reuses_completed_hashes(self) -> None:
        for index in range(4):
            write_wave(self.source / f"take-{index}.wav", amplitude=200 + index)
        should_cancel = {"value": False}

        def progress(event):
            if event.stage == "scanning" and event.processed == 1:
                should_cancel["value"] = True

        first = CatalogScanner(self.database_path).scan(
            self.source,
            cancel_check=lambda: should_cancel["value"],
            progress_callback=progress,
        )
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(first.files_processed, 1)

        second = CatalogScanner(self.database_path).scan(
            self.source, cancel_check=lambda: False
        )
        self.assertEqual(second.status, "completed")
        self.assertTrue(second.resumed)
        self.assertEqual(second.scan_id, first.scan_id)
        self.assertEqual(second.files_processed, 4)
        self.assertEqual(second.files_reused, 1)

    def test_file_changed_during_scan_never_receives_stale_hash(self) -> None:
        changing = self.source / "live-export.wav"
        write_wave(changing)

        def external_mutation(path: Path) -> AudioMetadata:
            with path.open("ab") as output:
                output.write(b"external-change")
            return AudioMetadata()

        result = CatalogScanner(
            self.database_path,
            metadata_reader=external_mutation,
        ).scan(self.source)

        with CatalogDatabase(self.database_path) as database:
            row = database.list_files(result.scan_id)[0]
        self.assertEqual(row["status"], "changed_during_scan")
        self.assertEqual(row["error_code"], "unstable_source")
        self.assertIsNone(row["sha256"])

    def test_exports_have_integrity_receipt_and_refuse_source_tree(self) -> None:
        source_file = self.source / "catalog.wav"
        write_wave(source_file)
        shutil.copyfile(source_file, self.source / "catalog-copy.wav")
        result = CatalogScanner(self.database_path).scan(self.source)

        paths = export_reports(self.database_path, result.scan_id, self.reports)

        self.assertEqual(
            {path.name for path in paths.values()},
            {
                "catalog.csv",
                "exact-duplicates.csv",
                "issues.csv",
                "catalog.json",
                "scan-receipt.json",
            },
        )
        valid, errors = verify_receipt(paths["scan-receipt"])
        self.assertTrue(valid, errors)
        catalog = json.loads(paths["catalog-json"].read_text(encoding="utf-8"))
        self.assertEqual(catalog["summary"]["catalog_rows"], 2)
        self.assertEqual(catalog["summary"]["exact_duplicate_groups"], 1)
        with self.assertRaises(UnsafeReportLocation):
            export_reports(self.database_path, result.scan_id, self.source / "reports")
        self.assertFalse((self.source / "reports").exists())

        with paths["catalog-json"].open("a", encoding="utf-8") as output:
            output.write("tamper")
        valid, errors = verify_receipt(paths["scan-receipt"])
        self.assertFalse(valid)
        self.assertTrue(any("catalog.json" in error for error in errors))

    def test_csv_neutralizes_spreadsheet_formulas_but_json_preserves_text(self) -> None:
        dangerous_name = "=HYPERLINK-example.wav"
        write_wave(self.source / dangerous_name)
        result = CatalogScanner(self.database_path).scan(self.source)

        paths = export_reports(self.database_path, result.scan_id, self.reports)

        with paths["catalog-csv"].open(encoding="utf-8", newline="") as source:
            csv_row = next(csv.DictReader(source))
        json_row = json.loads(paths["catalog-json"].read_text(encoding="utf-8"))[
            "files"
        ][0]
        self.assertEqual(csv_row["relative_path"], "'=HYPERLINK-example.wav")
        self.assertEqual(json_row["relative_path"], dangerous_name)


if __name__ == "__main__":
    unittest.main()
