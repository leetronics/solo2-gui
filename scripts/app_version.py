#!/usr/bin/env python3
"""Resolve and materialize SoloKeys GUI build versions."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import tomllib


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BUILD_VERSION_MODULE = ROOT / "src" / "solo_gui" / "_build_version.py"


def source_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return str(data["tool"]["poetry"]["version"])


def resolved_version() -> str:
    override = os.environ.get("SOLOKEYS_GUI_VERSION", "").strip()
    if override:
        return override
    return source_version()


def write_build_module(version: str) -> None:
    BUILD_VERSION_MODULE.write_text(
        '"""Generated at build time; do not commit."""\n\n'
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("source")
    sub.add_parser("resolved")
    write = sub.add_parser("write-build-module")
    write.add_argument("--version", default=None)
    args = parser.parse_args()

    if args.command == "source":
        print(source_version())
        return 0
    if args.command == "resolved":
        print(resolved_version())
        return 0
    if args.command == "write-build-module":
        write_build_module(args.version or resolved_version())
        print(BUILD_VERSION_MODULE)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
