# Massive Magnetics Catalog Recovery

Catalog Recovery is a local-first desktop and command-line tool for creators whose audio files have outgrown memory, scattered across project folders, exports, backups, and version names.

It recursively inventories supported audio, calculates a SHA-256 hash for byte-for-byte duplicate proof, extracts embedded metadata, stores a searchable SQLite catalog, and exports CSV/JSON reports. Source audio is opened read-only. The application does not delete, rename, move, retag, or otherwise modify originals.

## What version 0.1 delivers

- Recursive inventory for WAV, MP3, FLAC, M4A/MP4, AAC, Ogg/Opus, AIFF, ALAC, and WMA extensions.
- Exact duplicate groups backed by cryptographic SHA-256 hashes.
- Embedded title, artist, album, date, genre, rights, ISRC, and technical audio fields where the container supplies them.
- Search across paths, titles, artists, albums, genres, composers, ISRCs, and hashes.
- SQLite, CSV, and JSON output plus a checksum receipt for the reports.
- Resumable scans. Unchanged completed files are reused after a pause or interruption.
- Explicit rows for corrupt, unreadable, unstable, missing, or metadata-incompatible files.
- A folder-picker desktop interface and a scriptable CLI.

Exact duplicate means byte-for-byte identical. A WAV and an MP3 of the same performance, two differently tagged files, or two similar mixes will not share an exact hash. Version 0.1 does not make deletion decisions, identify rights owners, repair metadata, fingerprint similar audio, or upload audio to any service.

## Run the desktop app

Python 3.11 or newer is required for a source installation.

```bash
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -e .
# macOS/Linux:
.venv/bin/python -m pip install -e .
.venv/bin/python -m catalog_recovery
```

Choose the source audio folder and a separate report folder, then select **Start or resume read-only scan**. Reports are deliberately refused when the selected report folder is inside the source catalog.

Unsigned desktop bundles for Windows, macOS, and Linux are produced by the repository's release workflow. Because the preview executables are not code-signed, the operating system may require the owner to review and explicitly allow them. Each release includes `SHA256SUMS.txt` for download verification.

## Command-line use

```bash
catalog-recovery scan /path/to/audio --output /separate/path/to/reports
catalog-recovery search "unfinished hook" --database /path/to/catalog.sqlite3
catalog-recovery duplicates --database /path/to/catalog.sqlite3
catalog-recovery verify-receipt /path/to/reports/scan-receipt.json
```

The default database and rotating log live in the operating system's per-user application-data area. Logs can contain local file paths and should be treated as private catalog data.

## Recovery behavior

Every candidate is stat-checked before and after hashing and metadata extraction. If it changes, the tool retries once and then records `changed_during_scan` without accepting the stale hash. Symlinks are skipped so a selected tree cannot silently escape into another catalog. Work is committed in bounded batches; a cancelled or interrupted scan can resume from the same scan record.

Reports include:

- `catalog.csv` — one searchable row per discovered audio file.
- `exact-duplicates.csv` — each member of each byte-identical group.
- `issues.csv` — corrupt files and bounded discovery/read problems.
- `catalog.json` — the full machine-readable scan, duplicate groups, and limitations.
- `scan-receipt.json` — SHA-256 and byte length for each report.

## Safety and failure boundaries

- The scanner opens audio in read-only binary mode and never exposes a cleanup or mutation command.
- A file must have the same size, modification time, change time, device, and inode before and after inspection before its hash is accepted.
- Symlinks are reported and skipped. Unreadable directories and malformed metadata become bounded issue records instead of aborting the catalog.
- CSV cells that spreadsheet programs could interpret as formulas are prefixed defensively; the JSON catalog preserves the original text.
- Embedded tag count, value count, and value length are bounded to keep malformed metadata from expanding reports without limit.
- SQLite commits bounded batches in full-synchronous WAL mode. A process exit can require rehashing the last uncommitted batch but preserves earlier progress.
- Filenames, paths, tags, hashes, and logs can reveal private project information. Keep catalog outputs private unless intentionally sharing them.

## Test

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

The test suite covers exact duplicate detection, corrupted audio, tag extraction, search, report integrity, interrupted-scan recovery, hash binding when a file changes mid-scan, and a before/after proof that the scanner leaves source files unchanged.

## Product boundary

This repository also contains the earlier Truth Compiler evidence core. Catalog Recovery reuses its evidence-first, fail-closed direction but remains a small independent vertical. It does not depend on the unfinished OMNI acoustic analyzer or the SUNOKILLER synthesis stack.
