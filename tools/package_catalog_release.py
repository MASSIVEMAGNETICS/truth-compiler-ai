#!/usr/bin/env python3
"""Create one stable-layout ZIP from a PyInstaller output directory."""

from __future__ import annotations

import argparse
import os
import stat
import zipfile
from pathlib import Path

DEFAULT_README = Path(__file__).parents[1] / "catalog_recovery" / "DESKTOP_README.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    return parser.parse_args()


def add_path(bundle: zipfile.ZipFile, path: Path, source: Path) -> None:
    relative = path.relative_to(source).as_posix()
    if path.is_symlink():
        info = zipfile.ZipInfo(relative)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, os.readlink(path))
    elif path.is_file():
        bundle.write(path, relative)


def main() -> int:
    args = parse_args()
    source = args.input.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    readme = args.readme.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise NotADirectoryError(source)
    candidates = sorted(source.rglob("*"), key=lambda path: path.as_posix())
    if not any(path.is_file() or path.is_symlink() for path in candidates):
        raise FileNotFoundError(f"no package files found in {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in candidates:
            add_path(bundle, path, source)
        bundle.write(readme, "README.txt")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
