"""Timing report parsing and iCE40 icetime execution."""

from __future__ import annotations

import json
from pathlib import Path

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput, PreflightFailed
from oss_cad_mcp.models.results import TimingReport
from oss_cad_mcp.parsers import icetime as icetime_parser
from oss_cad_mcp.parsers import nextpnr as nextpnr_parser
from oss_cad_mcp.workflows.common import read_tail, run_tool, work_path


def parse_timing_report(app: AppContext, path: str | Path, *, clock_mhz: float | None = None) -> TimingReport:
    resolved = app.paths.resolve_read(path, must_exist=True)
    suffix = resolved.suffix.lower()
    if suffix == ".asc":
        raise InvalidInput(
            "An .asc file needs icetime execution; pass action='icetime' instead.",
            hint="Use cad_timing(action='icetime', path='design.asc', device='up5k').",
        )
    try:
        data = json.loads(resolved.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidInput(f"Could not parse timing report: {exc}") from exc
    if isinstance(data, dict) and "fmax" in data:
        report, _ = nextpnr_parser.parse_report(resolved, source=resolved.name)
        return report
    if isinstance(data, list):
        report = icetime_parser.parse_icetime(resolved, clock_mhz=clock_mhz)
        report.source = resolved.name
        return report
    raise InvalidInput("Unrecognized timing report format.", hint="Expected nextpnr report JSON or icetime -j JSON.")


async def run_icetime(
    app: AppContext,
    job,
    *,
    asc_path: str | Path,
    device: str,
    clock_mhz: float | None = None,
    timeout_s: float = 120.0,
) -> TimingReport:
    if not app.require_suite().has("icetime"):
        raise PreflightFailed("icetime is not installed in this suite.")
    asc = app.paths.resolve_read(asc_path, must_exist=True)
    json_path = work_path(job, "icetime.json")
    args = ["-d", device, "-j", str(json_path), str(asc)]
    result = await run_tool(
        app, job, name="icetime", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="icetime"
    )
    if result.exit_code != 0:
        raise InvalidInput(
            f"icetime exited with code {result.exit_code}.",
            hint="Check the device name and the .asc input.",
        )
    log = read_tail(Path(job.steps[-1].stdout), max_bytes=2_000_000) if job.steps else ""
    report = icetime_parser.parse_icetime(json_path, log, clock_mhz=clock_mhz)
    report.job = job.to_ref()
    return report
