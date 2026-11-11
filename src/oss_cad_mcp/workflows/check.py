"""Parse, lint, and elaborate HDL sources."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.models.diagnostics import CheckReport, Diagnostic, HierarchyNode
from oss_cad_mcp.parsers import ghdl as ghdl_parser
from oss_cad_mcp.parsers import iverilog as iverilog_parser
from oss_cad_mcp.parsers import slang as slang_parser
from oss_cad_mcp.parsers import verilator as verilator_parser
from oss_cad_mcp.project import load_project_config, resolve_sources
from oss_cad_mcp.workflows.common import (
    count_diagnostics,
    define_args,
    include_args,
    merge_diagnostics,
    read_tail,
    run_tool,
    source_files,
    work_path,
)

Engine = Literal["auto", "slang", "verilator", "iverilog", "ghdl"]


def _read_log(path: Path, *, limit: int = 4_000_000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as fh:
        data = fh.read(limit)
    return data.decode("utf-8", errors="replace")


def _std_slang(std: str) -> str:
    return std if std in {"1364-2005", "1800-2017", "1800-2023", "latest"} else "1800-2017"


def _iverilog_std(std: str, language: str) -> str:
    if language == "systemverilog" or std.startswith("1800"):
        return "-g2012"
    if std.startswith("1364-2005"):
        return "-g2005"
    return "-g2012"


async def run_check(
    app: AppContext,
    job,
    *,
    source: SourceSet | None = None,
    level: Literal["parse", "lint", "elaborate"] = "lint",
    engines: list[Engine] | None = None,
    top: str | None = None,
    timeout_s: float = 180.0,
) -> CheckReport:
    suite = app.require_suite()
    project, _ = load_project_config(app.workspace)
    try:
        resolved = resolve_sources(source, project=project, paths=app.paths)
    except Exception as exc:
        raise InvalidInput(str(exc), hint="Check source globs and project configuration.") from exc
    if not resolved.files:
        raise InvalidInput(
            "No HDL source files were provided or detected.",
            hint="Pass SourceSet(files=[...]) or add sources to oss-cad-project.toml.",
        )
    top = top or resolved.top
    requested = [item for item in (engines or ["auto"]) if item != "auto"]
    if not requested:
        if resolved.language == "vhdl":
            requested = ["ghdl"]
        elif resolved.language == "verilog":
            requested = ["iverilog"]
        else:
            requested = ["slang"]
    available: list[str] = []
    for engine in requested:
        tool = {"slang": "slang", "verilator": "verilator", "iverilog": "iverilog", "ghdl": "ghdl"}[engine]
        if suite.has(tool):
            available.append(engine)
    if not available:
        raise InvalidInput(
            "None of the requested lint engines are installed.",
            hint="Run cad_status to inspect available tools.",
            details={"requested": requested},
        )

    all_diagnostics: list[Diagnostic] = []
    engine_names: list[str] = []
    warnings: list[str] = []
    hierarchy: list[HierarchyNode] = []
    artifacts = []

    for engine in available:
        if engine == "slang":
            diag_path = work_path(job, f"slang-{job.id}.json")
            args = ["--diag-json", str(diag_path), "--std", _std_slang(resolved.std)]
            if level != "elaborate":
                args.insert(0, "--lint-only")
            args.extend(include_args("-I", resolved.include_dirs))
            args.extend(define_args("-D", resolved.defines))
            args.extend(source_files(resolved))
            result = await run_tool(
                app, job, name="slang", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="slang"
            )
            all_diagnostics.extend(slang_parser.parse_diagnostics(diag_path, base=app.workspace))
            if diag_path.exists():
                artifacts.append(
                    app.artifacts.register(job, diag_path, kind="report", description="slang diagnostic JSON")
                )
            if level == "elaborate":
                hier = await run_tool(
                    app,
                    job,
                    name="slang-hier",
                    args=[
                        "--params",
                        "--std",
                        _std_slang(resolved.std),
                        *include_args("-I", resolved.include_dirs),
                        *define_args("-D", resolved.defines),
                        *source_files(resolved),
                    ],
                    cwd=app.workspace,
                    timeout_s=timeout_s,
                    step_name="slang-hier",
                )
                hierarchy = slang_parser.parse_hierarchy(result_tail(app, job, "slang-hier"))
                if hier.exit_code not in (0, None):
                    warnings.append("slang-hier exited nonzero; hierarchy may be incomplete.")
            engine_names.append("slang")
            if result.timed_out:
                warnings.append("slang timed out.")
            if result.exit_code not in (0, None) and not all_diagnostics:
                warnings.append(f"slang exited with code {result.exit_code}.")
        elif engine == "verilator":
            sarif_path = work_path(job, f"verilator-{job.id}.sarif.json")
            args = [
                "--lint-only",
                "-Wall",
                "-Wno-fatal",
                "--diagnostics-sarif",
                "--diagnostics-sarif-output",
                str(sarif_path),
                "--language",
                _std_slang(resolved.std),
                *include_args("-I", resolved.include_dirs),
                *define_args("-D", resolved.defines),
            ]
            if top:
                args.extend(["--top-module", top])
            args.extend(source_files(resolved))
            result = await run_tool(
                app, job, name="verilator", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="verilator"
            )
            all_diagnostics.extend(verilator_parser.parse_sarif(sarif_path, base=app.workspace))
            if sarif_path.exists():
                artifacts.append(
                    app.artifacts.register(job, sarif_path, kind="report", description="Verilator SARIF diagnostics")
                )
            if not sarif_path.exists():
                all_diagnostics.extend(
                    verilator_parser.parse_text(result_tail(app, job, "verilator"), base=app.workspace)
                )
            engine_names.append("verilator")
            if result.timed_out:
                warnings.append("Verilator timed out.")
        elif engine == "iverilog":
            args = [
                _iverilog_std(resolved.std, resolved.language),
                "-Wall",
                "-o",
                str(work_path(job, "check.vvp")),
                *include_args("-I", resolved.include_dirs),
                *define_args("-D", resolved.defines),
            ]
            if top:
                args.extend(["-s", top])
            args.extend(source_files(resolved))
            result = await run_tool(
                app, job, name="iverilog", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="iverilog"
            )
            all_diagnostics.extend(
                iverilog_parser.parse_iverilog(result_tail(app, job, "iverilog"), base=app.workspace)
            )
            engine_names.append("iverilog")
            if result.timed_out:
                warnings.append("Icarus Verilog timed out.")
        elif engine == "ghdl":
            workdir = work_path(job, "ghdl")
            workdir.mkdir(parents=True, exist_ok=True)
            args = ["-a", "--std=08", f"--workdir={workdir}", *source_files(resolved)]
            result = await run_tool(
                app, job, name="ghdl", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="ghdl"
            )
            all_diagnostics.extend(ghdl_parser.parse_ghdl(result_tail(app, job, "ghdl"), base=app.workspace))
            engine_names.append("ghdl")
            if result.timed_out:
                warnings.append("GHDL timed out.")

    diagnostics = merge_diagnostics(all_diagnostics)
    counts = count_diagnostics(diagnostics)
    if counts.errors == 0 and any(step.exit_code not in (0, None) and not step.timed_out for step in job.steps):
        # A nonzero exit without a parsed diagnostic still means the check failed.
        warnings.append("A lint step exited nonzero without a parseable diagnostic; inspect the step log.")
    ok = counts.errors == 0 and not any(step.timed_out for step in job.steps)
    summary = f"{counts.errors} error(s), {counts.warnings} warning(s) using {', '.join(engine_names)}"
    if level == "elaborate" and hierarchy:
        summary += f"; hierarchy has {len(hierarchy)} root(s)"
    return CheckReport(
        ok=ok,
        level=level,
        engines=engine_names,
        diagnostics=diagnostics,
        counts=counts,
        hierarchy=hierarchy,
        top=top,
        job=job.to_ref(),
        artifacts=artifacts,
        warnings=warnings,
        summary=summary,
    )


def result_tail(app: AppContext, job, step_name: str) -> str:
    for step in reversed(job.steps):
        if step.name == step_name:
            parts = []
            if step.stdout:
                parts.append(read_tail(Path(step.stdout), max_bytes=2_000_000))
            if step.stderr:
                parts.append(read_tail(Path(step.stderr), max_bytes=2_000_000))
            if parts:
                return "\n".join(parts)
    return ""
