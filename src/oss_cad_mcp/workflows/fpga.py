"""Software-only FPGA build flow: synth, place-and-route, pack, timing."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.boards import get_board
from oss_cad_mcp.errors import InvalidInput, PreflightFailed
from oss_cad_mcp.models.common import ErrorInfo, SourceSet
from oss_cad_mcp.models.results import FpgaBuildReport, StageReport
from oss_cad_mcp.parsers import icetime as icetime_parser
from oss_cad_mcp.parsers import nextpnr as nextpnr_parser
from oss_cad_mcp.project import load_project_config
from oss_cad_mcp.workflows.common import run_tool
from oss_cad_mcp.workflows.synthesize import run_synthesize

FpgaFamily = Literal["ice40", "ecp5"]


async def run_fpga_build(
    app: AppContext,
    job,
    *,
    source: SourceSet | None = None,
    top: str | None = None,
    board: str | None = None,
    family: FpgaFamily | None = None,
    device: str | None = None,
    package: str | None = None,
    speed: str | None = None,
    constraints: list[str] | None = None,
    frequency_mhz: float | None = None,
    seed: int | None = None,
    placer: str | None = None,
    router: str | None = None,
    timeout_s: float = 600.0,
) -> FpgaBuildReport:
    suite = app.require_suite()
    project, _ = load_project_config(app.workspace)
    preset = get_board(board) if board else None
    if board and preset is None:
        raise InvalidInput(
            f"Unknown board preset: {board}", hint="Use cad_status or the board catalog for known presets."
        )
    if preset:
        family = preset.family  # type: ignore[assignment]
        device = device or preset.device
        package = package or preset.package
        speed = speed or preset.speed
        frequency_mhz = frequency_mhz or preset.default_frequency_mhz
    if family is None and project and project.target.family:
        family = project.target.family  # type: ignore[assignment]
    if family is None:
        raise InvalidInput(
            "An FPGA family or board is required.",
            hint="Pass board='icebreaker' or family='ice40' with device/package.",
        )
    if family not in {"ice40", "ecp5"}:
        raise InvalidInput(
            f"Software build flow currently supports iCE40 and ECP5, not {family}.",
            hint="Use cad_synthesize + cad_tool_run for other targets.",
        )
    if not device:
        raise InvalidInput("An FPGA device is required.", hint="Example: device='up5k' for iCE40.")
    if family == "ecp5" and not package:
        raise InvalidInput("ECP5 builds require a package, e.g. package='CABGA381'.")
    if not frequency_mhz:
        frequency_mhz = project.target.frequency_mhz if project and project.target.frequency_mhz else 25.0
    constraint_patterns = constraints or (project.target.constraints if project else [])
    constraint_paths = [app.paths.resolve_read(item, must_exist=True) for item in constraint_patterns]
    if not constraint_paths:
        raise InvalidInput(
            "A physical constraint file is required for PnR.",
            hint="Pass constraints=['pins.pcf'] (iCE40) or ['pins.lpf'] (ECP5).",
        )
    expected_suffix = ".pcf" if family == "ice40" else ".lpf"
    for path in constraint_paths:
        if path.suffix.lower() != expected_suffix:
            raise InvalidInput(
                f"{family} expects {expected_suffix} constraints, got {path.name}.",
                hint="Check the constraint file extension or the selected board family.",
            )
    pnr_tool = "nextpnr-ice40" if family == "ice40" else "nextpnr-ecp5"
    pack_tool = "icepack" if family == "ice40" else "ecppack"
    for tool in (pnr_tool, pack_tool):
        if not suite.has(tool):
            raise PreflightFailed(f"{tool} is not installed in this suite.")

    synth_report = await run_synthesize(
        app,
        job,
        source=source,
        top=top,
        target=family,  # type: ignore[arg-type]
        timeout_s=timeout_s,
    )
    stages = [
        StageReport(
            name="synth",
            tool="yosys",
            ok=synth_report.ok,
            summary=synth_report.summary,
        )
    ]
    if not synth_report.ok or synth_report.netlist is None:
        return FpgaBuildReport(
            ok=False,
            family=family,
            device=device,
            package=package,
            speed=speed,
            top=synth_report.top,
            stages=stages,
            job=job.to_ref(),
            error=synth_report.error or ErrorInfo(code="synthesis_failed", message="Synthesis failed."),
            summary="FPGA build stopped after synthesis failed.",
        )
    netlist_path = app.paths.resolve_read(synth_report.netlist.path)
    build_root = app.settings.build_path / "fpga" / job.id
    build_root.mkdir(parents=True, exist_ok=True)
    asc_path = build_root / "design.asc"
    config_path = build_root / "design.config"
    report_path = build_root / "nextpnr-report.json"
    if family == "ice40":
        pnr_args = [
            f"--{device}",
            "--package",
            package or "",
            "--json",
            str(netlist_path),
            "--pcf",
            str(constraint_paths[0]),
            "--freq",
            str(frequency_mhz),
            "--asc",
            str(asc_path),
            "--report",
            str(report_path),
        ]
        if seed is not None:
            pnr_args.extend(["--seed", str(seed)])
        if placer:
            pnr_args.extend(["--placer", placer])
        if router:
            pnr_args.extend(["--router", router])
        if project:
            pnr_args.extend(project.target.nextpnr_opts)
    else:
        pnr_args = [
            f"--{device}",
            "--package",
            package or "",
            "--speed",
            str(speed or "6"),
            "--json",
            str(netlist_path),
            "--lpf",
            str(constraint_paths[0]),
            "--freq",
            str(frequency_mhz),
            "--textcfg",
            str(config_path),
            "--report",
            str(report_path),
        ]
        if seed is not None:
            pnr_args.extend(["--seed", str(seed)])
        if placer:
            pnr_args.extend(["--placer", placer])
        if router:
            pnr_args.extend(["--router", router])
        if project:
            pnr_args.extend(project.target.nextpnr_opts)

    pnr_result = await run_tool(
        app, job, name=pnr_tool, args=pnr_args, cwd=app.workspace, timeout_s=timeout_s, step_name="place-route"
    )
    stages.append(
        StageReport(
            name="place-route",
            tool=pnr_tool,
            ok=pnr_result.exit_code == 0,
            exit_code=pnr_result.exit_code,
            duration_s=pnr_result.duration_s,
            summary=f"{pnr_tool} exited {pnr_result.exit_code}.",
        )
    )
    if pnr_result.exit_code != 0:
        return FpgaBuildReport(
            ok=False,
            family=family,
            device=device,
            package=package,
            speed=speed,
            top=synth_report.top,
            stages=stages,
            job=job.to_ref(),
            error=ErrorInfo(
                code="pnr_failed",
                message=f"{pnr_tool} failed with exit code {pnr_result.exit_code}.",
                hint="Inspect the place-and-route log for timing or constraint errors.",
            ),
            summary="FPGA build stopped after place-and-route failed.",
        )

    bitstream_path = build_root / ("design.bin" if family == "ice40" else "design.bit")
    if family == "ice40":
        pack_args = [str(asc_path), str(bitstream_path)]
        if project:
            pack_args.extend(project.target.pack_opts)
    else:
        pack_args = ["--input", str(config_path), "--bit", str(bitstream_path), "--compress"]
        if project:
            pack_args.extend(project.target.pack_opts)
    pack_result = await run_tool(
        app, job, name=pack_tool, args=pack_args, cwd=app.workspace, timeout_s=timeout_s, step_name="pack"
    )
    stages.append(
        StageReport(
            name="pack",
            tool=pack_tool,
            ok=pack_result.exit_code == 0 and bitstream_path.exists(),
            exit_code=pack_result.exit_code,
            duration_s=pack_result.duration_s,
            summary=f"{pack_tool} exited {pack_result.exit_code}.",
        )
    )
    if pack_result.exit_code != 0 or not bitstream_path.exists():
        return FpgaBuildReport(
            ok=False,
            family=family,
            device=device,
            package=package,
            speed=speed,
            top=synth_report.top,
            stages=stages,
            job=job.to_ref(),
            error=ErrorInfo(code="pack_failed", message=f"{pack_tool} did not produce a bitstream."),
            summary="FPGA build stopped after packing failed.",
        )

    timing, utilization = nextpnr_parser.parse_report(report_path, source="nextpnr")
    if family == "ice40" and suite.has("icetime") and asc_path.exists():
        timing_json = build_root / "icetime.json"
        ice_args = ["-d", device, "-j", str(timing_json), str(asc_path)]
        ice_result = await run_tool(
            app, job, name="icetime", args=ice_args, cwd=app.workspace, timeout_s=timeout_s, step_name="timing"
        )
        if ice_result.exit_code == 0:
            ice_timing = icetime_parser.parse_icetime(
                timing_json,
                read_tail_safe(job, "timing"),
                clock_mhz=frequency_mhz,
            )
            if ice_timing.ok:
                timing = ice_timing
    bitstream = app.artifacts.register(job, bitstream_path, kind="bitstream", description="Packed FPGA bitstream")
    artifacts = [bitstream]
    if report_path.exists():
        artifacts.append(
            app.artifacts.register(job, report_path, kind="report", description="nextpnr timing/utilization report")
        )
    ok = all(stage.ok for stage in stages) and timing.met is not False
    error = None
    if not ok:
        error = ErrorInfo(
            code="timing_failed" if timing.met is False else "fpga_build_failed",
            message="FPGA build completed with timing failure." if timing.met is False else "FPGA build failed.",
            hint="Inspect the timing report and critical paths.",
        )
    return FpgaBuildReport(
        ok=ok,
        family=family,
        device=device,
        package=package,
        speed=speed,
        top=synth_report.top,
        stages=stages,
        utilization=utilization,
        timing=timing,
        timing_met=timing.met,
        bitstream=bitstream,
        job=job.to_ref(),
        artifacts=artifacts,
        error=error,
        summary=(
            f"{family} build {'passed' if ok else 'failed'} for {device}; "
            f"{'timing met' if timing.met else 'timing not met'}"
        ),
    )


def read_tail_safe(job, step_name: str) -> str:
    from oss_cad_mcp.workflows.common import read_tail

    for step in reversed(job.steps):
        if step.name == step_name and step.stdout:
            return read_tail(Path(step.stdout), max_bytes=2_000_000)
    return ""
