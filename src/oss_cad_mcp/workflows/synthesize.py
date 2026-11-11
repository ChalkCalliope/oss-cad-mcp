"""Yosys synthesis workflows."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput, PreflightFailed
from oss_cad_mcp.models.common import ErrorInfo, SourceSet
from oss_cad_mcp.models.results import SynthesisReport
from oss_cad_mcp.parsers import yosys as yosys_parser
from oss_cad_mcp.project import inspect_project, load_project_config, resolve_sources
from oss_cad_mcp.workflows.common import (
    define_args,
    include_args,
    output_path,
    read_tail,
    run_tool,
    work_path,
)

SynthTarget = Literal["generic", "ice40", "ecp5", "gowin", "nexus", "machxo2", "gatemate"]

_SYNTH_COMMANDS = {
    "generic": "synth",
    "ice40": "synth_ice40",
    "ecp5": "synth_ecp5",
    "gowin": "synth_gowin",
    "nexus": "synth_nexus",
    "machxo2": "synth_machxo2",
    "gatemate": "synth_gatemate",
}


def _quote(value: str) -> str:
    return shlex.quote(value)


def _resolve_top(app: AppContext, project, source: SourceSet | None, top: str | None) -> str | None:
    if top:
        return top
    if source and source.top:
        return source.top
    if project and project.top:
        return project.top
    profile = inspect_project(app.workspace, paths=app.paths)
    if profile.top_candidates:
        return profile.top_candidates[0]
    return None


def _plugin_available(app: AppContext, name: str) -> bool:
    suite = app.require_suite()
    return (suite.root / "share" / "yosys" / "plugins" / f"{name}.so").is_file()


async def run_synthesize(
    app: AppContext,
    job,
    *,
    source: SourceSet | None = None,
    top: str | None = None,
    target: SynthTarget = "generic",
    flatten: bool = False,
    abc9: bool = False,
    timeout_s: float = 300.0,
) -> SynthesisReport:
    app.require_suite()
    project, _ = load_project_config(app.workspace)
    try:
        resolved = resolve_sources(source, project=project, paths=app.paths)
    except Exception as exc:
        raise InvalidInput(str(exc), hint="Check source globs and project configuration.") from exc
    if not resolved.files:
        raise InvalidInput("No HDL source files were found for synthesis.")
    top = _resolve_top(app, project, source, top)
    target = target or (project.target.family if project and project.target.family else "generic")  # type: ignore[assignment]
    netlist = output_path(app, job, "synth", f"{top or 'design'}.json")
    stat_path = output_path(app, job, "synth", f"{top or 'design'}.stat.json")
    script_path = work_path(job, "synth.ys")
    yosys_args: list[str] = ["-Q"]
    commands: list[str] = []

    if resolved.language == "vhdl":
        if not _plugin_available(app, "ghdl"):
            raise PreflightFailed(
                "VHDL synthesis requires the Yosys GHDL plugin, which is not installed.",
                hint="Use cad_check with GHDL, or install a suite with the ghdl plugin.",
            )
        yosys_args.extend(["-m", "ghdl"])
        commands.append("ghdl --std=08 " + " ".join(_quote(str(path)) for path in resolved.files))
    elif resolved.language == "mixed" and any(path.suffix.lower() in {".vhd", ".vhdl"} for path in resolved.files):
        raise InvalidInput(
            "Mixed VHDL/Verilog synthesis is not supported in v0.1.",
            hint="Split the flow into separate VHDL and Verilog synthesis runs.",
        )
    else:
        if _plugin_available(app, "slang"):
            yosys_args.extend(["-m", "slang"])
            read_args = [
                "read_slang",
                "--std",
                resolved.std if resolved.std in {"1364-2005", "1800-2017", "1800-2023", "latest"} else "1800-2017",
                *include_args("-I", resolved.include_dirs),
                *define_args("-D", resolved.defines),
                *[_quote(str(path)) for path in resolved.files],
            ]
            commands.append(" ".join(read_args))
        else:
            read_args = [
                "read_verilog",
                "-sv" if resolved.language in {"systemverilog", "mixed"} else "",
                *include_args("-I", resolved.include_dirs),
                *define_args("-D", resolved.defines),
                *[_quote(str(path)) for path in resolved.files],
            ]
            commands.append(" ".join(item for item in read_args if item))

    synth_command = _SYNTH_COMMANDS.get(target)
    if synth_command is None:
        raise InvalidInput(f"Unsupported synthesis target: {target}")
    synth_args = [synth_command]
    if top:
        synth_args.extend(["-top", top])
    else:
        synth_args.append("-auto-top")
    if flatten:
        synth_args.append("-flatten")
    if abc9 and target in {"ice40", "ecp5"}:
        synth_args.append("-abc9")
    synth_args.extend(["-json", _quote(str(netlist))])
    commands.append(" ".join(synth_args))
    commands.append(f"tee -o {_quote(str(stat_path))} stat -json")
    script_path.write_text("\n".join(commands) + "\n", encoding="utf-8")
    yosys_args.extend(["-s", str(script_path)])

    result = await run_tool(
        app,
        job,
        name="yosys",
        args=yosys_args,
        cwd=app.workspace,
        timeout_s=timeout_s,
        step_name="synth",
    )
    log_text = _step_log(job, "synth")
    warnings = [
        diagnostic.message for diagnostic in yosys_parser.parse_log(log_text) if diagnostic.severity == "warning"
    ]
    stat = yosys_parser.parse_stat_json(stat_path)
    summary_stats = yosys_parser.summarize_stat(stat)
    artifacts = []
    if netlist.exists():
        artifacts.append(
            app.artifacts.register(job, netlist, kind="netlist", description="Synthesized Yosys JSON netlist")
        )
    if stat_path.exists():
        artifacts.append(
            app.artifacts.register(job, stat_path, kind="report", description="Yosys synthesis statistics")
        )
    ok = result.exit_code == 0 and netlist.exists()
    error = None
    if not ok:
        error = ErrorInfo(
            code="synthesis_failed",
            message=f"Yosys exited with code {result.exit_code}; netlist {'exists' if netlist.exists() else 'missing'}.",
            hint="Inspect the synthesis job log and the Yosys diagnostics.",
        )
    summary = (
        f"Synthesized {top or 'design'} for {target}: "
        f"{summary_stats.get('num_cells', 0)} cells, {summary_stats.get('num_wires', 0)} wires"
        if ok
        else "Synthesis failed."
    )
    return SynthesisReport(
        ok=ok,
        target=target,
        top=top,
        cells_by_type=summary_stats.get("cells_by_type", {}),
        stats=summary_stats,
        warnings=warnings[:50],
        netlist=artifacts[0] if artifacts else None,
        job=job.to_ref(),
        artifacts=artifacts,
        error=error,
        summary=summary,
    )


def _step_log(job, name: str) -> str:
    for step in reversed(job.steps):
        if step.name == name and step.stdout:
            return read_tail(Path(step.stdout), max_bytes=4_000_000)
    return ""
