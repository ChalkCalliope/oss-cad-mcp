"""Status, project, and catalog/dispatcher tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli_w
from mcp.server.mcpserver import Context

from oss_cad_mcp import API_VERSION, __version__
from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import CadError, PathNotAllowed, ToolNotFound
from oss_cad_mcp.models.common import (
    CapabilityReport,
    ErrorInfo,
    HardwareInfo,
    StatusReport,
    ToolchainInfo,
    ToolHelp,
)
from oss_cad_mcp.models.results import ProjectActionReport, ToolRunReport, ToolSearchResult
from oss_cad_mcp.project import find_config_file, inspect_project
from oss_cad_mcp.suite import host_info
from oss_cad_mcp.tools.schemas import (
    ProjectRequest,
    StatusRequest,
    ToolHelpRequest,
    ToolRunRequest,
    ToolSearchRequest,
)
from oss_cad_mcp.workflows.common import read_tail


def get_app(ctx: Context) -> AppContext:
    app = ctx.request_context.lifespan_context
    if not isinstance(app, AppContext):
        raise RuntimeError("Server lifespan did not provide an AppContext")
    return app


def _error(exc: Exception) -> ErrorInfo:
    if isinstance(exc, CadError):
        return exc.to_info()
    return ErrorInfo(code="internal_error", message=str(exc))


def _blocked_job(app: AppContext, *, summary: str, error: ErrorInfo):
    return app.jobs.blocked("tool", summary=summary, error=error).to_ref()


def _hardware_info() -> HardwareInfo:
    devices: list[str] = []
    usb_root = Path("/dev/bus/usb")
    if usb_root.exists():
        devices.extend(str(path) for path in usb_root.rglob("*") if path.is_file())
    sys_root = Path("/sys/bus/usb/devices")
    if sys_root.exists():
        devices.extend(path.name for path in sys_root.iterdir() if path.is_dir())
    return HardwareInfo(
        usb_visible=bool(devices),
        devices=devices[:20],
        note="Hardware programming is out of scope for v0.1; the suite is used for software flows only.",
    )


def _capabilities(app: AppContext) -> CapabilityReport:
    suite = app.suite
    if suite is None:
        return CapabilityReport()
    host = suite.host
    return CapabilityReport(
        synthesis=suite.has("yosys"),
        simulation=suite.has("iverilog") or (suite.has("verilator") and bool(host.cxx)),
        formal=suite.has("sby"),
        ice40=suite.has("nextpnr-ice40") and suite.has("icepack"),
        ecp5=suite.has("nextpnr-ecp5") and suite.has("ecppack"),
        gowin=suite.has("nextpnr-himbaechel") and suite.has("gowin_pack"),
        vhdl=suite.has("ghdl") and (suite.root / "share" / "yosys" / "plugins" / "ghdl.so").is_file(),
        verilator=suite.has("verilator") and bool(host.cxx),
        iverilog=suite.has("iverilog") and suite.has("vvp"),
        waveform=suite.has("fst2vcd") and suite.has("vcd2fst"),
        fpga_build=(
            (suite.has("nextpnr-ice40") and suite.has("icepack"))
            or (suite.has("nextpnr-ecp5") and suite.has("ecppack"))
        ),
    )


def build_status(app: AppContext, *, deep: bool = False) -> StatusReport:
    host = host_info()
    warnings: list[str] = []
    if app.suite is None:
        return StatusReport(
            ok=False,
            package_version=__version__,
            api_version=API_VERSION,
            platform=host.release,
            platform_status="unverified",
            wsl=host.wsl,
            workspace=str(app.workspace),
            profile=app.settings.profile,
            toolchain=ToolchainInfo(
                cxx=host.cxx,
                make=host.make,
                git=host.git,
                display=host.display,
                wsl=host.wsl,
            ),
            hardware=_hardware_info(),
            warnings=["OSS CAD Suite was not found."],
            error=ErrorInfo(
                code="suite_not_found",
                message=app.suite_error or "OSS CAD Suite was not found.",
                hint="Set OSS_CAD_SUITE_ROOT or pass --suite-root.",
            ),
        )
    suite = app.suite
    if not suite.verified_platform:
        warnings.append("This platform is not the verified WSL2 x86_64 configuration.")
    if host.wsl and app.workspace.as_posix().startswith("/mnt/"):
        warnings.append("Workspace is on a mounted Windows filesystem; use the Linux filesystem for performance.")
    if not host.cxx:
        warnings.append("No C++ compiler found; Verilator simulation is unavailable.")
    if not suite.verified_platform:
        warnings.append("macOS and native Linux are not verified for full support.")
    warnings.append("Hardware programming is disabled in v0.1.")
    sandbox_available = False
    sandbox_note: str | None = None
    if deep:
        sandbox_available, sandbox_note = suite.sandbox_available()
        if not sandbox_available:
            warnings.append("bwrap sandbox is unavailable in this environment; running unsandboxed.")
    return StatusReport(
        ok=True,
        package_version=__version__,
        api_version=API_VERSION,
        platform=suite.platform_label,
        platform_status="verified" if suite.verified_platform else "unverified",
        wsl=host.wsl,
        suite_root=str(suite.root),
        suite_version=suite.version,
        workspace=str(app.workspace),
        profile=app.settings.profile,
        tools_present=app.registry.count() if app.registry else 0,
        tools_missing=[
            name
            for name in ("yosys", "verilator", "iverilog", "slang", "sby", "nextpnr-ice40", "icepack")
            if not suite.has(name)
        ],
        capabilities=_capabilities(app),
        toolchain=ToolchainInfo(
            cxx=host.cxx,
            make=host.make,
            git=host.git,
            display=host.display,
            wsl=host.wsl,
        ),
        hardware=_hardware_info(),
        sandbox_available=sandbox_available,
        sandbox_note=sandbox_note,
        warnings=warnings,
    )


async def cad_status(ctx: Context, request: StatusRequest) -> StatusReport:
    """Check the OSS CAD Suite installation, workspace, and tool capabilities."""
    return build_status(get_app(ctx), deep=request.deep)


async def cad_project(ctx: Context, request: ProjectRequest) -> ProjectActionReport:
    """Inspect, initialize, or validate an HDL project."""
    app = get_app(ctx)
    try:
        root = app.paths.resolve_read(request.root, must_exist=True) if request.root else app.workspace
    except Exception as exc:
        return ProjectActionReport(
            ok=False,
            action=request.action,
            error=_error(exc),
            summary=str(exc),
        )
    try:
        profile = inspect_project(root, paths=app.paths, deep=request.deep)
    except Exception as exc:
        error = _error(exc)
        if error.code == "internal_error" and isinstance(exc, ValueError):
            error = ErrorInfo(code="invalid_input", message=str(exc))
        return ProjectActionReport(ok=False, action=request.action, error=error, summary=str(exc))
    if request.action == "inspect":
        return ProjectActionReport(
            ok=True,
            action=request.action,
            profile=profile,
            path=app.paths.display(root),
            summary=(
                f"Found {len(profile.hdl_files)} HDL file(s), "
                f"{len(profile.top_candidates)} top candidate(s), "
                f"{len(profile.constraints)} constraint file(s)."
            ),
        )
    if request.action == "validate":
        warnings = list(profile.warnings)
        known_units = set(profile.modules) | set(profile.entities)
        if profile.config and profile.config.top and profile.config.top not in known_units:
            warnings.append(f"Configured top {profile.config.top!r} is not defined.")
        return ProjectActionReport(
            ok=not warnings,
            action=request.action,
            profile=profile,
            path=app.paths.display(root),
            summary="Project validation passed." if not warnings else "Project validation found warnings.",
        )
    if find_config_file(root) and not request.write:
        return ProjectActionReport(
            ok=False,
            action=request.action,
            profile=profile,
            path=app.paths.display(root),
            error=ErrorInfo(
                code="config_exists",
                message="oss-cad-project.toml already exists.",
                hint="Set write=true to overwrite it, or edit it manually.",
            ),
            summary="Project configuration already exists.",
        )
    if not request.write:
        return ProjectActionReport(
            ok=False,
            action=request.action,
            profile=profile,
            path=app.paths.display(root),
            error=ErrorInfo(
                code="write_not_allowed",
                message="Project initialization writes a file, so write=true is required.",
            ),
            summary="Project initialization was not authorized.",
        )
    config_path = root / "oss-cad-project.toml"
    payload: dict[str, Any] = {
        "project": {
            "name": root.name,
            "top": profile.top_candidates[0] if profile.top_candidates else None,
            "sources": (profile.source_files or profile.hdl_files)[:200],
            "testbenches": profile.testbench_files,
            "include_dirs": [],
            "defines": {},
            "language": "auto",
        },
        "target": {},
        "sim": {
            "engine": "auto",
            "testbench": profile.testbenches[0] if profile.testbenches else None,
            "trace": "auto",
        },
        "formal": {"engine": "smtbmc", "depth": 20, "mode": "prove"},
    }
    payload["project"] = {key: value for key, value in payload["project"].items() if value is not None}
    config_path.write_text(tomli_w.dumps(payload), encoding="utf-8")
    return ProjectActionReport(
        ok=True,
        action=request.action,
        profile=profile,
        path=app.paths.display(config_path),
        written=True,
        summary="Wrote oss-cad-project.toml.",
    )


async def cad_tool_search(ctx: Context, request: ToolSearchRequest) -> ToolSearchResult:
    """Search the complete OSS CAD Suite catalog, including tools not exposed as workflows."""
    app = get_app(ctx)
    registry = app.require_registry()
    entries = registry.search(
        request.query,
        family=request.family,
        risk=request.risk,
        include_internal=request.include_internal,
        include_hardware=request.include_hardware,
        limit=registry.count(),
    )
    return ToolSearchResult(
        query=request.query,
        total=len(entries),
        tools=registry.infos(entries[: request.limit]),
    )


async def cad_tool_help(ctx: Context, request: ToolHelpRequest) -> ToolHelp:
    """Show curated help, version, examples, and risk notes for a suite tool."""
    app = get_app(ctx)
    try:
        return await app.require_registry().help(request.tool)
    except ToolNotFound as exc:
        return ToolHelp(
            tool=request.tool,
            summary="Unknown suite tool.",
            binary=request.tool,
            risk="read_only",
            ok=False,
            error=exc.to_info(),
            notes=["Use cad_tool_search to discover available tools."],
        )


async def cad_tool_run(ctx: Context, request: ToolRunRequest) -> ToolRunReport:
    """Run an allowlisted suite binary with argv. Prefer high-level workflows."""
    app = get_app(ctx)
    if not app.settings.allow_raw:
        error = ErrorInfo(
            code="raw_disabled",
            message="Raw tool execution is disabled by configuration.",
            hint="Use the high-level workflows or set allow_raw=true.",
        )
        return ToolRunReport(
            ok=False,
            tool=request.tool,
            job=_blocked_job(app, summary="Raw tool execution is disabled.", error=error),
            error=error,
            summary="Raw tool execution is disabled.",
        )
    registry = app.require_registry()
    try:
        entry = registry.get(request.tool)
    except ToolNotFound as exc:
        error = exc.to_info()
        return ToolRunReport(
            ok=False,
            tool=request.tool,
            job=_blocked_job(app, summary="Unknown suite tool.", error=error),
            error=error,
            summary="Unknown suite tool.",
        )
    if entry.hardware:
        error = ErrorInfo(
            code="hardware_disabled",
            message="Hardware programming is out of scope for v0.1.",
            hint="Use software workflows: check, simulate, synthesize, verify, fpga_build.",
        )
        return ToolRunReport(
            ok=False,
            tool=entry.name,
            job=_blocked_job(app, summary="Hardware tools are disabled.", error=error),
            error=error,
            summary="Hardware tools are disabled.",
        )
    if entry.gui:
        error = ErrorInfo(
            code="gui_not_supported",
            message="GUI tools are not run as blocking MCP calls.",
            hint="Use cad_waveform for analysis or launch a viewer manually.",
        )
        return ToolRunReport(
            ok=False,
            tool=entry.name,
            job=_blocked_job(app, summary="GUI tool blocked.", error=error),
            error=error,
            summary="GUI tool blocked.",
        )
    unsafe_allowed = request.allow_unsafe and app.settings.allow_unsafe
    if entry.internal and not unsafe_allowed:
        error = ErrorInfo(
            code="internal_tool",
            message="This is an internal suite helper.",
            hint="Use the high-level wrapper or set allow_unsafe=true only if you understand it.",
        )
        return ToolRunReport(
            ok=False,
            tool=entry.name,
            job=_blocked_job(app, summary="Internal tool blocked.", error=error),
            error=error,
            summary="Internal tool blocked.",
        )
    if entry.name == "yosys" and not unsafe_allowed:
        joined = " ".join(request.args).lower()
        if any(token in joined for token in (" shell", "exec", "plugin", "read -tcl", "tcl ")):
            error = ErrorInfo(
                code="unsafe_command",
                message="The raw Yosys invocation contains a disabled command.",
                hint="Use cad_synthesize for synthesis; set allow_unsafe=true for advanced use.",
            )
            return ToolRunReport(
                ok=False,
                tool=entry.name,
                job=_blocked_job(app, summary="Unsafe Yosys command blocked.", error=error),
                error=error,
                summary="Unsafe Yosys command blocked.",
            )
    cwd = app.workspace
    if request.cwd:
        try:
            cwd = app.paths.resolve_read(request.cwd, must_exist=True)
        except PathNotAllowed as exc:
            error = exc.to_info()
            return ToolRunReport(
                ok=False,
                tool=entry.name,
                job=_blocked_job(app, summary="Invalid working directory.", error=error),
                error=error,
                summary="Invalid working directory.",
            )
        if not cwd.is_dir():
            error = ErrorInfo(code="invalid_cwd", message=f"Not a directory: {cwd}")
            return ToolRunReport(
                ok=False,
                tool=entry.name,
                job=_blocked_job(app, summary="Invalid working directory.", error=error),
                error=error,
                summary="Invalid working directory.",
            )

    async def run(job):
        suite = app.require_suite()
        binary = suite.binary(entry.binary)
        result = await app.jobs.run_step(
            job,
            entry.name,
            [str(binary), *request.args],
            tool=entry.name,
            cwd=cwd,
            env=suite.run_env(),
            timeout_s=request.timeout_s,
            stdin_text=request.stdin,
        )
        stdout = read_tail(Path(job.steps[-1].stdout)) if job.steps and job.steps[-1].stdout else ""
        stderr = read_tail(Path(job.steps[-1].stderr)) if job.steps and job.steps[-1].stderr else ""
        return ToolRunReport(
            ok=result.exit_code == 0 and not result.timed_out,
            tool=entry.name,
            argv=[str(binary), *request.args],
            exit_code=result.exit_code,
            stdout_tail=stdout[-16_384:],
            stderr_tail=stderr[-16_384:],
            job=job.to_ref(),
            error=None
            if result.exit_code == 0 and not result.timed_out
            else ErrorInfo(
                code="tool_timeout" if result.timed_out else "tool_failed",
                message=f"{entry.name} exited with code {result.exit_code}.",
            ),
            summary=f"{entry.name} exited with code {result.exit_code}.",
            duration_s=result.duration_s,
        )

    job = await app.jobs.execute("tool", run, timeout_s=request.timeout_s)
    if isinstance(job.result, ToolRunReport):
        return job.result
    return ToolRunReport(
        ok=False,
        tool=entry.name,
        job=job.to_ref(),
        error=job.error
        or ErrorInfo(
            code="job_running" if job.status in {"pending", "running"} else "tool_failed",
            message=f"Job is {job.status}.",
            hint="Use cad_job(action='wait') to poll this job.",
        ),
        summary=f"{entry.name} job is {job.status}.",
    )
