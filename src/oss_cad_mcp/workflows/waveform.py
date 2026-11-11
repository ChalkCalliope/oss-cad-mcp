"""Waveform conversion and inspection workflows."""

from __future__ import annotations

import csv
import fnmatch
import re
from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.models.results import (
    SignalInfo,
    WaveformInfo,
    WaveformReport,
)
from oss_cad_mcp.parsers.vcd import parse_header, scan
from oss_cad_mcp.workflows.common import output_path, run_tool, work_path

WaveAction = Literal["info", "signals", "sample", "summarize", "export", "convert"]


def _parse_time(value: str | int | float | None, timescale: str | None) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)", text)
    if not match:
        raise InvalidInput(f"Could not parse time value: {value}")
    amount = float(match.group(1))
    unit = match.group(2) or None
    if unit is None:
        return int(amount)
    multipliers = {
        "fs": 1e-15,
        "ps": 1e-12,
        "ns": 1e-9,
        "us": 1e-6,
        "ms": 1e-3,
        "s": 1.0,
    }
    if unit not in multipliers:
        raise InvalidInput(f"Unknown time unit: {unit}")
    scale = _timescale_seconds(timescale or "1ns")
    ticks = (amount * multipliers[unit]) / scale
    return round(ticks)


def _timescale_seconds(value: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(fs|ps|ns|us|ms|s)", value.lower())
    if not match:
        return 1e-9
    amount = float(match.group(1))
    return (
        amount
        * {
            "fs": 1e-15,
            "ps": 1e-12,
            "ns": 1e-9,
            "us": 1e-6,
            "ms": 1e-3,
            "s": 1.0,
        }[match.group(2)]
    )


def _signal_infos(header, signals: list[str] | None, max_signals: int) -> tuple[list[SignalInfo], bool]:
    selected = []
    for var in header.vars:
        if signals and not any(
            var.name == pattern
            or var.path == pattern
            or fnmatch.fnmatch(var.path, pattern)
            or fnmatch.fnmatch(var.name, pattern)
            for pattern in signals
        ):
            continue
        selected.append(SignalInfo(name=var.path, width=var.width, kind=var.kind, scope=var.scope))
        if len(selected) >= max_signals:
            return selected, True
    return selected, False


async def _ensure_vcd(app: AppContext, job, path: Path) -> Path:
    if path.suffix.lower() == ".vcd":
        return path
    output = work_path(job, "converted.vcd")
    if path.suffix.lower() == ".fst":
        result = await run_tool(
            app,
            job,
            name="fst2vcd",
            args=["-f", str(path), "-o", str(output)],
            cwd=app.workspace,
            timeout_s=120.0,
            step_name="convert-vcd",
        )
    elif path.suffix.lower() in {".lxt", ".lxt2"}:
        result = await run_tool(
            app,
            job,
            name="lxt2vcd",
            args=["-l", str(path), "-o", str(output)],
            cwd=app.workspace,
            timeout_s=120.0,
            step_name="convert-vcd",
        )
    elif path.suffix.lower() == ".vzt":
        result = await run_tool(
            app,
            job,
            name="vzt2vcd",
            args=["-v", str(path), "-o", str(output)],
            cwd=app.workspace,
            timeout_s=120.0,
            step_name="convert-vcd",
        )
    elif path.suffix.lower() == ".ghw":
        raise InvalidInput(
            "GHW analysis is not supported in v0.1.",
            hint="Re-run GHDL with --vcd=wave.vcd, or use ghwdump for textual inspection.",
        )
    else:
        raise InvalidInput(f"Unsupported waveform format: {path.suffix}")
    if result.exit_code != 0 or not output.exists():
        raise InvalidInput(f"Could not convert {path.name} to VCD (exit {result.exit_code}).")
    return output


async def run_waveform(
    app: AppContext,
    job,
    *,
    action: WaveAction,
    path: str | Path,
    signals: list[str] | None = None,
    start: str | int | float | None = None,
    end: str | int | float | None = None,
    max_samples: int = 1_000,
    max_signals: int = 64,
    output_format: Literal["vcd", "fst"] | None = None,
) -> WaveformReport:
    source = app.paths.resolve_read(path, must_exist=True)
    if action == "convert":
        if output_format not in {"vcd", "fst"}:
            raise InvalidInput("output_format must be 'vcd' or 'fst'.")
        target = output_path(app, job, "waveform", f"converted.{output_format}")
        if output_format == "fst":
            result = await run_tool(
                app,
                job,
                name="vcd2fst",
                args=[str(source), str(target)],
                cwd=app.workspace,
                timeout_s=120.0,
                step_name="convert-fst",
            )
        else:
            vcd = await _ensure_vcd(app, job, source)
            target = output_path(app, job, "waveform", "converted.vcd")
            target.write_bytes(vcd.read_bytes())
            result = None
        if result is not None and (result.exit_code != 0 or not target.exists()):
            raise InvalidInput(f"Waveform conversion failed (exit {result.exit_code}).")
        artifact = app.artifacts.register(job, target, kind="waveform", description="Converted waveform")
        return WaveformReport(
            ok=True,
            action=action,
            artifact=artifact,
            job=job.to_ref(),
            artifacts=[artifact],
            summary=f"Converted {source.name} to {output_format.upper()}.",
        )

    vcd_path = await _ensure_vcd(app, job, source)
    header = parse_header(vcd_path)
    start_tick = _parse_time(start, header.timescale)
    end_tick = _parse_time(end, header.timescale) if end is not None else None
    if end_tick is not None and end_tick < start_tick:
        raise InvalidInput("Waveform end time precedes start time.")

    if action in {"info", "signals"}:
        selected, truncated = _signal_infos(header, signals, max_signals)
        scan_result = scan(
            vcd_path,
            signals=None,
            start=0,
            end=None,
            max_samples=1,
            max_signals=1,
            collect_samples=False,
        )
        info = WaveformInfo(
            path=app.paths.display(source),
            format=source.suffix.lstrip(".").lower(),
            timescale=header.timescale,
            signal_count=header.signal_count,
            max_time=scan_result.max_time,
            scopes=header.scopes,
            signals=selected,
            signals_truncated=truncated or len(header.vars) > len(selected),
        )
        if action == "signals":
            summary = (
                f"{len(selected)} selected signal(s) of {header.signal_count}, "
                f"timescale {header.timescale or 'unknown'}, max time {scan_result.max_time}."
            )
        else:
            summary = (
                f"{header.signal_count} signals, timescale {header.timescale or 'unknown'}, "
                f"max time {scan_result.max_time}."
            )
        return WaveformReport(
            ok=True,
            action=action,
            info=info,
            job=job.to_ref(),
            summary=summary,
        )

    scan_result = scan(
        vcd_path,
        signals=signals,
        start=start_tick,
        end=end_tick,
        max_samples=max_samples,
        max_signals=max_signals,
        collect_samples=action in {"sample", "export"},
    )
    if not scan_result.selected:
        raise InvalidInput("No waveform signals matched the requested patterns.")
    if action == "summarize":
        info = WaveformInfo(
            path=app.paths.display(source),
            format=source.suffix.lstrip(".").lower(),
            timescale=header.timescale,
            signal_count=header.signal_count,
            max_time=scan_result.max_time,
            scopes=header.scopes,
            signals=[
                SignalInfo(name=var.path, width=var.width, kind=var.kind, scope=var.scope)
                for var in scan_result.selected
            ],
        )
        return WaveformReport(
            ok=True,
            action=action,
            info=info,
            signal_stats=scan_result.stats,
            job=job.to_ref(),
            summary=f"Summarized {len(scan_result.selected)} signal(s) through time {scan_result.max_time}.",
        )
    if action == "export":
        target = output_path(app, job, "waveform", "samples.csv")
        fieldnames = ["time", *[var.path for var in scan_result.selected]]
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for sample in scan_result.samples:
                writer.writerow({"time": sample.time, **sample.values})
        artifact = app.artifacts.register(job, target, kind="report", description="Waveform samples CSV")
        return WaveformReport(
            ok=True,
            action=action,
            samples=scan_result.samples,
            artifact=artifact,
            job=job.to_ref(),
            artifacts=[artifact],
            summary=f"Exported {len(scan_result.samples)} waveform sample(s).",
        )
    return WaveformReport(
        ok=True,
        action=action,
        samples=scan_result.samples,
        job=job.to_ref(),
        summary=f"Sampled {len(scan_result.samples)} point(s) from {len(scan_result.selected)} signal(s).",
    )
