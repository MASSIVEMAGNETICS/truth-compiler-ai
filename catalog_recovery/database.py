"""SQLite persistence for resumable, searchable catalog scans."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from . import __version__

SCHEMA_VERSION = 1
ACTIVE_STATUSES = ("running", "cancelled", "interrupted")


class CatalogDatabase:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        self.connection = sqlite3.connect(self.path, timeout=30)
        if is_new:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self._initialize()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.close()

    def close(self) -> None:
        self.connection.close()

    def commit(self) -> None:
        self.connection.commit()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL,
                root_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('running', 'completed', 'cancelled', 'interrupted')
                ),
                tool_version TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                files_discovered INTEGER NOT NULL DEFAULT 0,
                files_processed INTEGER NOT NULL DEFAULT 0,
                files_reused INTEGER NOT NULL DEFAULT 0,
                files_with_errors INTEGER NOT NULL DEFAULT 0,
                bytes_hashed INTEGER NOT NULL DEFAULT 0,
                current_path TEXT,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scans_root
                ON scans(root_key, id DESC);

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                extension TEXT NOT NULL,
                size_bytes INTEGER,
                mtime_ns INTEGER,
                ctime_ns INTEGER,
                device INTEGER,
                inode INTEGER,
                sha256 TEXT,
                status TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                track_number TEXT NOT NULL DEFAULT '',
                disc_number TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL DEFAULT '',
                genre TEXT NOT NULL DEFAULT '',
                composer TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                copyright TEXT NOT NULL DEFAULT '',
                isrc TEXT NOT NULL DEFAULT '',
                duration_seconds REAL,
                bitrate INTEGER,
                sample_rate INTEGER,
                channels INTEGER,
                bits_per_sample INTEGER,
                container TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error_message TEXT,
                scanned_at TEXT NOT NULL,
                seen_token TEXT NOT NULL,
                UNIQUE(scan_id, relative_path)
            );
            CREATE INDEX IF NOT EXISTS idx_files_scan_path
                ON files(scan_id, relative_path);
            CREATE INDEX IF NOT EXISTS idx_files_scan_hash
                ON files(scan_id, sha256);
            CREATE INDEX IF NOT EXISTS idx_files_scan_artist
                ON files(scan_id, artist);
            CREATE INDEX IF NOT EXISTS idx_files_scan_album
                ON files(scan_id, album);
            CREATE INDEX IF NOT EXISTS idx_files_scan_title
                ON files(scan_id, title);

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                stage TEXT NOT NULL,
                code TEXT NOT NULL,
                message TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_issues_scan ON issues(scan_id, id);
            """
        )
        self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    def begin_scan(
        self,
        root_path: Path,
        root_key: str,
        now: str,
        *,
        resume: bool,
    ) -> tuple[int, bool]:
        if resume:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            row = self.connection.execute(
                f"""
                SELECT id FROM scans
                WHERE root_key = ? AND status IN ({placeholders})
                ORDER BY id DESC LIMIT 1
                """,
                (root_key, *ACTIVE_STATUSES),
            ).fetchone()
            if row:
                scan_id = int(row["id"])
                self.connection.execute(
                    """
                    UPDATE scans
                    SET status='running', updated_at=?, completed_at=NULL,
                        current_path=NULL, error_message=NULL,
                        files_discovered=0, files_processed=0,
                        files_reused=0, files_with_errors=0, bytes_hashed=0
                    WHERE id=?
                    """,
                    (now, scan_id),
                )
                self.connection.execute(
                    "DELETE FROM issues WHERE scan_id=?", (scan_id,)
                )
                self.connection.commit()
                return scan_id, True

        cursor = self.connection.execute(
            """
            INSERT INTO scans (
                root_path, root_key, status, tool_version, started_at, updated_at
            ) VALUES (?, ?, 'running', ?, ?, ?)
            """,
            (str(root_path), root_key, __version__, now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid), False

    def existing_file(self, scan_id: int, relative_path: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM files WHERE scan_id=? AND relative_path=?",
            (scan_id, relative_path),
        ).fetchone()

    def mark_seen(self, file_id: int, token: str) -> None:
        self.connection.execute(
            "UPDATE files SET seen_token=? WHERE id=?",
            (token, file_id),
        )

    def record_file(self, row: dict[str, Any]) -> None:
        columns = (
            "scan_id",
            "relative_path",
            "file_name",
            "extension",
            "size_bytes",
            "mtime_ns",
            "ctime_ns",
            "device",
            "inode",
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
            "comment",
            "copyright",
            "isrc",
            "duration_seconds",
            "bitrate",
            "sample_rate",
            "channels",
            "bits_per_sample",
            "container",
            "metadata_json",
            "error_code",
            "error_message",
            "scanned_at",
            "seen_token",
        )
        values = [row.get(column) for column in columns]
        assignments = ", ".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column not in {"scan_id", "relative_path"}
        )
        self.connection.execute(
            f"""
            INSERT INTO files ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            ON CONFLICT(scan_id, relative_path) DO UPDATE SET {assignments}
            """,
            values,
        )

    def record_issue(
        self,
        scan_id: int,
        relative_path: str,
        stage: str,
        code: str,
        message: str,
        now: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO issues (scan_id, relative_path, stage, code, message, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scan_id, relative_path, stage, code, message, now),
        )

    def update_progress(
        self,
        scan_id: int,
        *,
        now: str,
        discovered: int,
        processed: int,
        reused: int,
        errors: int,
        bytes_hashed: int,
        current_path: str | None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE scans SET
                updated_at=?, files_discovered=?, files_processed=?,
                files_reused=?, files_with_errors=?, bytes_hashed=?, current_path=?
            WHERE id=?
            """,
            (
                now,
                discovered,
                processed,
                reused,
                errors,
                bytes_hashed,
                current_path,
                scan_id,
            ),
        )

    def finish_scan(
        self,
        scan_id: int,
        status: str,
        now: str,
        *,
        seen_token: str,
        error_message: str | None = None,
    ) -> None:
        if status == "completed":
            self.connection.execute(
                """
                UPDATE files
                SET status='missing', error_code='missing_since_resume',
                    error_message='File was not present when the resumed scan completed.'
                WHERE scan_id=? AND seen_token<>?
                """,
                (scan_id, seen_token),
            )
        self.connection.execute(
            """
            UPDATE scans SET status=?, updated_at=?, completed_at=?,
                current_path=NULL, error_message=? WHERE id=?
            """,
            (
                status,
                now,
                now if status in {"completed", "cancelled"} else None,
                error_message,
                scan_id,
            ),
        )
        self.connection.commit()

    def get_scan(self, scan_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM scans WHERE id=?", (scan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"scan {scan_id} does not exist")
        return dict(row)

    def latest_scan_id(self) -> int | None:
        row = self.connection.execute(
            "SELECT id FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None

    def list_files(self, scan_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM files WHERE scan_id=?
            ORDER BY relative_path COLLATE NOCASE
            """,
            (scan_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_issues(self, scan_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM issues WHERE scan_id=? ORDER BY relative_path, id",
            (scan_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def duplicate_groups(self, scan_id: int) -> list[dict[str, Any]]:
        hashes = self.connection.execute(
            """
            SELECT sha256, size_bytes, COUNT(*) AS file_count
            FROM files
            WHERE scan_id=? AND sha256 IS NOT NULL AND status<>'missing'
            GROUP BY sha256, size_bytes HAVING COUNT(*) > 1
            ORDER BY file_count DESC, sha256
            """,
            (scan_id,),
        ).fetchall()
        groups: list[dict[str, Any]] = []
        for row in hashes:
            members = self.connection.execute(
                """
                SELECT relative_path, status
                FROM files
                WHERE scan_id=? AND sha256=? AND status<>'missing'
                ORDER BY relative_path COLLATE NOCASE
                """,
                (scan_id, row["sha256"]),
            ).fetchall()
            size_bytes = int(row["size_bytes"] or 0)
            file_count = int(row["file_count"])
            groups.append(
                {
                    "group": f"exact-{str(row['sha256'])[:12]}",
                    "sha256": row["sha256"],
                    "size_bytes": size_bytes,
                    "file_count": file_count,
                    "reclaimable_bytes": size_bytes * (file_count - 1),
                    "files": [dict(member) for member in members],
                }
            )
        return groups

    def search(
        self, scan_id: int, query: str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 5000))
        terms = [term for term in query.casefold().split() if term]
        where = ["scan_id=?", "status<>'missing'"]
        parameters: list[Any] = [scan_id]
        searchable = (
            "LOWER(relative_path || ' ' || title || ' ' || artist || ' ' || album || ' ' || "
            "album_artist || ' ' || genre || ' ' || composer || ' ' || isrc || ' ' || "
            "COALESCE(sha256, ''))"
        )
        for term in terms:
            where.append(f"{searchable} LIKE ? ESCAPE '\\'")
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.append(f"%{escaped}%")
        parameters.append(bounded_limit)
        rows = self.connection.execute(
            f"""
            SELECT * FROM files WHERE {" AND ".join(where)}
            ORDER BY relative_path COLLATE NOCASE LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def metadata_json(metadata: dict[str, Any]) -> str:
        return json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def execute_many(self, sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
        self.connection.executemany(sql, rows)
