#!/usr/bin/env python3
"""Run the packaged desktop application's display-free dependency check."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()

    source = args.input.expanduser().resolve(strict=True)
    suffix = ".exe" if os.name == "nt" else ""
    executable = source / f"Massive-Magnetics-Catalog-Recovery{suffix}"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    completed = subprocess.run(
        [str(executable), "--self-test"],
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"desktop self-test failed with exit code {completed.returncode}"
        )
    print(f"desktop self-test passed: {executable.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
