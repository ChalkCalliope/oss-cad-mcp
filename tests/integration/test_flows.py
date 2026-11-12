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


@pytest.mark.integration
async def test_synthesis_uses_project_target(app: AppContext) -> None:
    (app.workspace / "oss-cad-project.toml").write_text(
        '[project]\ntop = "blinky"\nsources = ["rtl/blinky.sv"]\nlanguage = "systemverilog"\n'
        '[target]\nfamily = "ice40"\n',
        encoding="utf-8",
    )

    async def factory(job):
        return await run_synthesize(app, job, timeout_s=120)

    report = await _run(app, "synthesize", factory)
    assert report.target == "ice40"
    assert report.ok


@pytest.mark.integration
async def test_config_only_fpga_build(app: AppContext) -> None:
    (app.workspace / "oss-cad-project.toml").write_text(
        '[project]\ntop = "blinky"\nsources = ["rtl/blinky.sv"]\nlanguage = "systemverilog"\n'
        '[target]\nboard = "icebreaker"\nfamily = "ice40"\ndevice = "up5k"\n'
        'package = "sg48"\nfrequency_mhz = 48\nconstraints = ["pins.pcf"]\n',
        encoding="utf-8",
    )

    async def factory(job):
        return await run_fpga_build(app, job, timeout_s=180)

    report = await _run(app, "fpga_build", factory, timeout=180)
    assert report.ok
    assert report.family == "ice40"
    assert report.device == "up5k"
    assert report.package == "sg48"
    assert report.bitstream is not None


@pytest.mark.integration
async def test_allow_unconstrained_with_partial_constraints(app: AppContext) -> None:
    (app.workspace / "pins_partial.pcf").write_text("set_io clk 35\nset_io rst 11\n", encoding="utf-8")

    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            board="icebreaker",
            constraints=["pins_partial.pcf"],
            allow_unconstrained=True,
            frequency_mhz=48,
            timeout_s=180,
        )

    report = await _run(app, "fpga_build", factory, timeout=180)
    assert report.ok
    assert report.bitstream is not None


@pytest.mark.integration
async def test_project_formal_defaults_are_applied(app: AppContext) -> None:
    source = app.workspace / "formal.sv"
    example = app.suite.root / "examples" / "quickstart" / "prove.sv"  # type: ignore[union-attr]
    shutil.copyfile(example, source)
    (app.workspace / "oss-cad-project.toml").write_text(
        '[project]\ntop = "testbench"\nsources = ["formal.sv"]\nlanguage = "verilog"\n'
        '[formal]\nmode = "cover"\ndepth = 5\nengine = "smtbmc"\n',
        encoding="utf-8",
    )

    async def factory(job):
        return await run_verify(
            app,
            job,
            source=SourceSet(files=["formal.sv"], language="verilog"),
            top="testbench",
            timeout_s=120,
        )

    report = await _run(app, "verify", factory)
    assert report.mode == "cover"
    assert report.depth == 5


@pytest.mark.integration
async def test_multiple_constraints_are_rejected(app: AppContext) -> None:
    (app.workspace / "pins2.pcf").write_text("set_io clk 35\n", encoding="utf-8")

    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            board="icebreaker",
            constraints=["pins.pcf", "pins2.pcf"],
            frequency_mhz=48,
            timeout_s=120,
        )

    job = await app.jobs.execute("fpga_build", factory, timeout_s=120)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "invalid_input"
    assert "one constraint" in job.error.message.lower()


@pytest.mark.integration
async def test_timing_failure_is_classified(app: AppContext) -> None:
    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            board="icebreaker",
            constraints=["pins.pcf"],
            frequency_mhz=200,
            timeout_s=180,
        )

    job = await app.jobs.execute("fpga_build", factory, timeout_s=180)
    report = job.result
    assert report is not None
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "timing_failed"
    assert report.timing is not None
    assert report.timing.met is False
    assert report.timing_met is False


@pytest.mark.integration
async def test_eqy_runs_are_isolated(app: AppContext) -> None:
    (app.workspace / "gold.v").write_text(
        "module counter(input clk, output reg [3:0] q);\n  always @(posedge clk) q <= q + 1'b1;\nendmodule\n",
        encoding="utf-8",
    )
    (app.workspace / "gate.v").write_text((app.workspace / "gold.v").read_text(), encoding="utf-8")
    (app.workspace / "counter.eqy").write_text(
        "[options]\n\n[strategy simple]\nuse sat\ndepth 5\n\n"
        "[gold]\nread_verilog gold.v\nprep -top counter\n\n"
        "[gate]\nread_verilog gate.v\nprep -top counter\n",
        encoding="utf-8",
    )

    async def factory(job):
        return await run_verify(
            app,
            job,
            mode="equiv",
            config_file="counter.eqy",
            timeout_s=180,
        )

    first = await _run(app, "verify", factory, timeout=180)
    second = await _run(app, "verify", factory, timeout=180)
    assert first.ok and second.ok
    assert first.log_tail and second.log_tail
    assert not (app.workspace / "counter").exists()


@pytest.mark.integration
async def test_mcy_mutation_is_explicitly_unsupported(app: AppContext) -> None:
    (app.workspace / "mutation.mcy").write_text("[options]\nsize 10\n", encoding="utf-8")

    async def factory(job):
        return await run_verify(app, job, mode="mutation", config_file="mutation.mcy", timeout_s=60)

    job = await app.jobs.execute("verify", factory, timeout_s=60)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "not_supported"


@pytest.mark.integration
async def test_ghdl_analysis_diagnostics_are_reported(app: AppContext) -> None:
    (app.workspace / "bad.vhd").write_text(
        """\
entity bad_syntax is
    port (
        clk : in std_logic;
        q   : out std_logic
    )
end entity bad_syntax;
""",
        encoding="utf-8",
    )

    async def factory(job):
        return await run_simulate(
            app,
            job,
            source=SourceSet(files=["bad.vhd"], language="vhdl"),
            top="bad_syntax",
            engine="ghdl",
            trace="none",
            timeout_s=60,
        )

    job = await app.jobs.execute("simulate", factory, timeout_s=60)
    report = job.result
    assert report is not None
    assert job.status == "failed"
    assert report.diagnostics
    assert any("missing" in diagnostic.message for diagnostic in report.diagnostics)
    assert report.stderr_tail


@pytest.mark.integration
async def test_verilator_seed_is_passed_as_plusarg(app: AppContext) -> None:
    (app.workspace / "tb_seed.v").write_text(
        """\
module tb_seed;
    integer seed;
    initial begin
        if (!$value$plusargs("seed=%d", seed)) begin
            seed = 1;
        end
        $display("SEED=%0d", seed);
        $display("RANDOM=%0d", $random(seed));
        $finish;
    end
endmodule
""",
        encoding="utf-8",
    )

    outputs: list[str] = []
    for seed in (123, 456):

        async def factory(job, seed=seed):
            return await run_simulate(
                app,
                job,
                source=SourceSet(files=["tb_seed.v"], language="verilog"),
                top="tb_seed",
                engine="verilator",
                trace="none",
                seed=seed,
                timeout_s=120,
            )

        report = await _run(app, "simulate", factory)
        outputs.append(report.stdout_tail)
    assert "SEED=123" in outputs[0]
    assert "SEED=456" in outputs[1]
    assert outputs[0] != outputs[1]


@pytest.mark.integration
async def test_configless_simulation_prefers_testbench_for_dut(app: AppContext) -> None:
    (app.workspace / "rtl" / "blinky.sv").unlink()
    (app.workspace / "tb" / "tb_blinky.sv").unlink()
    (app.workspace / "rtl" / "counter.v").write_text(
        "module counter(input clk, output reg q);\n  always @(posedge clk) q <= ~q;\nendmodule\n",
        encoding="utf-8",
    )
    (app.workspace / "tb" / "tb_aaa.v").write_text(
        'module tb_aaa;\n  initial begin $display("TEST FAILED"); $finish; end\nendmodule\n',
        encoding="utf-8",
    )
    (app.workspace / "tb" / "tb_counter.v").write_text(
        "module tb_counter;\n"
        "  reg clk = 0;\n  wire q;\n"
        "  counter uut(.clk(clk), .q(q));\n"
        "  always #5 clk = ~clk;\n"
        '  initial begin #40 $display("TEST PASSED"); $finish; end\n'
        "endmodule\n",
        encoding="utf-8",
    )

    async def factory(job):
        return await run_simulate(app, job, engine="iverilog", trace="none", timeout_s=60)

    report = await _run(app, "simulate", factory)
    assert report.top == "tb_counter"
    assert report.ok


@pytest.mark.integration
async def test_elaborate_hierarchy_has_children(app: AppContext) -> None:
    (app.workspace / "rtl" / "child.sv").write_text(
        "module child(input logic a, output logic b); assign b = a; endmodule\n",
        encoding="utf-8",
    )
    (app.workspace / "rtl" / "pipeline.sv").write_text(
        "module pipeline(input logic a, output logic b);\n  child u_child(.a(a), .b(b));\nendmodule\n",
        encoding="utf-8",
    )

    async def factory(job):
        return await run_check(
            app,
            job,
            source=SourceSet(files=["rtl/child.sv", "rtl/pipeline.sv"], language="systemverilog"),
            top="pipeline",
            level="elaborate",
            engines=["slang"],
            timeout_s=120,
        )

    report = await _run(app, "check", factory)
    assert report.ok
    assert len(report.hierarchy) == 1
    root = report.hierarchy[0]
    assert root.name == "pipeline"
    assert [child.name for child in root.children] == ["u_child"]


@pytest.mark.integration
async def test_unsupported_synthesis_target_is_structured(app: AppContext) -> None:
    async def factory(job):
        return await run_synthesize(app, job, target="machxo2", timeout_s=60)

    job = await app.jobs.execute("synthesize", factory, timeout_s=90)
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "unsupported_target"
    assert "machxo2" in job.error.message


@pytest.mark.integration
async def test_allow_timing_fail_returns_bitstream(app: AppContext) -> None:
    async def factory(job):
        return await run_fpga_build(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            board="icebreaker",
            constraints=["pins.pcf"],
            frequency_mhz=200,
            allow_timing_fail=True,
            timeout_s=180,
        )

    job = await app.jobs.execute("fpga_build", factory, timeout_s=180)
    report = job.result
    assert report is not None
    assert job.status == "failed"
    assert job.error is not None
    assert job.error.code == "timing_failed"
    assert report.bitstream is not None
    assert report.timing is not None
    assert report.timing.met is False
