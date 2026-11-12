"""Formal verification workflows (SBY first, EQY/MCY config fallback)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import CadError, InvalidInput, PreflightFailed
from oss_cad_mcp.models.common import ErrorInfo, SourceSet
from oss_cad_mcp.models.project import FormalConfig
from oss_cad_mcp.models.results import VerificationReport
from oss_cad_mcp.parsers import sby as sby_parser
from oss_cad_mcp.project import inspect_project, load_project_config, resolve_sources
from oss_cad_mcp.workflows.common import run_tool, step_text, work_path

VerifyMode = Literal["prove", "bmc", "cover", "equiv", "mutation"]


def _quote(value: str) -> str:
    if any(ch.isspace() or ch in "\"'" for ch in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _resolve_top(app: AppContext, project, source: SourceSet | None, top: str | None) -> str:
    if top:
        return top
    if source and source.top:
        return source.top
    if project and project.formal and project.formal.top:
        return project.formal.top
    if project and project.top:
        return project.top
    profile = inspect_project(app.workspace, paths=app.paths)
    if profile.formal_units:
        return profile.formal_units[0]
    if profile.top_candidates:
        return profile.top_candidates[0]
    if profile.testbenches:
        return profile.testbenches[0]
    raise InvalidInput("No top module was specified or detected for verification.")


async def run_verify(
    app: AppContext,
    job,
    *,
    mode: VerifyMode | None = None,
    source: SourceSet | None = None,
    top: str | None = None,
    engine: str | None = None,
    depth: int | None = None,
    config_file: str | None = None,
    timeout_s: float = 600.0,
    expect_fail: bool = False,
) -> VerificationReport:
    suite = app.require_suite()
    project, _ = load_project_config(app.workspace)
    if mode is None and config_file:
        suffix = Path(config_file).suffix.lower()
        if suffix == ".eqy":
            mode = "equiv"
        elif suffix == ".mcy":
            mode = "mutation"
    formal_cfg = project.formal if project else FormalConfig()
    resolved_mode: VerifyMode = mode or formal_cfg.mode
    resolved_depth = depth or formal_cfg.depth
    resolved_engine = engine or formal_cfg.engine
    if resolved_mode in {"equiv", "mutation"}:
        if not config_file:
            raise InvalidInput(
                f"{resolved_mode} verification requires an existing configuration file in v0.1.",
                hint="Pass config_file for EQY (.eqy) or MCY runs.",
            )
        return await _run_config_tool(app, job, mode=resolved_mode, config_file=config_file, timeout_s=timeout_s)

    if (source is None or (not source.files and not source.filelist)) and project and project.formal.sources:
        source = SourceSet(files=list(project.formal.sources), language=project.language)
    if config_file:
        config = app.paths.resolve_read(config_file, must_exist=True)
        run_dir = work_path(job, "sby")
        run_dir.mkdir(parents=True, exist_ok=True)
        args = ["-d", str(run_dir), str(config)]
        result = await run_tool(
            app, job, name="sby", args=args, cwd=config.parent, timeout_s=timeout_s, step_name="sby"
        )
        work_root = run_dir
    else:
        if not suite.has("sby"):
            raise PreflightFailed("SBY is not installed in this suite.")
        try:
            resolved = resolve_sources(source, project=project, paths=app.paths)
        except CadError:
            raise
        except Exception as exc:
            raise InvalidInput(str(exc), hint="Check source globs and project configuration.") from exc
        if not resolved.files:
            raise InvalidInput("No HDL source files were found for formal verification.")
        top = _resolve_top(app, project, source, top)
        if resolved.language not in {"verilog", "systemverilog", "mixed"}:
            raise InvalidInput(
                "SBY generation currently supports Verilog/SystemVerilog sources.",
                hint="Pass an existing .sby config for other flows.",
            )
        sby_path = work_path(job, "formal.sby")
        script_lines = []
        read_command = "read -formal -sv" if resolved.language in {"systemverilog", "mixed"} else "read -formal"
        read_line = " ".join(
            [
                read_command,
                *[f"-I{path}" for path in resolved.include_dirs],
                *[f"-D{name}={value}" for name, value in sorted(resolved.defines.items())],
                *[_quote(str(path)) for path in resolved.files],
            ]
        )
        script_lines.append(read_line)
        script_lines.append(f"prep -top {top}")
        config_text = "\n".join(
            [
                "[options]",
                f"mode {resolved_mode}",
                f"depth {resolved_depth}",
                "" if not expect_fail else "expect fail",
                "[engines]",
                resolved_engine,
                "",
                "[script]",
                *script_lines,
                "",
                "[files]",
                *[str(path) for path in resolved.files],
                "",
            ]
        )
        sby_path.write_text(config_text, encoding="utf-8")
        run_dir = work_path(job, "sby-run")
        run_dir.mkdir(parents=True, exist_ok=True)
        result = await run_tool(
            app,
            job,
            name="sby",
            args=["-d", str(run_dir), str(sby_path)],
            cwd=work_path(job, "."),
            timeout_s=timeout_s,
            step_name="sby",
        )
        work_root = run_dir

    log_text = step_text(job, "sby")
    status, engine_name = sby_parser.parse_summary(log_text)
    properties = []
    status_result = await run_tool(
        app,
        job,
        name="sby",
        args=["--status", "--statusfmt", "jsonl", str(work_root)],
        cwd=work_root,
        timeout_s=60.0,
        step_name="sby-status",
    )
    if status_result.exit_code == 0:
        status_text = step_text(job, "sby-status")
        properties = sby_parser.parse_status_jsonl(status_text)
    if not properties:
        status_jsonl = _status_jsonl(work_root)
        if status_jsonl:
            properties = sby_parser.parse_status_jsonl(status_jsonl)
    if properties and status == "unknown":
        if any(prop.status == "FAIL" for prop in properties):
            status = "failed"
        elif all(prop.status == "PASS" for prop in properties):
            status = "passed"
    ok = True if expect_fail and status == "failed" else status == "passed"
    if result.timed_out:
        ok = False
        status = "error"
    trace_path = sby_parser.find_trace(work_root)
    trace = (
        app.artifacts.register(job, trace_path, kind="waveform", description="Formal counterexample trace")
        if trace_path
        else None
    )
    assert_failures = [prop for prop in properties if prop.status == "FAIL" and (prop.kind or "").upper() != "COVER"]
    cover_failures = [prop for prop in properties if prop.status != "PASS" and (prop.kind or "").upper() == "COVER"]
    error = None
    summary = f"Formal {resolved_mode} {status} ({len(properties)} property statuses)."
    if not ok and status == "failed" and cover_failures and not assert_failures:
        summary = f"Formal {resolved_mode} failed: {len(cover_failures)} cover(s) unreachable."
        error = ErrorInfo(
            code="verification_failed",
            message=summary,
            hint="Covers are reachability goals; add constraints or reduce the depth if they should be reachable.",
        )
    elif not ok and status == "failed" and assert_failures:
        summary = f"Formal {resolved_mode} failed: {len(assert_failures)} assertion(s) failed."
        error = ErrorInfo(
            code="verification_failed",
            message=summary,
            hint="Inspect the counterexample trace and the SBY log.",
        )
    elif not ok and status != "failed":
        error = ErrorInfo(
            code="verification_failed",
            message=f"SBY exited with code {result.exit_code} and status {status}.",
            hint="Inspect the SBY log and the counterexample trace.",
        )
    return VerificationReport(
        ok=ok,
        mode=resolved_mode,
        status=status,  # type: ignore[arg-type]
        engine=engine_name or resolved_engine,
        depth=resolved_depth,
        properties=properties,
        trace=trace,
        log_tail=log_text[-16_384:],
        job=job.to_ref(),
        artifacts=[trace] if trace else [],
        error=error,
        summary=summary,
    )


async def _run_config_tool(
    app: AppContext, job, *, mode: str, config_file: str, timeout_s: float
) -> VerificationReport:
    config = app.paths.resolve_read(config_file, must_exist=True)
    if mode == "mutation":
        return VerificationReport(
            ok=False,
            mode=mode,
            status="error",
            engine="mcy",
            job=job.to_ref(),
            error=ErrorInfo(
                code="not_supported",
                message="MCY mutation verification is not supported by cad_verify in v0.1.",
                hint=("Run mcy init/run yourself with cad_tool_run, or use an SBY harness for property verification."),
            ),
            summary="MCY mutation verification is not supported in v0.1.",
        )
    tool = "eqy"
    if not app.require_suite().has(tool):
        raise PreflightFailed(f"{tool} is not installed in this suite.")
    run_dir = work_path(job, "eqy")
    args = ["-d", str(run_dir), "-f", str(config)]
    result = await run_tool(app, job, name=tool, args=args, cwd=config.parent, timeout_s=timeout_s, step_name=tool)
    log_text = step_text(job, tool)
    pass_file = run_dir / "PASS"
    fail_file = run_dir / "FAIL"
    if pass_file.exists():
        status = "passed"
    elif fail_file.exists():
        status = "failed"
    else:
        status = "passed" if result.exit_code == 0 else "failed"
    ok = result.exit_code == 0 and status == "passed"
    artifacts = []
    logfile = run_dir / "logfile.txt"
    if logfile.exists():
        artifacts.append(app.artifacts.register(job, logfile, kind="report", description="EQY proof log"))
    error = None
    if not ok:
        error = ErrorInfo(
            code="verification_failed",
            message=f"EQY {status} (exit {result.exit_code}).",
            hint="Inspect the EQY log and the work directory for partition details.",
            details={"work_dir": str(run_dir), "log_tail": log_text[-4_000:]},
        )
    return VerificationReport(
        ok=ok,
        mode=mode,
        status=status,  # type: ignore[arg-type]
        engine=tool,
        log_tail=log_text[-16_384:],
        job=job.to_ref(),
        artifacts=artifacts,
        error=error,
        summary=f"{tool} {status} (exit {result.exit_code}).",
    )


def _status_jsonl(work_root: Path) -> str:
    # sby stores its status database under the work directory; try the common
    # status.jsonl path produced by --statusfmt jsonl in newer versions.
    for name in ("status.jsonl", "status.json"):
        path = work_root / name
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
    return ""
