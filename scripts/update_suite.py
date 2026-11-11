#!/usr/bin/env python3
"""Standalone OSS CAD Suite updater.

Can be run before `uv sync` because the updater itself is stdlib-only. It adds
the repository's src directory to sys.path when the package is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    try:
        import oss_cad_mcp.suite_update  # noqa: F401
    except ImportError:
        src = Path(__file__).resolve().parents[1] / "src"
        if src.is_dir():
            sys.path.insert(0, str(src))


def main() -> int:
    _bootstrap()
    from oss_cad_mcp.suite_update import main as updater_main

    return updater_main()


if __name__ == "__main__":
    raise SystemExit(main())
