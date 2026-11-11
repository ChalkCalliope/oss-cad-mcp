"""Verilator diagnostic parsers (SARIF preferred, text fallback)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from oss_cad_mcp.models.diagnostics import Diagnostic

_TEXT_RE = re.compile(
    r"^%(?P<kind>Error|Warning)(?:-(?P<code>[A-Za-z0-9_]+))?:\s*"
    r"(?:(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*)?(?P<message>.*)$"
)


def _display_path(value: str, base: Path) -> str:
    if value.startswith("file://"):
        value = unquote(urlparse(value).path)
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def parse_sarif(path: Path, *, base: Path) -> list[Diagnostic]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except OSError, json.JSONDecodeError:
        return []
    diagnostics: list[Diagnostic] = []
    runs = data.get("runs", []) if isinstance(data, dict) else []
    for run in runs:
        if not isinstance(run, dict):
            continue
        for result in run.get("results", []):
            if not isinstance(result, dict):
                continue
            message_obj = result.get("message", {})
            message = str(message_obj.get("text", "")) if isinstance(message_obj, dict) else str(message_obj)
            if not message:
                continue
            locations = result.get("locations", []) or []
            file = None
            line = None
            column = None
            end_line = None
            end_column = None
            snippet = None
            if locations:
                physical = locations[0].get("physicalLocation", {}) if isinstance(locations[0], dict) else {}
                artifact = physical.get("artifactLocation", {}) if isinstance(physical, dict) else {}
                region = physical.get("region", {}) if isinstance(physical, dict) else {}
                uri = artifact.get("uri") if isinstance(artifact, dict) else None
                if uri:
                    file = _display_path(str(uri), base)
                if isinstance(region, dict):
                    line = region.get("startLine")
                    column = region.get("startColumn")
                    end_line = region.get("endLine")
                    end_column = region.get("endColumn")
                    snippet_data = region.get("snippet") or region.get("snippit")
                    if isinstance(snippet_data, dict):
                        snippet = snippet_data.get("text")
                    elif isinstance(snippet_data, str):
                        snippet = snippet_data
            if line is None and message.startswith("Exiting due to"):
                continue
            level = str(result.get("level", "error")).lower()
            severity = {"error": "error", "warning": "warning", "note": "info", "none": "info"}.get(level, "info")
            diagnostics.append(
                Diagnostic(
                    engine="verilator",
                    severity=severity,  # type: ignore[arg-type]
                    message=message,
                    code=str(result.get("ruleId")) if result.get("ruleId") else None,
                    file=file,
                    line=int(line) if isinstance(line, int) and line > 0 else None,
                    column=int(column) if isinstance(column, int) and column > 0 else None,
                    end_line=int(end_line) if isinstance(end_line, int) and end_line > 0 else None,
                    end_column=int(end_column) if isinstance(end_column, int) and end_column > 0 else None,
                    snippet=str(snippet) if snippet else None,
                )
            )
    return diagnostics


def parse_text(text: str, *, base: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        match = _TEXT_RE.match(raw.strip())
        if not match:
            continue
        kind = match.group("kind")
        code = match.group("code")
        file = match.group("file")
        line = match.group("line")
        col = match.group("col")
        message = match.group("message").strip()
        diagnostics.append(
            Diagnostic(
                engine="verilator",
                severity="error" if kind == "Error" else "warning",
                message=message,
                code=code,
                file=_display_path(file, base) if file else None,
                line=int(line) if line else None,
                column=int(col) if col else None,
            )
        )
    return diagnostics
