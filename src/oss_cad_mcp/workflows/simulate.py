"""Compile and run HDL testbenches."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import CadError, InvalidInput, PreflightFailed
from oss_cad_mcp.models.common import ErrorInfo, SourceSet
from oss_cad_mcp.models.diagnostics import Diagnostic
from oss_cad_mcp.models.results import SimulationReport
from oss_cad_mcp.parsers import ghdl as ghdl_parser
from oss_cad_mcp.parsers import iverilog as iverilog_parser
from oss_cad_mcp.parsers import verilator as verilator_parser
from oss_cad_mcp.project import inspect_project, load_project_config, resolve_sources, unit_instantiates
from oss_cad_mcp.workflows.common import (
    define_args,
    include_args,
    merge_diagnostics,
    read_tail,
    run_tool,
    source_files,
    step_text,
    work_path,
)

SimEngine = Literal["auto", "iverilog", "verilator", "ghdl"]
TraceMode = Literal["auto", "none", "vcd", "fst"]

_FINISH_RE = re.compile(r"\$finish(?:\s+called)?\s+at\s+([0-9.]+)\s*([a-z]*)", re.IGNORECASE)
_FAIL_RE = re.compile(
    r"(ASSERTION FAILED|SIMULATION FAILED|TESTBENCH FAILED|TEST FAILED|%Error:|\$fatal|Fatal:)",
    re.IGNORECASE,
)


def _resolve_top(app: AppContext, project, source: SourceSet | None, top: str | None) -> str:
    if top:
        return top
    if source and source.top:
        return source.top
    if project and project.sim.testbench:
        return project.sim.testbench
    profile = inspect_project(app.workspace, paths=app.paths)
    if profile.testbenches:
        candidate_top = profile.top_candidates[0] if profile.top_candidates else None
        if candidate_top:
            matching = [
                name
                for name in profile.testbenches
                if name in profile.testbench_units
                and unit_instantiates(app.paths.resolve_read(profile.testbench_units[name]), candidate_top)
            ]
            if len(matching) == 1:
                return matching[0]
            for pattern in (f"tb_{candidate_top}", f"{candidate_top}_tb"):
                if pattern in matching:
                    return pattern
        if len(profile.testbenches) == 1:
            return profile.testbenches[0]
        raise InvalidInput(
            f"Multiple testbenches were detected: {', '.join(profile.testbenches)}",
            hint="Pass top='tb_name' or set [sim].testbench in oss-cad-project.toml.",
        )
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
    except CadError:
        raise
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
    # GHDL is always asked for a VCD by this workflow; it does not use the
    # Verilog $dumpfile/$dumpvars convention.
    dump_detected = selected == "ghdl" or _has_dump(combined_text)
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
        run_args = list(plusargs or [])
        if seed is not None:
            if any(arg.startswith("+seed=") for arg in run_args):
                warnings.append("seed was ignored because plusargs already contains +seed=.")
            else:
                run_args.append(f"+seed={seed}")
                run_args.append(f"+verilator+seed+{seed}")
        run_result = await run_tool(
            app,
            job,
            name="sim",
            executable=executable,
            args=run_args,
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
            if any(arg.startswith("+seed=") for arg in run_args):
                warnings.append("seed was ignored because plusargs already contains +seed=.")
            else:
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
        analyze_text = step_text(job, "ghdl-analyze")
        diagnostics.extend(ghdl_parser.parse_ghdl(analyze_text, base=app.workspace))
        if analyze.exit_code not in (0, None):
            return _simulation_failure(
                job, selected, top, diagnostics, warnings, analyze.exit_code, "GHDL analysis failed."
            )
        elaborate_args = [
            "-e",
            "--std=08",
            f"--workdir={workdir}",
            "-o",
            str(workdir / "sim"),
            top,
        ]
        elaborate = await run_tool(
            app,
            job,
            name="ghdl",
            args=elaborate_args,
            cwd=app.workspace,
            timeout_s=timeout_s,
            step_name="ghdl-elaborate",
        )
        elaborate_text = step_text(job, "ghdl-elaborate")
        if elaborate_text:
            diagnostics.extend(ghdl_parser.parse_ghdl(elaborate_text, base=app.workspace))
        if elaborate.exit_code not in (0, None):
            return _simulation_failure(
                job, selected, top, diagnostics, warnings, elaborate.exit_code, "GHDL elaboration failed."
            )
        wave = work_path(job, "wave.vcd")
        run_args = [f"--vcd={wave}", *(plusargs or [])]
        if seed is not None:
            warnings.append("seed is not supported by GHDL; use a testbench generic or engine-specific option.")
        run_result = await run_tool(
            app,
            job,
            name="sim",
            executable=workdir / "sim",
            args=run_args,
            cwd=workdir,
            timeout_s=timeout_s,
            step_name="simulate",
        )
        engine_label = "ghdl"

    if not dump_detected:
        warnings.append("The testbench does not appear to call $dumpfile/$dumpvars; no waveform may be produced.")
    stdout_text = _step_text(job, "simulate", "stdout")
    stderr_text = _step_text(job, "simulate", "stderr")
    combined = stdout_text + "\n" + stderr_text
    timed_out = run_result.timed_out
    failed = run_result.exit_code not in (0, None) or bool(_FAIL_RE.search(combined))
    if timed_out:
        # A timeout is never a passing outcome, even when expect_fail is set.
        ok = False
    elif expect_fail:
        ok = failed
    else:
        ok = not failed
    finish = _FINISH_RE.search(combined)
    finish_reason = finish.group(0).strip() if finish else ("timeout" if timed_out else None)
    simulated_time = f"{finish.group(1)} {finish.group(2)}".strip() if finish else None
    if timed_out:
        _discard_waveforms(job)
        warnings.append("Simulation timed out; partial waveform files were discarded.")
        waveform = None
    else:
        waveform = _find_waveform(job, trace_mode, app, warnings=warnings)
    if waveform is not None:
        artifacts.append(waveform)
    if not timed_out and trace_mode not in {"none", "auto"} and waveform is None:
        warnings.append(f"No {trace_mode.upper()} waveform was found after simulation.")
    summary = f"{engine_label} simulation {'passed' if ok else 'failed'} for top {top} (exit {run_result.exit_code})"
    error = None
    if timed_out:
        summary = f"{engine_label} simulation timed out for top {top}"
        error = ErrorInfo(
            code="simulation_timeout",
            message=f"{engine_label} simulation exceeded timeout_s for top {top}.",
            hint="Increase timeout_s or make the testbench terminate with $finish/std.env.finish.",
            retryable=True,
        )
    return SimulationReport(
        ok=ok,
        engine=engine_label,
        top=top,
        exit_code=run_result.exit_code,
        timed_out=timed_out,
        finish_reason=finish_reason,
        simulated_time=simulated_time,
        stdout_tail=stdout_text[-16_384:],
        stderr_tail=stderr_text[-16_384:],
        diagnostics=merge_diagnostics(diagnostics),
        waveform=waveform,
        job=job.to_ref(),
        artifacts=artifacts,
        warnings=warnings,
        error=error,
        summary=summary,
    )


def _step_text(job, step_name: str, stream: str) -> str:
    for step in reversed(job.steps):
        if step.name == step_name:
            path = getattr(step, stream)
            if path:
                return read_tail(Path(path), max_bytes=2_000_000)
    return ""


def _find_waveform(job, trace_mode: str, app: AppContext, *, warnings: list[str] | None = None):
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
    limit = app.settings.max_waveform_bytes
    if limit and path.stat().st_size > limit:
        if warnings is not None:
            warnings.append(
                f"Waveform {path.name} is {path.stat().st_size} bytes, above max_waveform_bytes={limit}; "
                "the artifact was not registered. Increase the setting if this is intentional."
            )
        return None
    return app.artifacts.register(job, path, kind="waveform", description="Simulation waveform")


def _discard_waveforms(job) -> None:
    """Remove partial waveform files after a timed-out simulation."""
    for path in job.work_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".vcd", ".fst", ".ghw", ".lxt", ".lxt2", ".vzt"}:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def _simulation_failure(job, engine, top, diagnostics, warnings, exit_code, summary) -> SimulationReport:
    stdout = _step_text(job, "simulate", "stdout")
    stderr = _step_text(job, "simulate", "stderr")
    if not stdout and not stderr and job.steps:
        last = job.steps[-1]
        stdout = read_tail(Path(last.stdout), max_bytes=2_000_000) if last.stdout else ""
        stderr = read_tail(Path(last.stderr), max_bytes=2_000_000) if last.stderr else ""
    return SimulationReport(
        ok=False,
        engine=engine,
        top=top,
        exit_code=exit_code,
        stdout_tail=stdout[-16_384:],
        stderr_tail=stderr[-16_384:],
        diagnostics=merge_diagnostics(diagnostics),
        job=job.to_ref(),
        warnings=warnings,
        error=ErrorInfo(
            code="simulation_compile_failed",
            message=summary,
            hint="Inspect the compiler diagnostics and the job step log.",
        ),
        summary=summary,
    )
