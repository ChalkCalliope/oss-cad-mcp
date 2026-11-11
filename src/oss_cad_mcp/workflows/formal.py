"""Formal verification workflows (SBY first, EQY/MCY config fallback)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput, PreflightFailed
from oss_cad_mcp.models.common import ErrorInfo, SourceSet
from oss_cad_mcp.models.results import VerificationReport
from oss_cad_mcp.parsers import sby as sby_parser
from oss_cad_mcp.project import inspect_project, load_project_config, resolve_sources
from oss_cad_mcp.workflows.common import read_tail, run_tool, work_path

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
    if project and project.formal:
        profile = inspect_project(app.workspace, paths=app.paths)
        if profile.top_candidates:
            return profile.top_candidates[0]
    profile = inspect_project(app.workspace, paths=app.paths)
    if profile.testbenches:
        return profile.testbenches[0]
    if profile.top_candidates:
        return profile.top_candidates[0]
    raise InvalidInput("No top module was specified or detected for verification.")


async def run_verify(
    app: AppContext,
    job,
    *,
    mode: VerifyMode = "prove",
    source: SourceSet | None = None,
    top: str | None = None,
    engine: str = "smtbmc",
    depth: int = 20,
    config_file: str | None = None,
    timeout_s: float = 600.0,
    expect_fail: bool = False,
) -> VerificationReport:
    suite = app.require_suite()
    if mode in {"equiv", "mutation"}:
        if not config_file:
            raise InvalidInput(
                f"{mode} verification requires an existing configuration file in v0.1.",
                hint="Pass config_file for EQY (.eqy) or MCY runs.",
            )
        return await _run_config_tool(app, job, mode=mode, config_file=config_file, timeout_s=timeout_s)

    project, _ = load_project_config(app.workspace)
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
                f"mode {mode}",
                f"depth {depth}",
                "" if not expect_fail else "expect fail",
                "[engines]",
                engine,
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

    log_text = _step_log(job, "sby")
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
        status_text = _step_log(job, "sby-status")
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
    error = None
    if not ok and status != "failed":
        error = ErrorInfo(
            code="verification_failed",
            message=f"SBY exited with code {result.exit_code} and status {status}.",
            hint="Inspect the SBY log and the counterexample trace.",
        )
    return VerificationReport(
        ok=ok,
        mode=mode,
        status=status,  # type: ignore[arg-type]
        engine=engine_name or engine,
        depth=depth,
        properties=properties,
        trace=trace,
        log_tail=log_text[-16_384:],
        job=job.to_ref(),
        artifacts=[trace] if trace else [],
        error=error,
        summary=f"Formal {mode} {status} ({len(properties)} property statuses).",
    )


async def _run_config_tool(
    app: AppContext, job, *, mode: str, config_file: str, timeout_s: float
) -> VerificationReport:
    config = app.paths.resolve_read(config_file, must_exist=True)
    tool = "eqy" if mode == "equiv" else "mcy"
    if not app.require_suite().has(tool):
        raise PreflightFailed(f"{tool} is not installed in this suite.")
    args = [str(config)]
    result = await run_tool(app, job, name=tool, args=args, cwd=config.parent, timeout_s=timeout_s, step_name=tool)
    log_text = _step_log(job, tool)
    ok = result.exit_code == 0
    return VerificationReport(
        ok=ok,
        mode=mode,
        status="passed" if ok else "failed",
        engine=tool,
        log_tail=log_text[-16_384:],
        job=job.to_ref(),
        summary=f"{tool} {'passed' if ok else 'failed'} (exit {result.exit_code}).",
        error=None if ok else ErrorInfo(code="verification_failed", message=f"{tool} exited {result.exit_code}."),
    )


def _step_log(job, name: str) -> str:
    for step in reversed(job.steps):
        if step.name == name and step.stdout:
            return read_tail(Path(step.stdout), max_bytes=4_000_000)
    return ""


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
