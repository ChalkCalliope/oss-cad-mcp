"""nextpnr JSON report parsing."""

from __future__ import annotations

import json
from pathlib import Path

from oss_cad_mcp.models.results import (
    ClockTiming,
    CriticalPath,
    PathStep,
    ResourceUsage,
    TimingReport,
)


def parse_report(path: Path, *, source: str | None = None) -> tuple[TimingReport, dict[str, ResourceUsage]]:
    if not path.exists():
        return TimingReport(ok=False, source=source, summary="Timing report was not produced."), {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return TimingReport(ok=False, source=source, summary=f"Could not parse timing report: {exc}"), {}
    if not isinstance(data, dict):
        return TimingReport(ok=False, source=source, summary="Timing report has an unexpected shape."), {}

    clocks: list[ClockTiming] = []
    fmax = data.get("fmax", {})
    if isinstance(fmax, dict):
        for name, value in fmax.items():
            if isinstance(value, dict):
                achieved = value.get("achieved")
                constraint = value.get("constraint")
            else:
                achieved = value
                constraint = None
            achieved_f = float(achieved) if isinstance(achieved, (int, float)) else None
            constraint_f = float(constraint) if isinstance(constraint, (int, float)) else None
            met = achieved_f >= constraint_f if achieved_f is not None and constraint_f is not None else None
            clocks.append(
                ClockTiming(
                    name=str(name),
                    achieved_mhz=achieved_f,
                    constraint_mhz=constraint_f,
                    met=met,
                )
            )

    critical_paths: list[CriticalPath] = []
    raw_paths = data.get("critical_paths", [])
    if isinstance(raw_paths, list):
        for raw_path in raw_paths[:10]:
            if not isinstance(raw_path, dict):
                continue
            steps: list[PathStep] = []
            logic = 0.0
            routing = 0.0
            for raw_step in raw_path.get("path", []) or []:
                if not isinstance(raw_step, dict):
                    continue
                delay = raw_step.get("delay")
                delay_f = float(delay) if isinstance(delay, (int, float)) else None
                kind = str(raw_step.get("type")) if raw_step.get("type") else None
                if kind == "logic" and delay_f:
                    logic += delay_f
                elif kind == "routing" and delay_f:
                    routing += delay_f
                from_cell = raw_step.get("from", {})
                to_cell = raw_step.get("to", {})
                steps.append(
                    PathStep(
                        type=kind,
                        delay_ns=delay_f,
                        from_cell=str(from_cell.get("cell"))
                        if isinstance(from_cell, dict) and from_cell.get("cell")
                        else None,
                        to_cell=str(to_cell.get("cell")) if isinstance(to_cell, dict) and to_cell.get("cell") else None,
                        net=str(raw_step.get("net")) if raw_step.get("net") else None,
                        sources=[str(item) for item in raw_step.get("sources", [])][:10],
                    )
                )
            critical_paths.append(
                CriticalPath(
                    from_node=str(raw_path.get("from")) if raw_path.get("from") else None,
                    to_node=str(raw_path.get("to")) if raw_path.get("to") else None,
                    delay_ns=(logic + routing) or None,
                    logic_ns=logic or None,
                    routing_ns=routing or None,
                    steps=steps,
                )
            )

    utilization: dict[str, ResourceUsage] = {}
    raw_util = data.get("utilization", {})
    if isinstance(raw_util, dict):
        for name, value in raw_util.items():
            if not isinstance(value, dict):
                continue
            used = int(value.get("used", 0) or 0)
            available = int(value.get("available", 0) or 0)
            utilization[str(name)] = ResourceUsage(
                used=used,
                available=available,
                percent=(100.0 * used / available) if available else None,
            )

    met = all(clock.met is not False for clock in clocks) if clocks else None
    max_freq = max((clock.achieved_mhz for clock in clocks if clock.achieved_mhz is not None), default=None)
    ok = bool(clocks or utilization) and met is not False
    summary = f"Timing {'met' if met else 'not met' if met is False else 'unknown'}" + (
        f"; max {max_freq:.2f} MHz" if max_freq else ""
    )
    return (
        TimingReport(
            ok=ok,
            source=source,
            clocks=clocks,
            critical_paths=critical_paths,
            met=met,
            max_frequency_mhz=max_freq,
            summary=summary,
        ),
        utilization,
    )
