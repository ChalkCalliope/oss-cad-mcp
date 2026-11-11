from __future__ import annotations

import shutil

import pytest

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.workflows.check import run_check
from oss_cad_mcp.workflows.formal import run_verify
from oss_cad_mcp.workflows.fpga import run_fpga_build
from oss_cad_mcp.workflows.netlist import inspect_netlist
from oss_cad_mcp.workflows.simulate import run_simulate
from oss_cad_mcp.workflows.synthesize import run_synthesize
from oss_cad_mcp.workflows.waveform import run_waveform


async def _run(app: AppContext, kind: str, factory, timeout: float = 180.0):
    job = await app.jobs.execute(kind, factory, timeout_s=timeout)
    assert job.status == "passed", (job.status, job.error, job.summary)
    assert job.result is not None
    return job.result


@pytest.mark.integration
async def test_lint_and_elaborate(app: AppContext) -> None:
    async def factory(job):
        return await run_check(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            engines=["slang", "verilator"],
            timeout_s=120,
        )

    report = await _run(app, "check", factory)
    assert report.ok
    assert report.engines == ["slang", "verilator"]
    assert report.counts.errors == 0


@pytest.mark.integration
async def test_simulation_and_waveform(app: AppContext) -> None:
    async def factory(job):
        return await run_simulate(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv", "tb/tb_blinky.sv"]),
            top="tb_blinky",
            engine="iverilog",
            trace="vcd",
            timeout_s=120,
        )

    report = await _run(app, "simulate", factory)
    assert report.ok
    assert report.waveform is not None

    async def waveform(job):
        return await run_waveform(
            app,
            job,
            action="info",
            path=report.waveform.path,
        )

    info = await _run(app, "waveform", waveform, timeout=60)
    assert info.ok
    assert info.info is not None
    assert info.info.signal_count > 0


@pytest.mark.integration
async def test_synthesis_and_netlist(app: AppContext) -> None:
    async def factory(job):
        return await run_synthesize(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            target="ice40",
            timeout_s=120,
        )

    report = await _run(app, "synthesize", factory)
    assert report.ok
    assert report.netlist is not None
    assert "SB_LUT4" in report.cells_by_type

    netlist = inspect_netlist(app, action="stats", path=report.netlist.path)
    assert netlist.ok
    assert netlist.module_count >= 1


@pytest.mark.integration
async def test_formal_prove(app: AppContext) -> None:
    source = app.workspace / "formal.sv"
    example = app.suite.root / "examples" / "quickstart" / "prove.sv"  # type: ignore[union-attr]
    shutil.copyfile(example, source)

    async def factory(job):
        return await run_verify(
            app,
            job,
            mode="prove",
            source=SourceSet(files=["formal.sv"], language="verilog"),
            top="testbench",
            depth=20,
            timeout_s=120,
        )

    report = await _run(app, "verify", factory)
    assert report.ok
    assert report.status == "passed"
    assert report.properties


@pytest.mark.integration
async def test_ice40_bitstream_build(app: AppContext) -> None:
    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            board="icebreaker",
            constraints=["pins.pcf"],
            frequency_mhz=48,
            timeout_s=180,
        )

    report = await _run(app, "fpga_build", factory)
    assert report.ok
    assert report.bitstream is not None
    assert report.timing_met is not False


@pytest.mark.integration
async def test_ecp5_unconstrained_bringup_build(app: AppContext) -> None:
    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            family="ecp5",
            device="85k",
            package="CABGA381",
            speed="6",
            allow_unconstrained=True,
            frequency_mhz=25,
            timeout_s=180,
        )

    report = await _run(app, "fpga_build", factory)
    assert report.ok
    assert report.bitstream is not None
    assert report.timing is not None
