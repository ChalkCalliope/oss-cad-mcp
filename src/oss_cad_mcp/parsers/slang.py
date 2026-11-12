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


_MODULE_LINE_RE = re.compile(r'^Module="(?P<module>[^"]+)"\s+Instance="(?P<instance>[^"]+)"(?P<rest>.*)$')
_PARAM_RE = re.compile(r"(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>[^,]+)")


def _parse_parameters(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    return {match.group("name"): match.group("value").strip() for match in _PARAM_RE.finditer(text)}


def parse_hierarchy(text: str) -> list[HierarchyNode]:
    """Parse slang-hier output into a hierarchy tree.

    Modern slang-hier emits one structured line per instance:

        Module="stream_top" Instance="stream_top.u_fifo" ... Parameters: WIDTH=32'd8

    Older/other tools emit an indented name tree. Both are supported; the
    structured form is preferred when present.
    """

    entries: list[tuple[str, str, dict[str, str]]] = []
    for raw in text.splitlines():
        match = _MODULE_LINE_RE.match(raw.strip())
        if match:
            rest = match.group("rest")
            params = rest.split("Parameters:", 1)[1] if "Parameters:" in rest else None
            entries.append(
                (
                    match.group("module"),
                    match.group("instance"),
                    _parse_parameters(params),
                )
            )
    if entries:
        nodes: dict[str, HierarchyNode] = {}
        for module, instance, parameters in entries:
            leaf = instance.rsplit(".", 1)[-1]
            nodes[instance] = HierarchyNode(
                name=leaf,
                module=module,
                depth=instance.count("."),
                parameters=parameters,
            )
        roots: list[HierarchyNode] = []
        for _module, instance, _parameters in entries:
            node = nodes[instance]
            parent_path = instance.rsplit(".", 1)[0] if "." in instance else None
            if parent_path and parent_path in nodes:
                nodes[parent_path].children.append(node)
            else:
                roots.append(node)
        return roots

    return _parse_indented_hierarchy(text)


def _parse_indented_hierarchy(text: str) -> list[HierarchyNode]:
    """Parse a legacy indented name tree conservatively."""

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
