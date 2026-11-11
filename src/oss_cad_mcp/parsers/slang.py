"""Parsers for slang JSON diagnostics and hierarchy output."""

from __future__ import annotations

import json
import re
from pathlib import Path

from oss_cad_mcp.models.diagnostics import Diagnostic, HierarchyNode

_LOCATION_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?$")


def _display_path(value: str, base: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def parse_diagnostics(path: Path, *, base: Path) -> list[Diagnostic]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except OSError, json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    diagnostics: list[Diagnostic] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "error")).lower()
        if severity not in {"error", "warning", "info", "note"}:
            severity = "info"
        message = str(item.get("message", "")).strip()
        location = str(item.get("location", "") or "")
        file = None
        line = None
        column = None
        match = _LOCATION_RE.match(location)
        if match:
            file = _display_path(match.group("file"), base)
            line = int(match.group("line"))
            column = int(match.group("col")) if match.group("col") else None
        diagnostics.append(
            Diagnostic(
                engine="slang",
                severity=severity,  # type: ignore[arg-type]
                message=message,
                code=str(item.get("code")) if item.get("code") else None,
                file=file,
                line=line,
                column=column,
            )
        )
    return diagnostics


def parse_hierarchy(text: str) -> list[HierarchyNode]:
    """Parse the indented tree printed by slang-hier.

    The exact format is intentionally parsed conservatively: lines beginning
    with a module name and indentation determine nesting. Unrecognized lines
    are ignored.
    """

    roots: list[HierarchyNode] = []
    stack: list[tuple[int, HierarchyNode]] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith(("Build", "Top", "Warning:", "Error:")):
            continue
        stripped = line.strip()
        name = stripped.split()[0]
        if not re.match(r"^[A-Za-z_\\$][\w$\\.]*$", name):
            continue
        depth = len(line) - len(line.lstrip())
        node = HierarchyNode(name=name, module=name, depth=depth)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((depth, node))
    return roots
