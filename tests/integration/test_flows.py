from __future__ import annotations

import shutil

import pytest

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.workflows.check import run_check
from oss_cad_mcp.workflows.formal import run_verify
from oss_cad_mcp.workflows.fpga import run_fpga_build
from oss_cad_mcp.workflows.netlist import inspect_netlist
from oss_cad_mcp.workflows.simulate import run_simulate
from oss_cad_mcp.workflows.synthesize import run_synthesize
from oss_cad_mcp.workflows.timing import parse_timing_report
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

    async def selected_signals(job):
        return await run_waveform(
            app,
            job,
            action="signals",
            path=report.waveform.path,
            signals=["clk"],
            max_signals=1,
        )

    selected = await _run(app, "waveform", selected_signals, timeout=60)
    assert selected.ok
    assert "1 selected signal(s)" in selected.summary
    assert len(selected.info.signals) == 1


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
async def test_generic_synthesis(app: AppContext) -> None:
    async def factory(job):
        return await run_synthesize(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            target="generic",
            timeout_s=120,
        )

    report = await _run(app, "synthesize", factory)
    assert report.ok
    assert report.netlist is not None
    assert report.stats["num_cells"] > 0


@pytest.mark.integration
async def test_vhdl_simulation_and_synthesis(app: AppContext) -> None:
    (app.workspace / "rtl" / "pwm.vhd").write_text(
        """\
library ieee;
use ieee.std_logic_1164.all;

entity pwm is
    port (
        clk     : in  std_logic;
        rst     : in  std_logic;
        pwm_out : out std_logic
    );
end entity pwm;

architecture rtl of pwm is
    signal state : std_logic := '0';
begin
    process (clk)
    begin
        if rising_edge(clk) then
            if rst = '1' then
                state <= '0';
            else
                state <= not state;
            end if;
        end if;
    end process;

    pwm_out <= state;
end architecture rtl;
""",
        encoding="utf-8",
    )
    (app.workspace / "tb" / "tb_pwm.vhd").write_text(
        """\
library ieee;
use ieee.std_logic_1164.all;
use std.env.all;

entity tb_pwm is end entity tb_pwm;

architecture sim of tb_pwm is
    signal clk     : std_logic := '0';
    signal rst     : std_logic := '1';
    signal pwm_out : std_logic;
begin
    clk <= not clk after 5 ns;

    dut : entity work.pwm
        port map (clk => clk, rst => rst, pwm_out => pwm_out);

    stimulus : process
    begin
        wait for 20 ns;
        rst <= '0';
        for i in 0 to 5 loop
            wait until rising_edge(clk);
        end loop;
        assert pwm_out = '1' report "PWM did not toggle" severity failure;
        report "TEST PASSED" severity note;
        finish;
    end process;
end architecture sim;
""",
        encoding="utf-8",
    )

    async def simulate(job):
        return await run_simulate(
            app,
            job,
            source=SourceSet(
                files=["rtl/pwm.vhd", "tb/tb_pwm.vhd"],
                language="vhdl",
            ),
            top="tb_pwm",
            engine="ghdl",
            trace="vcd",
            timeout_s=120,
        )

    simulation = await _run(app, "simulate", simulate)
    assert simulation.ok
    assert simulation.waveform is not None

    async def synthesize(job):
        return await run_synthesize(
            app,
            job,
            source=SourceSet(files=["rtl/pwm.vhd"], language="vhdl"),
            top="pwm",
            target="ice40",
            timeout_s=180,
        )

    synthesis = await _run(app, "synthesize", synthesize, timeout=180)
    assert synthesis.ok
    assert synthesis.netlist is not None


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


@pytest.mark.integration
def test_timing_asc_hint_uses_action(app: AppContext) -> None:
    (app.workspace / "design.asc").write_text("dummy\n", encoding="utf-8")
    with pytest.raises(InvalidInput) as excinfo:
        parse_timing_report(app, "design.asc")
    assert "action='icetime'" in str(excinfo.value)


@pytest.mark.integration
async def test_simulation_test_failed_marker_is_a_failure(app: AppContext) -> None:
    (app.workspace / "sim_fail.v").write_text(
        """\
module sim_fail;
    initial begin
        $display("TEST FAILED: deliberate");
        $finish;
    end
endmodule
""",
        encoding="utf-8",
    )

    async def factory(job):
        return await run_simulate(
            app,
            job,
            source=SourceSet(files=["sim_fail.v"], language="verilog"),
            top="sim_fail",
            engine="iverilog",
            trace="none",
            timeout_s=30,
        )

    job = await app.jobs.execute("simulate", factory, timeout_s=30)
    assert job.result is not None
    assert job.result.ok is False
    assert job.status == "failed"
    assert "TEST FAILED" in job.result.stdout_tail


@pytest.mark.integration
async def test_simulation_timeout_is_terminal_failure(app: AppContext) -> None:
    (app.workspace / "hang.v").write_text(
        """\
module hang;
    initial begin
        forever #10;
    end
endmodule
""",
        encoding="utf-8",
    )

    async def factory(job):
        return await run_simulate(
            app,
            job,
            source=SourceSet(files=["hang.v"], language="verilog"),
            top="hang",
            engine="iverilog",
            trace="none",
            timeout_s=0.5,
        )

    job = await app.jobs.execute("simulate", factory, timeout_s=0.5)
    assert job.result is not None
    assert job.result.ok is False
    assert job.result.timed_out is True
    assert job.status == "timeout"
    assert job.result.error is not None
    assert job.result.error.code == "simulation_timeout"
    assert job.result.waveform is None


@pytest.mark.integration
async def test_waveform_size_cap_skips_artifact(app: AppContext) -> None:
    app.settings.max_waveform_bytes = 1

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

    job = await app.jobs.execute("simulate", factory, timeout_s=120)
    assert job.result is not None
    assert job.result.ok is True
    assert job.result.waveform is None
    assert any("max_waveform_bytes" in warning for warning in job.result.warnings)
