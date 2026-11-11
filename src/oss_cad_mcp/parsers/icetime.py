"""icetime JSON and text parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path

from oss_cad_mcp.models.results import ClockTiming, CriticalPath, PathStep, TimingReport

_ESTIMATE_RE = re.compile(r"Timing estimate:\s*([\d.]+)\s*ns\s*\(([\d.]+)\s*MHz\)")


def parse_icetime(json_path: Path | None, stdout: str = "", *, clock_mhz: float | None = None) -> TimingReport:
    paths: list[list[dict[str, object]]] = []
    if json_path is not None and json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list):
                paths = [item for item in data if isinstance(item, list)]
        except OSError, json.JSONDecodeError:
            paths = []
    max_ns = 0.0
    critical: list[CriticalPath] = []
    for raw_path in paths[:10]:
        steps: list[PathStep] = []
        for raw_step in raw_path:
            if not isinstance(raw_step, dict):
                continue
            delay = raw_step.get("delay_ns")
            steps.append(
                PathStep(
                    type=str(raw_step.get("cell_type")) if raw_step.get("cell_type") else None,
                    delay_ns=float(delay) if isinstance(delay, (int, float)) else None,
                    from_cell=str(raw_step.get("cell")) if raw_step.get("cell") else None,
                    to_cell=str(raw_step.get("cell")) if raw_step.get("cell") else None,
                    net=str(raw_step.get("net")) if raw_step.get("net") else None,
                )
            )
        delay = steps[-1].delay_ns if steps and steps[-1].delay_ns is not None else None
        if delay is None:
            delay = max((step.delay_ns or 0.0 for step in steps), default=0.0)
        max_ns = max(max_ns, delay)
        critical.append(CriticalPath(delay_ns=delay, steps=steps))

    match = _ESTIMATE_RE.search(stdout)
    estimate_ns = float(match.group(1)) if match else None
    estimate_mhz = float(match.group(2)) if match else None
    if max_ns <= 0 and estimate_ns:
        max_ns = estimate_ns
    if estimate_mhz is None and max_ns > 0:
        estimate_mhz = 1000.0 / max_ns
    met = None
    if estimate_mhz is not None and clock_mhz is not None:
        met = estimate_mhz >= clock_mhz
    clocks = (
        [
            ClockTiming(
                name="clk",
                achieved_mhz=estimate_mhz,
                constraint_mhz=clock_mhz,
                met=met,
            )
        ]
        if estimate_mhz is not None
        else []
    )
    ok = bool(clocks or critical)
    return TimingReport(
        ok=ok,
        source="icetime",
        clocks=clocks,
        critical_paths=critical,
        met=met,
        max_frequency_mhz=estimate_mhz,
        summary=(f"icetime estimate {max_ns:.2f} ns" + (f" ({estimate_mhz:.2f} MHz)" if estimate_mhz else ""))
        if ok
        else "No icetime timing data was parsed.",
    )
