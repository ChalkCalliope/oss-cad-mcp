"""Yosys log and JSON stat parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from oss_cad_mcp.models.diagnostics import Diagnostic

_WARNING_RE = re.compile(r"^(?:Warning|WARNING):\s*(?P<message>.+)$")
_ERROR_RE = re.compile(r"^(?:ERROR|Error):\s*(?P<message>.+)$")
_FILELINE_RE = re.compile(r"(?P<file>[^:\s]+\.(?:v|sv|vh|vhd|vhdl)):(?P<line>\d+)(?:\.(?P<col>\d+))?")


def parse_log(text: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.strip()
        match = _ERROR_RE.match(line)
        severity = "error"
        if not match:
            match = _WARNING_RE.match(line)
            severity = "warning"
        if not match:
            continue
        message = match.group("message").strip()
        file = None
        lineno = None
        column = None
        location = _FILELINE_RE.search(message)
        if location:
            file = location.group("file")
            lineno = int(location.group("line"))
            column = int(location.group("col")) if location.group("col") else None
        diagnostics.append(
            Diagnostic(
                engine="yosys",
                severity=severity,  # type: ignore[arg-type]
                message=message,
                file=file,
                line=lineno,
                column=column,
            )
        )
    return diagnostics


def parse_stat_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except OSError, json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def summarize_stat(stat: dict[str, Any]) -> dict[str, Any]:
    design = stat.get("design", {}) if isinstance(stat, dict) else {}
    modules = stat.get("modules", {}) if isinstance(stat, dict) else {}
    cells_by_type: dict[str, int] = {}
    for module in modules.values() if isinstance(modules, dict) else []:
        if not isinstance(module, dict):
            continue
        counts = module.get("num_cells_by_type", {})
        if isinstance(counts, dict):
            for key, value in counts.items():
                if isinstance(value, int):
                    cells_by_type[str(key)] = cells_by_type.get(str(key), 0) + value
    if not cells_by_type and isinstance(design, dict):
        counts = design.get("num_cells_by_type", {})
        if isinstance(counts, dict):
            cells_by_type = {str(key): int(value) for key, value in counts.items() if isinstance(value, int)}
    return {
        "cells_by_type": cells_by_type,
        "num_cells": int(design.get("num_cells", 0)) if isinstance(design, dict) else 0,
        "num_wires": int(design.get("num_wires", 0)) if isinstance(design, dict) else 0,
        "num_wire_bits": int(design.get("num_wire_bits", 0)) if isinstance(design, dict) else 0,
        "num_memories": int(design.get("num_memories", 0)) if isinstance(design, dict) else 0,
        "num_memory_bits": int(design.get("num_memory_bits", 0)) if isinstance(design, dict) else 0,
        "num_processes": int(design.get("num_processes", 0)) if isinstance(design, dict) else 0,
        "num_submodules": int(design.get("num_submodules", 0)) if isinstance(design, dict) else 0,
        "modules": sorted(modules) if isinstance(modules, dict) else [],
    }
