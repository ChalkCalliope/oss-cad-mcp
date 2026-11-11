"""Icarus Verilog and GHDL text diagnostic parsers."""

from __future__ import annotations

import re
from pathlib import Path

from oss_cad_mcp.models.diagnostics import Diagnostic

_IVERILOG_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:\s*(?P<col>\d+):)?\s*"
    r"(?:(?P<severity>error|warning|sorry|note):?\s*)?(?P<message>.+)$",
    re.IGNORECASE,
)
_GHDL_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<severity>error|warning|note|warning)?:?\s*(?P<message>.+)$",
    re.IGNORECASE,
)


def _display_path(value: str, base: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def parse_iverilog(text: str, *, base: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        match = _IVERILOG_RE.match(raw.strip())
        if not match:
            continue
        message = match.group("message").strip()
        if not message or message.startswith("$finish"):
            continue
        severity_raw = (match.group("severity") or "").lower()
        severity = "warning" if severity_raw == "warning" else "error"
        diagnostics.append(
            Diagnostic(
                engine="iverilog",
                severity=severity,
                message=message,
                file=_display_path(match.group("file"), base),
                line=int(match.group("line")),
                column=int(match.group("col")) if match.group("col") else None,
            )
        )
    return diagnostics


def parse_ghdl(text: str, *, base: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        match = _GHDL_RE.match(raw.strip())
        if not match:
            continue
        message = match.group("message").strip()
        lowered = message.lower()
        severity_raw = (match.group("severity") or "").lower()
        if severity_raw == "warning" or lowered.startswith("warning"):
            severity = "warning"
        elif severity_raw == "note":
            severity = "info"
        elif "error" in lowered or severity_raw == "error":
            severity = "error"
        else:
            continue
        diagnostics.append(
            Diagnostic(
                engine="ghdl",
                severity=severity,
                message=message,
                file=_display_path(match.group("file"), base),
                line=int(match.group("line")),
                column=int(match.group("col")) if match.group("col") else None,
            )
        )
    return diagnostics
