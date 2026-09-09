"""PyInstaller-friendly GUI entry point."""

from __future__ import annotations

import sys

from catalog_recovery.gui import run_gui


def self_test() -> int:
    """Exercise the bundled Python, SQLite, Mutagen, and Tcl runtimes."""
    import sqlite3
    import tkinter as tk

    import mutagen

    interpreter = tk.Tcl()
    assert interpreter.call("info", "patchlevel")
    assert sqlite3.sqlite_version
    assert mutagen.version
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    run_gui()
