"""SBY status and log parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path

from oss_cad_mcp.models.results import PropertyStatus

_DONE_RE = re.compile(r"DONE \((PASS|FAIL|UNKNOWN|ERROR)")
_STATUS_RE = re.compile(r"Status returned by engine for .*?: (pass|fail|unknown)", re.IGNORECASE)


def parse_status_jsonl(text: str) -> list[PropertyStatus]:
    statuses: list[PropertyStatus] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        status_raw = str(item.get("status", "UNKNOWN")).upper()
        if status_raw not in {"PASS", "FAIL", "UNKNOWN", "ERROR"}:
            status_raw = "UNKNOWN"
        depth = item.get("depth")
        statuses.append(
            PropertyStatus(
                name=str(item.get("name", "unknown")),
                kind=str(item.get("kind")) if item.get("kind") else None,
                status=status_raw,  # type: ignore[arg-type]
                depth=int(depth) if isinstance(depth, int) else None,
                location=str(item.get("location")) if item.get("location") else None,
            )
        )
    return statuses


def parse_summary(text: str) -> tuple[str, str | None]:
    status = "unknown"
    engine = None
    done = _DONE_RE.search(text)
    if done:
        status = {"PASS": "passed", "FAIL": "failed", "UNKNOWN": "unknown", "ERROR": "error"}[done.group(1)]
    elif "Status: passed" in text:
        status = "passed"
    else:
        match = _STATUS_RE.search(text)
        if match:
            status = {"pass": "passed", "fail": "failed", "unknown": "unknown"}[match.group(1).lower()]
    engine_match = re.search(r"engine_\d+ \(([^)]+)\)", text)
    if engine_match:
        engine = engine_match.group(1)
    return status, engine


def find_trace(work_dir: Path) -> Path | None:
    candidates = [path for path in work_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".vcd", ".fst"}]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
