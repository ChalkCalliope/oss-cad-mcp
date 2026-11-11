"""MCP tool wrappers for high-level workflows."""

from __future__ import annotations

from mcp.server.mcpserver import Context

from oss_cad_mcp.models.common import ErrorInfo
from oss_cad_mcp.models.diagnostics import CheckReport
from oss_cad_mcp.models.results import (
    FpgaBuildReport,
    NetlistReport,
    SimulationReport,
    SynthesisReport,
    TimingReport,
    VerificationReport,
    WaveformReport,
)
from oss_cad_mcp.tools.schemas import (
    CheckRequest,
    FpgaBuildRequest,
    NetlistRequest,
    SimulateRequest,
    SynthesizeRequest,
    TimingRequest,
    VerifyRequest,
    WaveformRequest,
)
from oss_cad_mcp.tools.system import _error, get_app
from oss_cad_mcp.workflows.check import run_check
from oss_cad_mcp.workflows.formal import run_verify
from oss_cad_mcp.workflows.fpga import run_fpga_build
from oss_cad_mcp.workflows.netlist import inspect_netlist
from oss_cad_mcp.workflows.simulate import run_simulate
from oss_cad_mcp.workflows.synthesize import run_synthesize
from oss_cad_mcp.workflows.timing import parse_timing_report, run_icetime
from oss_cad_mcp.workflows.waveform import run_waveform


def _job_error(job) -> ErrorInfo:
    if job.error is not None:
        return job.error
    if job.status == "cancelled":
        return ErrorInfo(
            code="job_cancelled",
            message=f"Job {job.id} was cancelled.",
            hint="Re-submit the workflow if the cancellation was unintended.",
            retryable=True,
        )
    if job.status == "timeout":
        return ErrorInfo(
            code="job_timeout",
            message=f"Job {job.id} timed out.",
            hint="Increase timeout_s or inspect the job log for a stuck process.",
            retryable=True,
        )
    if job.status in {"pending", "running"}:
        return ErrorInfo(
            code="job_running",
            message=f"Job {job.id} is still running.",
            hint="Use cad_job(action='wait', job_id=...) to poll it.",
            retryable=True,
        )
    return ErrorInfo(code="job_failed", message=f"Job {job.id} ended with status {job.status}.")


def _job_summary(job) -> str:
    if job.status in {"pending", "running"}:
        return f"Job {job.id} is still running; poll it with cad_job."
    return f"Job {job.id} ended with status {job.status}."


async def cad_check(ctx: Context, request: CheckRequest) -> CheckReport:
    """Parse, lint, or elaborate HDL sources and return normalized diagnostics."""
    app = get_app(ctx)

    async def factory(job):
        return await run_check(
            app,
            job,
            source=request.source,
            level=request.level,
            engines=request.engines,
            top=request.top,
            timeout_s=request.timeout_s,
        )

    job = await app.jobs.execute("check", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, CheckReport):
        return job.result
    return CheckReport(
        ok=False,
        level=request.level,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_simulate(ctx: Context, request: SimulateRequest) -> SimulationReport:
    """Compile and run an HDL testbench, returning diagnostics and a waveform artifact."""
    app = get_app(ctx)

    async def factory(job):
        return await run_simulate(
            app,
            job,
            source=request.source,
            top=request.top,
            engine=request.engine,
            trace=request.trace,
            plusargs=request.plusargs,
            timeout_s=request.timeout_s,
            seed=request.seed,
            expect_fail=request.expect_fail,
        )

    job = await app.jobs.execute("simulate", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, SimulationReport):
        return job.result
    return SimulationReport(
        ok=False,
        engine=request.engine,
        top=request.top,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_synthesize(ctx: Context, request: SynthesizeRequest) -> SynthesisReport:
    """Synthesize HDL with Yosys and return cell statistics and a JSON netlist."""
    app = get_app(ctx)

    async def factory(job):
        return await run_synthesize(
            app,
            job,
            source=request.source,
            top=request.top,
            target=request.target,
            flatten=request.flatten,
            abc9=request.abc9,
            timeout_s=request.timeout_s,
        )

    job = await app.jobs.execute("synthesize", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, SynthesisReport):
        return job.result
    return SynthesisReport(
        ok=False,
        target=request.target,
        top=request.top,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_verify(ctx: Context, request: VerifyRequest) -> VerificationReport:
    """Run formal verification with SBY, or EQY/MCY with an existing config."""
    app = get_app(ctx)

    async def factory(job):
        return await run_verify(
            app,
            job,
            mode=request.mode,
            source=request.source,
            top=request.top,
            engine=request.engine,
            depth=request.depth,
            config_file=request.config_file,
            timeout_s=request.timeout_s,
            expect_fail=request.expect_fail,
        )

    job = await app.jobs.execute("verify", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, VerificationReport):
        return job.result
    return VerificationReport(
        ok=False,
        mode=request.mode,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_fpga_build(ctx: Context, request: FpgaBuildRequest) -> FpgaBuildReport:
    """Build an iCE40/ECP5 bitstream: synthesis, place-and-route, packing, timing."""
    app = get_app(ctx)

    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=request.source,
            top=request.top,
            board=request.board,
            family=request.family,
            device=request.device,
            package=request.package,
            speed=request.speed,
            constraints=request.constraints,
            allow_unconstrained=request.allow_unconstrained,
            frequency_mhz=request.frequency_mhz,
            seed=request.seed,
            placer=request.placer,
            router=request.router,
            timeout_s=request.timeout_s,
        )

    job = await app.jobs.execute("fpga_build", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, FpgaBuildReport):
        return job.result
    return FpgaBuildReport(
        ok=False,
        family=request.family or "unknown",
        device=request.device,
        package=request.package,
        speed=request.speed,
        top=request.top,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_waveform(ctx: Context, request: WaveformRequest) -> WaveformReport:
    """Inspect, sample, summarize, export, or convert VCD/FST waveforms."""
    app = get_app(ctx)

    async def factory(job):
        return await run_waveform(
            app,
            job,
            action=request.action,
            path=request.path,
            signals=request.signals or None,
            start=request.start,
            end=request.end,
            max_samples=request.max_samples,
            max_signals=request.max_signals,
            output_format=request.output_format,
        )

    job = await app.jobs.execute("waveform", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, WaveformReport):
        return job.result
    return WaveformReport(
        ok=False,
        action=request.action,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_netlist(ctx: Context, request: NetlistRequest) -> NetlistReport:
    """Inspect a Yosys JSON netlist: stats, modules, cells, nets, fanout, or diff."""
    app = get_app(ctx)

    async def factory(job):
        report = inspect_netlist(
            app,
            action=request.action,
            path=request.path,
            module=request.module,
            cell_type=request.cell_type,
            name=request.name,
            other=request.other,
            limit=request.limit,
            include_internal=request.include_internal,
            include_library=request.include_library,
        )
        report.job = job.to_ref()
        return report

    job = await app.jobs.execute("netlist", factory, timeout_s=30.0)
    if isinstance(job.result, NetlistReport):
        return job.result
    return NetlistReport(
        ok=False,
        action=request.action,
        path=request.path,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )


async def cad_timing(ctx: Context, request: TimingRequest) -> TimingReport:
    """Parse nextpnr/icetime timing reports, or run icetime on an .asc file."""
    app = get_app(ctx)
    if request.action == "parse":
        try:
            return parse_timing_report(app, request.path, clock_mhz=request.clock_mhz)
        except Exception as exc:
            return TimingReport(ok=False, source=request.path, error=_error(exc), summary=str(exc))
    if not request.device:
        return TimingReport(
            ok=False,
            source=request.path,
            error=ErrorInfo(code="missing_device", message="icetime requires device='up5k'."),
            summary="Missing device.",
        )

    device = request.device

    async def factory(job):
        return await run_icetime(
            app,
            job,
            asc_path=request.path,
            device=device,
            clock_mhz=request.clock_mhz,
            timeout_s=request.timeout_s,
        )

    job = await app.jobs.execute("timing", factory, timeout_s=request.timeout_s)
    if isinstance(job.result, TimingReport):
        return job.result
    return TimingReport(
        ok=False,
        source=request.path,
        job=job.to_ref(),
        error=_job_error(job),
        summary=_job_summary(job),
    )
