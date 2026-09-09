"""Simple desktop interface for nontechnical catalog owners."""

from __future__ import annotations

import os
import queue
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .config import APP_NAME, default_database_path, default_report_base
from .database import CatalogDatabase
from .logging_config import configure_logging
from .reports import UnsafeReportLocation, export_reports
from .scanner import CatalogScanner, ScanProgress


class CatalogRecoveryApp:
    def __init__(self, window: tk.Tk):
        self.window = window
        self.window.title(APP_NAME)
        self.window.geometry("1120x720")
        self.window.minsize(820, 560)
        self.database_path = default_database_path()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.scan_id: int | None = None
        self.report_directory: Path | None = None

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(default_report_base()))
        self.status_var = tk.StringVar(
            value="Choose the folder that contains your audio."
        )
        self.summary_var = tk.StringVar(
            value="Nothing has been scanned in this session."
        )
        self.search_var = tk.StringVar()

        self._build()
        self._restore_latest_catalog()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.after(100, self._drain_events)

    def _restore_latest_catalog(self) -> None:
        if not self.database_path.is_file():
            return
        try:
            with CatalogDatabase(self.database_path) as database:
                scan_id = database.latest_scan_id()
                if scan_id is None:
                    return
                scan = database.get_scan(scan_id)
            self.scan_id = scan_id
            self.source_var.set(scan["root_path"])
            self.status_var.set(
                "Previous catalog loaded. Search it or start to refresh/resume."
            )
            self.summary_var.set(
                f"Scan {scan_id} · {scan['status']} · "
                f"{scan['files_processed']} processed file(s)"
            )
            self._load_catalog()
        except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error):
            self.status_var.set(
                "Existing catalog state could not be opened. Choose a folder to scan."
            )

    def _build(self) -> None:
        outer = ttk.Frame(self.window, padding=18)
        outer.pack(fill="both", expand=True)

        heading = ttk.Label(
            outer, text="Recover your audio catalog", font=("TkDefaultFont", 19, "bold")
        )
        heading.pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Find every supported audio file, prove exact duplicates with SHA-256, "
                "and export a searchable catalog. Originals are never renamed, deleted, retagged, or moved."
            ),
            wraplength=1040,
        ).pack(anchor="w", pady=(4, 14))

        chooser = ttk.LabelFrame(outer, text="1. Choose folders", padding=12)
        chooser.pack(fill="x")
        chooser.columnconfigure(1, weight=1)
        ttk.Label(chooser, text="Audio folder").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Entry(chooser, textvariable=self.source_var).grid(
            row=0, column=1, sticky="ew", pady=5
        )
        ttk.Button(chooser, text="Browse…", command=self._choose_source).grid(
            row=0, column=2, padx=(8, 0), pady=5
        )
        ttk.Label(chooser, text="Report folder").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Entry(chooser, textvariable=self.output_var).grid(
            row=1, column=1, sticky="ew", pady=5
        )
        ttk.Button(chooser, text="Browse…", command=self._choose_output).grid(
            row=1, column=2, padx=(8, 0), pady=5
        )

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=12)
        self.start_button = ttk.Button(
            controls, text="Start or resume read-only scan", command=self._start
        )
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(
            controls, text="Pause", command=self._cancel, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(
            controls, text="Open reports", command=self._open_reports, state="disabled"
        )
        self.open_button.pack(side="left")
        self.progress = ttk.Progressbar(controls, mode="determinate", maximum=100)
        self.progress.pack(side="right", fill="x", expand=True, padx=(18, 0))

        ttk.Label(outer, textvariable=self.status_var).pack(anchor="w")
        ttk.Label(outer, textvariable=self.summary_var, foreground="#555555").pack(
            anchor="w", pady=(1, 10)
        )

        search = ttk.Frame(outer)
        search.pack(fill="x", pady=(3, 8))
        ttk.Label(search, text="Search catalog").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda _event: self._load_catalog())
        ttk.Button(search, text="Search", command=self._load_catalog).pack(side="left")

        columns = (
            "path",
            "title",
            "artist",
            "album",
            "duration",
            "status",
            "duplicate",
        )
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings")
        labels = {
            "path": "File",
            "title": "Title",
            "artist": "Artist",
            "album": "Album",
            "duration": "Length",
            "status": "Status",
            "duplicate": "Exact duplicate",
        }
        widths = {
            "path": 320,
            "title": 155,
            "artist": 135,
            "album": 135,
            "duration": 75,
            "status": 105,
            "duplicate": 105,
        }
        for column in columns:
            self.table.heading(column, text=labels[column])
            self.table.column(column, width=widths[column], minwidth=65, anchor="w")
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.table.yview
        )
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(
            outer,
            text="Privacy: analysis runs on this computer. Audio is not uploaded. Reports contain file paths and metadata—share them deliberately.",
            wraplength=1040,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))

    def _choose_source(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose the folder containing your audio"
        )
        if selected:
            self.source_var.set(selected)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose a separate folder for reports")
        if selected:
            self.output_var.set(selected)

    @staticmethod
    def _inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _start(self) -> None:
        try:
            source = Path(self.source_var.get()).expanduser().resolve(strict=True)
            output_base = Path(self.output_var.get()).expanduser().resolve()
            if not source.is_dir():
                raise NotADirectoryError("The audio folder is not a directory.")
            if self._inside(output_base, source):
                raise UnsafeReportLocation(
                    "Choose a report folder outside the audio folder."
                )
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress.configure(value=0)
        self.status_var.set("Discovering audio files…")
        self.summary_var.set("Source files remain read-only during the scan.")
        self.worker = threading.Thread(
            target=self._scan_worker,
            args=(source, output_base),
            daemon=True,
        )
        self.worker.start()

    def _scan_worker(self, source: Path, output_base: Path) -> None:
        try:
            scanner = CatalogScanner(self.database_path)
            result = scanner.scan(
                source,
                resume=True,
                cancel_check=self.cancel_event.is_set,
                progress_callback=lambda event: self.events.put(("progress", event)),
            )
            reports: dict[str, Path] = {}
            report_directory: Path | None = None
            if result.status == "completed":
                report_directory = (
                    output_base / f"catalog-recovery-scan-{result.scan_id:06d}"
                )
                reports = export_reports(
                    self.database_path, result.scan_id, report_directory
                )
            self.events.put(("done", (result, reports, report_directory)))
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the UI
            self.events.put(("error", exc))

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Pausing safely after the current read block…")
        self.cancel_button.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._show_progress(payload)
                elif kind == "done":
                    self._show_done(*payload)
                elif kind == "error":
                    self._show_error(payload)
        except queue.Empty:
            pass
        self.window.after(100, self._drain_events)

    def _show_progress(self, event: ScanProgress) -> None:
        self.scan_id = event.scan_id
        if event.stage == "discovering":
            self.status_var.set("Discovering supported audio files…")
            return
        if event.discovered:
            self.progress.configure(value=(event.processed / event.discovered) * 100)
        self.status_var.set(
            f"{event.stage.capitalize()}: {event.processed} of {event.discovered} audio files"
        )
        self.summary_var.set(
            f"Reused {event.reused} unchanged result(s) · {event.errors} issue(s) · "
            f"{event.bytes_hashed / (1024 * 1024):,.1f} MiB hashed this run"
        )

    def _show_done(
        self, result: Any, reports: dict[str, Path], report_directory: Path | None
    ) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.scan_id = result.scan_id
        self.report_directory = report_directory
        if result.status == "completed":
            self.progress.configure(value=100)
            self.status_var.set("Scan complete. Reports are ready.")
            self.summary_var.set(
                f"{result.files_processed} audio file(s) · {result.files_with_errors} issue(s) · "
                f"{result.files_reused} result(s) resumed without rehashing"
            )
            self.open_button.configure(state="normal")
            self._load_catalog()
            messagebox.showinfo(
                APP_NAME,
                f"Scan complete. {len(reports)} verified report files were written to:\n{report_directory}",
            )
        else:
            self.status_var.set("Scan paused. Start again to resume it.")
            self.summary_var.set(
                f"Saved progress after {result.files_processed} of {result.files_discovered} audio files."
            )

    def _show_error(self, exc: BaseException) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_var.set(
            "The scan stopped safely. Existing progress can be resumed."
        )
        messagebox.showerror(APP_NAME, f"Catalog recovery could not continue:\n\n{exc}")

    def _load_catalog(self) -> None:
        if self.scan_id is None or not self.database_path.is_file():
            return
        query = self.search_var.get().strip()
        with CatalogDatabase(self.database_path) as database:
            rows = database.search(self.scan_id, query, limit=500)
            duplicate_hashes = {
                group["sha256"] for group in database.duplicate_groups(self.scan_id)
            }
        self.table.delete(*self.table.get_children())
        for row in rows:
            duration = ""
            if row["duration_seconds"] is not None:
                seconds = max(0, round(row["duration_seconds"]))
                duration = f"{seconds // 60}:{seconds % 60:02d}"
            self.table.insert(
                "",
                "end",
                values=(
                    row["relative_path"],
                    row["title"],
                    row["artist"],
                    row["album"],
                    duration,
                    row["status"],
                    "Yes" if row["sha256"] in duplicate_hashes else "",
                ),
            )

    def _open_reports(self) -> None:
        if self.report_directory is None:
            return
        if sys.platform == "win32":
            os.startfile(self.report_directory)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.report_directory)])
        else:
            subprocess.Popen(["xdg-open", str(self.report_directory)])

    def _close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
        self.window.destroy()


def run_gui() -> None:
    configure_logging()
    window = tk.Tk()
    CatalogRecoveryApp(window)
    window.mainloop()


if __name__ == "__main__":
    run_gui()
