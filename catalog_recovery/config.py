"""Application paths and supported formats."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Massive Magnetics Catalog Recovery"

SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aif",
        ".aiff",
        ".alac",
        ".flac",
        ".m4a",
        ".m4b",
        ".mp3",
        ".mp4",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".wave",
        ".wma",
    }
)


def default_data_dir() -> Path:
    """Return an owner-controlled per-user application data directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "massive-magnetics" / "catalog-recovery"


def default_database_path() -> Path:
    return default_data_dir() / "catalog.sqlite3"


def default_log_path() -> Path:
    return default_data_dir() / "catalog-recovery.log"


def default_report_base() -> Path:
    return default_data_dir() / "reports"
