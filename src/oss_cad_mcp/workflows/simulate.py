"""Compile and run HDL testbenches."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput, PreflightFailed
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.models.diagnostics import Diagnostic
from oss_cad_mcp.models.results import SimulationReport
from oss_cad_mcp.parsers import ghdl as ghdl_parser
from oss_cad_mcp.parsers import iverilog as iverilog_parser
from oss_cad_mcp.parsers import verilator as verilator_parser
from oss_cad_mcp.project import inspect_project, load_project_config, resolve_sources
from oss_cad_mcp.workflows.common import (
    define_args,
    include_args,
    merge_diagnostics,
    read_tail,
    run_tool,
    source_files,
    work_path,
)

SimEngine = Literal["auto", "iverilog", "verilator", "ghdl"]
TraceMode = Literal["auto", "none", "vcd", "fst"]

_FINISH_RE = re.compile(r"\$finish(?:\s+called)?\s+at\s+([0-9.]+)\s*([a-z]*)", re.IGNORECASE)
_FAIL_RE = re.compile(r"(ASSERTION FAILED|SIMULATION FAILED|%Error:|\$fatal|Fatal:)", re.IGNORECASE)


def _resolve_top(app: AppContext, project, source: SourceSet | None, top: str | None) -> str:
    if top:
        return top
    if source and source.top:
        return source.top
    if project and project.sim.testbench:
        return project.sim.testbench
    profile = inspect_project(app.workspace, paths=app.paths)
    if profile.testbenches:
        return profile.testbenches[0]
    if profile.top_candidates:
        return profile.top_candidates[0]
    raise InvalidInput(
        "No testbench top module was specified or detected.",
        hint="Pass top='tb_name' or set [sim].testbench in oss-cad-project.toml.",
    )


def _trace_mode(requested: TraceMode, engine: str) -> str:
    if requested == "auto":
        return "vcd"
    return requested


def _has_dump(text: str) -> bool:
    return "$dumpfile" in text or "$dumpvars" in text or "$fst" in text


async def run_simulate(
    app: AppContext,
    job,
    *,
    source: SourceSet | None = None,
    top: str | None = None,
    engine: SimEngine = "auto",
    trace: TraceMode = "auto",
    plusargs: list[str] | None = None,
    timeout_s: float = 180.0,
    seed: int | None = None,
    expect_fail: bool = False,
) -> SimulationReport:
    suite = app.require_suite()
    project, _ = load_project_config(app.workspace)
    try:
        resolved = resolve_sources(source, project=project, paths=app.paths, include_testbenches=True)
    except Exception as exc:
        raise InvalidInput(str(exc), hint="Check source globs and project configuration.") from exc
    if not resolved.files:
        raise InvalidInput("No HDL source files were found for simulation.")
    top = _resolve_top(app, project, source, top)
    selected = engine
    if selected == "auto":
        if resolved.language == "vhdl":
            selected = "ghdl"
        elif resolved.language == "systemverilog":
            selected = "verilator" if suite.has("verilator") and suite.host.cxx else "iverilog"
        else:
            selected = "iverilog" if suite.has("iverilog") else "verilator"
    if selected == "verilator" and not suite.host.cxx:
        raise PreflightFailed(
            "Verilator simulation requires a host C++ compiler.",
            hint="Install g++/clang++, or use engine='iverilog'.",
        )
    trace_mode = _trace_mode(trace, selected)
    combined_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in resolved.files)
    dump_detected = _has_dump(combined_text)
    warnings: list[str] = []
    diagnostics: list[Diagnostic] = []
    artifacts = []

    if selected == "verilator":
        mdir = work_path(job, "obj_dir")
        mdir.mkdir(parents=True, exist_ok=True)
        sarif = work_path(job, "verilator-sim.sarif.json")
        args = [
            "--binary",
            "--timing",
            "-j",
            "0",
            "--Mdir",
            str(mdir),
            "--top-module",
            top,
            "-o",
            "sim",
            "--diagnostics-sarif",
            "--diagnostics-sarif-output",
            str(sarif),
            "--language",
            "1800-2017" if resolved.language in {"systemverilog", "mixed"} else "1364-2005",
            "-Wno-fatal",
            *include_args("-I", resolved.include_dirs),
            *define_args("-D", resolved.defines),
        ]
        if trace_mode in {"fst", "vcd"}:
            # Build VCD in the simulation, then optionally convert to FST with
            # the suite's vcd2fst binary. This avoids requiring host lz4 headers
            # for Verilator's --trace-fst compile path.
            args.append("--trace")
        args.extend(source_files(resolved))
        compile_result = await run_tool(
            app, job, name="verilator", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="verilator-build"
        )
        diagnostics.extend(verilator_parser.parse_sarif(sarif, base=app.workspace))
        if not sarif.exists():
            diagnostics.extend(
                verilator_parser.parse_text(
                    read_tail(Path(job.steps[-1].stderr), max_bytes=2_000_000)
                    + "\n"
                    + read_tail(Path(job.steps[-1].stdout), max_bytes=2_000_000),
                    base=app.workspace,
                )
            )
        if compile_result.exit_code not in (0, None):
            return _simulation_failure(
                job,
                selected,
                top,
                diagnostics,
                warnings,
                compile_result.exit_code,
                "Verilator compilation failed.",
            )
        executable = mdir / ("sim.exe" if os.name == "nt" else "sim")
        if not executable.exists():
            return _simulation_failure(
                job,
                selected,
                top,
                diagnostics,
                warnings,
                None,
                "Verilator did not produce a simulation executable.",
            )
        run_result = await run_tool(
            app,
            job,
            name="sim",
            executable=executable,
            args=[*(plusargs or [])],
            cwd=mdir,
            timeout_s=timeout_s,
            step_name="simulate",
        )
        engine_label = "verilator"
    elif selected == "iverilog":
        vvp_path = work_path(job, "sim.vvp")
        args = [
            "-g2012" if resolved.language in {"systemverilog", "mixed"} else "-g2005",
            "-Wall",
            "-o",
            str(vvp_path),
            "-s",
            top,
            *include_args("-I", resolved.include_dirs),
            *define_args("-D", resolved.defines),
            *source_files(resolved),
        ]
        compile_result = await run_tool(
            app, job, name="iverilog", args=args, cwd=app.workspace, timeout_s=timeout_s, step_name="iverilog-build"
        )
        diagnostics.extend(
            iverilog_parser.parse_iverilog(
                read_tail(Path(job.steps[-1].stdout), max_bytes=2_000_000)
                + "\n"
                + read_tail(Path(job.steps[-1].stderr), max_bytes=2_000_000),
                base=app.workspace,
            )
        )
        if compile_result.exit_code not in (0, None):
            return _simulation_failure(
                job,
                selected,
                top,
                diagnostics,
                warnings,
                compile_result.exit_code,
                "Icarus Verilog compilation failed.",
            )
        run_args = ["-n", str(vvp_path), *(plusargs or [])]
        if seed is not None:
            run_args.append(f"+seed={seed}")
        run_result = await run_tool(
            app, job, name="vvp", args=run_args, cwd=work_path(job, "."), timeout_s=timeout_s, step_name="simulate"
        )
        engine_label = "iverilog"
    else:
        workdir = work_path(job, "ghdl")
        workdir.mkdir(parents=True, exist_ok=True)
        analyze_args = ["-a", "--std=08", f"--workdir={workdir}", *source_files(resolved)]
        analyze = await run_tool(
            app, job, name="ghdl", args=analyze_args, cwd=app.workspace, timeout_s=timeout_s, step_name="ghdl-analyze"
        )
        text = read_tail(Path(job.steps[-1].stdout), max_bytes=2_000_000)
        diagnostics.extend(ghdl_parser.parse_ghdl(text, base=app.workspace))
        if analyze.exit_code not in (0, None):
            return _simulation_failure(
                job, selected, top, diagnostics, warnings, analyze.exit_code, "GHDL analysis failed."
            )
        elaborate_args = ["-e", "--std=08", f"--workdir={workdir}", top]
        elaborate = await run_tool(
            app,
            job,
            name="ghdl",
            args=elaborate_args,
            cwd=app.workspace,
            timeout_s=timeout_s,
            step_name="ghdl-elaborate",
        )
        if elaborate.exit_code not in (0, None):
            return _simulation_failure(
                job, selected, top, diagnostics, warnings, elaborate.exit_code, "GHDL elaboration failed."
            )
        wave = work_path(job, "wave.vcd")
        run_args = ["-r", "--std=08", f"--workdir={workdir}", top, f"--vcd={wave}", *(plusargs or [])]
        run_result = await run_tool(
            app, job, name="ghdl", args=run_args, cwd=work_path(job, "."), timeout_s=timeout_s, step_name="simulate"
        )
        engine_label = "ghdl"

    if not dump_detected:
        warnings.append("The testbench does not appear to call $dumpfile/$dumpvars; no waveform may be produced.")
    stdout_text = _step_text(job, "simulate", "stdout")
    stderr_text = _step_text(job, "simulate", "stderr")
    combined = stdout_text + "\n" + stderr_text
    ok = run_result.exit_code == 0 and not _FAIL_RE.search(combined)
    if expect_fail:
        ok = not ok
    finish = _FINISH_RE.search(combined)
    finish_reason = finish.group(0) if finish else ("timeout" if run_result.timed_out else None)
    simulated_time = f"{finish.group(1)} {finish.group(2)}".strip() if finish else None
    waveform = _find_waveform(job, trace_mode, app)
    if waveform is not None:
        artifacts.append(waveform)
    if trace_mode not in {"none", "auto"} and waveform is None:
        warnings.append(f"No {trace_mode.upper()} waveform was found after simulation.")
    summary = f"{engine_label} simulation {'passed' if ok else 'failed'} for top {top} (exit {run_result.exit_code})"
    if run_result.timed_out:
        summary = f"{engine_label} simulation timed out for top {top}"
    return SimulationReport(
        ok=ok,
        engine=engine_label,
        top=top,
        exit_code=run_result.exit_code,
        finish_reason=finish_reason,
        simulated_time=simulated_time,
        stdout_tail=stdout_text[-16_384:],
        stderr_tail=stderr_text[-16_384:],
        diagnostics=merge_diagnostics(diagnostics),
        waveform=waveform,
        job=job.to_ref(),
        artifacts=artifacts,
        warnings=warnings,
        summary=summary,
    )


def _step_text(job, step_name: str, stream: str) -> str:
    for step in reversed(job.steps):
        if step.name == step_name:
            path = getattr(step, stream)
            if path:
                return read_tail(Path(path), max_bytes=2_000_000)
    return ""


def _find_waveform(job, trace_mode: str, app: AppContext):
    if trace_mode == "none":
        return None
    suffixes = {".vcd", ".fst", ".ghw"} if trace_mode == "auto" else {f".{trace_mode}"}
    candidates = [path for path in job.work_dir.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]
    if not candidates and trace_mode == "fst":
        vcds = [path for path in job.work_dir.rglob("*.vcd") if path.is_file()]
        if vcds:
            source = max(vcds, key=lambda path: path.stat().st_mtime)
            target = source.with_suffix(".fst")
            try:
                import subprocess

                suite = app.require_suite()
                subprocess.run(
                    [str(suite.binary("vcd2fst")), str(source), str(target)],
                    env=suite.run_env(),
                    check=False,
                    timeout=60,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                candidates = [target] if target.exists() else []
            except Exception:
                candidates = [source]
    if not candidates:
        return None
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    return app.artifacts.register(job, path, kind="waveform", description="Simulation waveform")


def _simulation_failure(job, engine, top, diagnostics, warnings, exit_code, summary) -> SimulationReport:
    return SimulationReport(
        ok=False,
        engine=engine,
        top=top,
        exit_code=exit_code,
        diagnostics=merge_diagnostics(diagnostics),
        job=job.to_ref(),
        warnings=warnings,
        summary=summary,
    )
