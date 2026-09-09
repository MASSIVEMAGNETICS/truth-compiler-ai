"""Read-only audio inventory and exact duplicate scanner."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import SUPPORTED_AUDIO_EXTENSIONS
from .database import CatalogDatabase
from .metadata import AudioMetadata, MetadataDependencyError, extract_metadata

LOGGER = logging.getLogger(__name__)
HASH_CHUNK_BYTES = 1024 * 1024


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bounded_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split()) or type(exc).__name__
    return message[:500]


@dataclass(frozen=True)
class StatSignature:
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int

    @classmethod
    def from_path(cls, path: Path) -> StatSignature:
        value = path.lstat()
        if not stat.S_ISREG(value.st_mode):
            raise OSError("path is not a regular file")
        return cls(
            size_bytes=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
            device=value.st_dev,
            inode=value.st_ino,
        )

    def matches_row(self, row: object) -> bool:
        return all(
            getattr(self, name) == row[name]  # type: ignore[index]
            for name in ("size_bytes", "mtime_ns", "ctime_ns", "device", "inode")
        )


@dataclass(frozen=True)
class DiscoveryIssue:
    relative_path: str
    code: str
    message: str


@dataclass(frozen=True)
class ScanProgress:
    stage: str
    scan_id: int
    discovered: int
    processed: int
    reused: int
    errors: int
    bytes_hashed: int
    current_path: str = ""


@dataclass(frozen=True)
class ScanResult:
    scan_id: int
    status: str
    resumed: bool
    root_path: Path
    database_path: Path
    files_discovered: int
    files_processed: int
    files_reused: int
    files_with_errors: int
    bytes_hashed: int


class ScanCancelled(RuntimeError):
    pass


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def discover_audio_files(
    root: Path,
    extensions: frozenset[str] = SUPPORTED_AUDIO_EXTENSIONS,
) -> tuple[list[Path], list[DiscoveryIssue]]:
    """Discover audio paths without following file or directory symlinks."""
    paths: list[Path] = []
    issues: list[DiscoveryIssue] = []
    stack = [root]

    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError as exc:
            issues.append(
                DiscoveryIssue(
                    _safe_relative(directory, root),
                    "directory_unreadable",
                    _bounded_error(exc),
                )
            )
            continue

        child_directories: list[Path] = []
        for entry in entries:
            child = Path(entry.path)
            try:
                if entry.is_symlink():
                    issues.append(
                        DiscoveryIssue(
                            _safe_relative(child, root),
                            "symlink_skipped",
                            "Symbolic links are not followed during read-only scans.",
                        )
                    )
                elif entry.is_dir(follow_symlinks=False):
                    child_directories.append(child)
                elif (
                    entry.is_file(follow_symlinks=False)
                    and child.suffix.casefold() in extensions
                ):
                    paths.append(child)
            except OSError as exc:
                issues.append(
                    DiscoveryIssue(
                        _safe_relative(child, root),
                        "entry_unreadable",
                        _bounded_error(exc),
                    )
                )
        stack.extend(reversed(child_directories))

    paths.sort(key=lambda path: _safe_relative(path, root).casefold())
    return paths, issues


def hash_file(path: Path, cancel_check: Callable[[], bool]) -> tuple[str, int]:
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as source:
        while True:
            if cancel_check():
                raise ScanCancelled("scan cancelled by user")
            chunk = source.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            bytes_read += len(chunk)
    return digest.hexdigest(), bytes_read


class CatalogScanner:
    """Inventory a folder into SQLite while treating all source audio as immutable."""

    def __init__(
        self,
        database_path: Path,
        *,
        metadata_reader: Callable[[Path], AudioMetadata] = extract_metadata,
        extensions: frozenset[str] = SUPPORTED_AUDIO_EXTENSIONS,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.metadata_reader = metadata_reader
        self.extensions = extensions

    def scan(
        self,
        root_path: Path,
        *,
        resume: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[ScanProgress], None] | None = None,
    ) -> ScanResult:
        cancel = cancel_check or (lambda: False)
        progress = progress_callback or (lambda _: None)
        root = Path(root_path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(f"scan root is not a directory: {root}")
        root_key = os.path.normcase(str(root))
        seen_token = uuid.uuid4().hex

        with CatalogDatabase(self.database_path) as database:
            scan_id, resumed = database.begin_scan(
                root, root_key, utc_now(), resume=resume
            )
            counters = {
                "discovered": 0,
                "processed": 0,
                "reused": 0,
                "errors": 0,
                "bytes_hashed": 0,
            }
            try:
                progress(ScanProgress("discovering", scan_id, 0, 0, 0, 0, 0))
                paths, discovery_issues = discover_audio_files(root, self.extensions)
                counters["discovered"] = len(paths)
                for issue in discovery_issues:
                    database.record_issue(
                        scan_id,
                        issue.relative_path,
                        "discovery",
                        issue.code,
                        issue.message,
                        utc_now(),
                    )
                    counters["errors"] += 1
                self._persist_progress(database, scan_id, counters, None)
                database.commit()

                for path in paths:
                    if cancel():
                        raise ScanCancelled("scan cancelled by user")
                    relative_path = _safe_relative(path, root)
                    current_signature = self._signature_or_none(path)
                    existing = database.existing_file(scan_id, relative_path)
                    if (
                        current_signature is not None
                        and existing is not None
                        and existing["sha256"]
                        and existing["status"] != "missing"
                        and current_signature.matches_row(existing)
                    ):
                        database.mark_seen(int(existing["id"]), seen_token)
                        counters["processed"] += 1
                        counters["reused"] += 1
                        if existing["status"] != "ok":
                            counters["errors"] += 1
                    else:
                        row, bytes_hashed = self._inspect_path(
                            scan_id,
                            path,
                            relative_path,
                            seen_token,
                            cancel,
                        )
                        database.record_file(row)
                        counters["processed"] += 1
                        counters["bytes_hashed"] += bytes_hashed
                        if row["status"] != "ok":
                            counters["errors"] += 1

                    self._persist_progress(database, scan_id, counters, relative_path)
                    if counters["processed"] % 25 == 0:
                        database.commit()
                    progress(
                        ScanProgress(
                            "scanning",
                            scan_id,
                            counters["discovered"],
                            counters["processed"],
                            counters["reused"],
                            counters["errors"],
                            counters["bytes_hashed"],
                            relative_path,
                        )
                    )

                self._persist_progress(database, scan_id, counters, None)
                database.finish_scan(
                    scan_id, "completed", utc_now(), seen_token=seen_token
                )
                status = "completed"
            except ScanCancelled:
                self._persist_progress(database, scan_id, counters, None)
                database.finish_scan(
                    scan_id, "cancelled", utc_now(), seen_token=seen_token
                )
                status = "cancelled"
            except BaseException as exc:
                LOGGER.exception("Catalog scan %s was interrupted", scan_id)
                self._persist_progress(database, scan_id, counters, None)
                database.finish_scan(
                    scan_id,
                    "interrupted",
                    utc_now(),
                    seen_token=seen_token,
                    error_message=_bounded_error(exc),
                )
                raise

        result = ScanResult(
            scan_id=scan_id,
            status=status,
            resumed=resumed,
            root_path=root,
            database_path=self.database_path,
            files_discovered=counters["discovered"],
            files_processed=counters["processed"],
            files_reused=counters["reused"],
            files_with_errors=counters["errors"],
            bytes_hashed=counters["bytes_hashed"],
        )
        progress(
            ScanProgress(
                status,
                scan_id,
                counters["discovered"],
                counters["processed"],
                counters["reused"],
                counters["errors"],
                counters["bytes_hashed"],
            )
        )
        return result

    def _inspect_path(
        self,
        scan_id: int,
        path: Path,
        relative_path: str,
        seen_token: str,
        cancel: Callable[[], bool],
    ) -> tuple[dict[str, object], int]:
        total_bytes_hashed = 0
        for attempt in range(2):
            try:
                before = StatSignature.from_path(path)
                digest, bytes_hashed = hash_file(path, cancel)
                total_bytes_hashed += bytes_hashed
                metadata_error: str | None = None
                try:
                    metadata = self.metadata_reader(path)
                except MetadataDependencyError:
                    raise
                except Exception as exc:  # noqa: BLE001 - malformed third-party tags stay row-scoped
                    metadata = AudioMetadata()
                    metadata_error = _bounded_error(exc)
                after = StatSignature.from_path(path)
            except ScanCancelled:
                raise
            except MetadataDependencyError:
                raise
            except OSError as exc:
                return self._error_row(
                    scan_id,
                    path,
                    relative_path,
                    seen_token,
                    "read_error",
                    "source_unreadable",
                    _bounded_error(exc),
                ), total_bytes_hashed

            if before == after:
                payload = metadata.to_dict()
                return {
                    "scan_id": scan_id,
                    "relative_path": relative_path,
                    "file_name": path.name,
                    "extension": path.suffix.casefold(),
                    "size_bytes": after.size_bytes,
                    "mtime_ns": after.mtime_ns,
                    "ctime_ns": after.ctime_ns,
                    "device": after.device,
                    "inode": after.inode,
                    "sha256": digest,
                    "status": "metadata_error" if metadata_error else "ok",
                    **{
                        key: payload[key]
                        for key in (
                            "title",
                            "artist",
                            "album",
                            "album_artist",
                            "track_number",
                            "disc_number",
                            "date",
                            "genre",
                            "composer",
                            "comment",
                            "copyright",
                            "isrc",
                            "duration_seconds",
                            "bitrate",
                            "sample_rate",
                            "channels",
                            "bits_per_sample",
                            "container",
                        )
                    },
                    "metadata_json": CatalogDatabase.metadata_json(payload),
                    "error_code": "metadata_unreadable" if metadata_error else None,
                    "error_message": metadata_error,
                    "scanned_at": utc_now(),
                    "seen_token": seen_token,
                }, total_bytes_hashed
            LOGGER.warning(
                "Source changed while scanning; retry %s for %s",
                attempt + 1,
                relative_path,
            )

        return self._error_row(
            scan_id,
            path,
            relative_path,
            seen_token,
            "changed_during_scan",
            "unstable_source",
            "File changed while it was being hashed; no hash was accepted.",
        ), total_bytes_hashed

    @staticmethod
    def _signature_or_none(path: Path) -> StatSignature | None:
        try:
            return StatSignature.from_path(path)
        except OSError:
            return None

    @staticmethod
    def _error_row(
        scan_id: int,
        path: Path,
        relative_path: str,
        seen_token: str,
        status: str,
        error_code: str,
        error_message: str,
    ) -> dict[str, object]:
        signature = CatalogScanner._signature_or_none(path)
        metadata = AudioMetadata().to_dict()
        return {
            "scan_id": scan_id,
            "relative_path": relative_path,
            "file_name": path.name,
            "extension": path.suffix.casefold(),
            "size_bytes": signature.size_bytes if signature else None,
            "mtime_ns": signature.mtime_ns if signature else None,
            "ctime_ns": signature.ctime_ns if signature else None,
            "device": signature.device if signature else None,
            "inode": signature.inode if signature else None,
            "sha256": None,
            "status": status,
            **{
                key: metadata[key]
                for key in (
                    "title",
                    "artist",
                    "album",
                    "album_artist",
                    "track_number",
                    "disc_number",
                    "date",
                    "genre",
                    "composer",
                    "comment",
                    "copyright",
                    "isrc",
                    "duration_seconds",
                    "bitrate",
                    "sample_rate",
                    "channels",
                    "bits_per_sample",
                    "container",
                )
            },
            "metadata_json": CatalogDatabase.metadata_json(metadata),
            "error_code": error_code,
            "error_message": error_message,
            "scanned_at": utc_now(),
            "seen_token": seen_token,
        }

    @staticmethod
    def _persist_progress(
        database: CatalogDatabase,
        scan_id: int,
        counters: dict[str, int],
        current_path: str | None,
    ) -> None:
        database.update_progress(
            scan_id,
            now=utc_now(),
            discovered=counters["discovered"],
            processed=counters["processed"],
            reused=counters["reused"],
            errors=counters["errors"],
            bytes_hashed=counters["bytes_hashed"],
            current_path=current_path,
        )
