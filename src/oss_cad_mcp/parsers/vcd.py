"""Streaming VCD parsing for waveform inspection.

The parser intentionally supports the subset emitted by Icarus, Verilator,
GHDL, and sby traces: scopes, scalar/vector/real/string changes, dump commands,
and a time window. It never loads a whole waveform into memory.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oss_cad_mcp.models.results import WaveformSample


@dataclass(frozen=True)
class VcdVar:
    code: str
    name: str
    width: int
    kind: str
    scope: str

    @property
    def path(self) -> str:
        return f"{self.scope}.{self.name}" if self.scope else self.name


@dataclass
class VcdHeader:
    path: Path
    timescale: str | None = None
    vars: list[VcdVar] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)

    @property
    def signal_count(self) -> int:
        return len(self.vars)


@dataclass
class VcdScanResult:
    header: VcdHeader
    max_time: int | None = None
    samples: list[WaveformSample] = field(default_factory=list)
    stats: dict[str, dict[str, object]] = field(default_factory=dict)
    truncated: bool = False
    selected: list[VcdVar] = field(default_factory=list)


def parse_header(path: Path) -> VcdHeader:
    header = VcdHeader(path=path)
    scope: list[str] = []
    pending_kind: str | None = None
    pending: list[str] = []

    def flush() -> None:
        nonlocal pending_kind, pending
        if pending_kind == "scope" and len(pending) >= 2:
            scope.append(pending[1])
        elif pending_kind == "upscope":
            if scope:
                scope.pop()
        elif pending_kind == "timescale":
            header.timescale = " ".join(pending).strip()
        elif pending_kind == "var" and len(pending) >= 4:
            kind = pending[0]
            try:
                width = int(pending[1])
            except ValueError:
                width = 1
            code = pending[2]
            name = " ".join(pending[3:]).strip()
            name = re.sub(r"\s+\[[^\]]+\]$", "", name)
            header.vars.append(VcdVar(code=code, name=name, width=width, kind=kind, scope=".".join(scope)))
        pending_kind = None
        pending = []

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if pending_kind is None:
                if not line.startswith("$"):
                    continue
                token = line.split(None, 1)[0]
                if token == "$enddefinitions":
                    break
                if token == "$comment":
                    pending_kind = "comment"
                    pending = []
                    if line.endswith("$end"):
                        pending_kind = None
                    continue
                if token in {"$date", "$version"}:
                    pending_kind = token[1:]
                    pending = []
                    if line.endswith("$end"):
                        pending_kind = None
                    continue
                if token in {"$scope", "$upscope", "$timescale", "$var"}:
                    pending_kind = token[1:]
                    pending = line.split()[1:]
                    if pending and pending[-1] == "$end":
                        pending = pending[:-1]
                        flush()
                    continue
            else:
                if line.endswith("$end"):
                    parts = line.split()
                    if parts and parts[-1] == "$end":
                        parts = parts[:-1]
                    pending.extend(parts)
                    flush()
                else:
                    pending.extend(line.split())
    if pending_kind:
        flush()
    header.scopes = sorted({var.scope for var in header.vars if var.scope})
    return header


def _selected_vars(header: VcdHeader, signals: list[str] | None, max_signals: int) -> list[VcdVar]:
    if not signals:
        return header.vars[:max_signals]
    selected: list[VcdVar] = []
    seen: set[str] = set()
    for pattern in signals:
        for var in header.vars:
            path = var.path
            if (
                var.name == pattern
                or path == pattern
                or fnmatch.fnmatch(path, pattern)
                or fnmatch.fnmatch(var.name, pattern)
            ):
                if path not in seen:
                    selected.append(var)
                    seen.add(path)
                if len(selected) >= max_signals:
                    return selected
    return selected


def _value_to_int(value: str, width: int) -> int | None:
    if not value or any(ch in value.lower() for ch in "xz"):
        return None
    if width > 64:
        return None
    try:
        return int(value, 2)
    except ValueError:
        return None


def scan(
    path: Path,
    *,
    signals: list[str] | None = None,
    start: int = 0,
    end: int | None = None,
    max_samples: int = 10_000,
    max_signals: int = 256,
    collect_samples: bool = True,
) -> VcdScanResult:
    header = parse_header(path)
    selected = _selected_vars(header, signals, max_signals)
    by_code: dict[str, list[VcdVar]] = {}
    for var in selected:
        by_code.setdefault(var.code, []).append(var)
    {var.path for var in selected}
    values: dict[str, str] = {}
    stats: dict[str, dict[str, Any]] = {
        var.path: {
            "transitions": 0,
            "first": None,
            "last": None,
            "min": None,
            "max": None,
            "width": var.width,
            "kind": var.kind,
        }
        for var in selected
    }
    samples: list[WaveformSample] = []
    pending_change = False
    current_time = 0
    max_time: int | None = None
    truncated = False
    in_header = True
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if in_header:
                if line.startswith("$enddefinitions"):
                    in_header = False
                continue
            if line.startswith("#"):
                if (
                    pending_change
                    and collect_samples
                    and start <= current_time
                    and (end is None or current_time <= end)
                ):
                    snapshot = {var.path: values.get(var.code, "x") for var in selected}
                    samples.append(WaveformSample(time=current_time, values=snapshot))
                    if len(samples) >= max_samples:
                        truncated = True
                        break
                pending_change = False
                try:
                    current_time = int(line[1:])
                except ValueError:
                    continue
                max_time = current_time if max_time is None else max(max_time, current_time)
                continue
            if line.startswith("$"):
                continue
            if line[0] in "01xXzZ" and len(line) > 1:
                code = line[1:]
                new_value = line[0].lower()
            elif line[0].lower() in {"b", "r", "s"}:
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                new_value = parts[0][1:]
                code = parts[1].strip()
            else:
                continue
            vars_for_code = by_code.get(code)
            if not vars_for_code:
                continue
            for var in vars_for_code:
                old = values.get(code)
                if old != new_value:
                    stats[var.path]["transitions"] = int(stats[var.path]["transitions"]) + 1
                    if stats[var.path]["first"] is None:
                        stats[var.path]["first"] = new_value
                    stats[var.path]["last"] = new_value
                    number = _value_to_int(new_value, var.width)
                    if number is not None:
                        current_min = stats[var.path]["min"]
                        current_max = stats[var.path]["max"]
                        stats[var.path]["min"] = number if current_min is None else min(int(current_min), number)
                        stats[var.path]["max"] = number if current_max is None else max(int(current_max), number)
                    pending_change = True
            values[code] = new_value
    if (
        pending_change
        and collect_samples
        and len(samples) < max_samples
        and start <= current_time
        and (end is None or current_time <= end)
    ):
        samples.append(
            WaveformSample(
                time=current_time,
                values={var.path: values.get(var.code, "x") for var in selected},
            )
        )
    return VcdScanResult(
        header=header,
        max_time=max_time,
        samples=samples,
        stats=stats,
        truncated=truncated,
        selected=selected,
    )
