from __future__ import annotations

import os
import shutil
import sys
import tomllib
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from oss_cad_mcp.app import AppContext


async def _call(session: ClientSession, tool: str, request: dict, timeout: float = 300.0):
    return await session.call_tool(tool, {"request": request}, read_timeout_seconds=timeout)


@pytest.mark.integration
async def test_e2e_report_bug_focus(app: AppContext) -> None:
    """MCP-level regressions for the defects in the e2e report."""

    if app.suite is None:
        pytest.skip("suite unavailable")
    workspace = app.workspace
    outside = workspace.parent / "outside_bugfix.v"
    outside.write_text("module outside(input a, output b); assign b = a; endmodule\n", encoding="utf-8")
    (workspace / "bad.f").write_text(f"{outside}\n", encoding="utf-8")
    (workspace / "malformed.vcd").write_text("not a vcd\n", encoding="utf-8")
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02\xfe\xffbinary")
    (workspace / "pins_partial.pcf").write_text("set_io clk 35\nset_io rst 11\n", encoding="utf-8")
    (workspace / "pins2.pcf").write_text("set_io clk 35\n", encoding="utf-8")
    (workspace / "rtl" / "child.sv").write_text(
        "module child(input logic a, output logic b); assign b = a; endmodule\n",
        encoding="utf-8",
    )
    (workspace / "rtl" / "pipeline.sv").write_text(
        "module pipeline(input logic a, output logic b);\n  child u_child(.a(a), .b(b));\nendmodule\n",
        encoding="utf-8",
    )
    (workspace / "bad.vhd").write_text(
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
    formal = workspace / "formal.sv"
    shutil.copyfile(app.suite.root / "examples" / "quickstart" / "prove.sv", formal)

    project_config = """\
[project]
name = "bugfix-e2e"
top = "blinky"
sources = ["rtl/blinky.sv"]
language = "systemverilog"

[target]
board = "icebreaker"
family = "ice40"
device = "up5k"
package = "sg48"
frequency_mhz = 48
constraints = ["pins.pcf"]

[formal]
mode = "cover"
depth = 5
engine = "smtbmc"
"""
    (workspace / "oss-cad-project.toml").write_text(project_config, encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "oss_cad_mcp", "serve", "--workspace", str(workspace)],
        env={
            **os.environ,
            "OSS_CAD_SUITE_ROOT": str(app.suite.root),
            "OSS_CAD_MCP_ALLOW_RAW": "true",
        },
        cwd=Path(__file__).resolve().parents[2],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        # SEC-01: filelist cannot escape the workspace.
        escaped = await _call(
            session,
            "cad_check",
            {"source": {"filelist": "bad.f", "language": "verilog"}, "top": "outside", "engines": ["slang"]},
        )
        assert escaped.structured_content["ok"] is False
        assert escaped.structured_content["error"]["code"] == "path_not_allowed"

        # SEC-02: raw argv cannot write outside the workspace even when raw mode is on.
        raw = await _call(
            session,
            "cad_tool_run",
            {"tool": "yosys", "args": ["-p", f"read_verilog {outside}"]},
        )
        assert raw.structured_content["ok"] is False
        assert raw.structured_content["error"]["code"] == "path_not_allowed"

        # BUG-02: config-only FPGA target resolution.
        build = await _call(session, "cad_fpga_build", {}, timeout=300)
        assert build.structured_content["ok"] is True
        assert build.structured_content["device"] == "up5k"
        assert build.structured_content["bitstream"]

        # BUG-01: allow_unconstrained must be honored alongside a constraint file.
        partial = await _call(
            session,
            "cad_fpga_build",
            {"constraints": ["pins_partial.pcf"], "allow_unconstrained": True},
            timeout=300,
        )
        assert partial.structured_content["ok"] is True

        # BUG-03: synthesis uses [target].family.
        synth = await _call(session, "cad_synthesize", {}, timeout=300)
        assert synth.structured_content["target"] == "ice40"

        # BUG-16: multiple constraint files are rejected.
        two = await _call(
            session,
            "cad_fpga_build",
            {"constraints": ["pins.pcf", "pins2.pcf"]},
            timeout=300,
        )
        assert two.structured_content["ok"] is False
        assert two.structured_content["error"]["code"] == "invalid_input"

        # BUG-19: timing failures are classified separately from PnR failures.
        timing = await _call(
            session,
            "cad_fpga_build",
            {"frequency_mhz": 200},
            timeout=300,
        )
        assert timing.structured_content["ok"] is False
        assert timing.structured_content["error"]["code"] == "timing_failed"
        assert timing.structured_content["timing_met"] is False

        # BUG-15: unsupported synthesis targets are preflighted.
        machxo2 = await _call(session, "cad_synthesize", {"target": "machxo2"}, timeout=300)
        assert machxo2.structured_content["ok"] is False
        assert machxo2.structured_content["error"]["code"] == "unsupported_target"

        # BUG-10: hierarchy has real children.
        hierarchy = await _call(
            session,
            "cad_check",
            {
                "source": {"files": ["rtl/child.sv", "rtl/pipeline.sv"], "language": "systemverilog"},
                "top": "pipeline",
                "level": "elaborate",
                "engines": ["slang"],
            },
        )
        root = hierarchy.structured_content["hierarchy"][0]
        assert root["name"] == "pipeline"
        assert [child["name"] for child in root["children"]] == ["u_child"]

        # BUG-07: GHDL diagnostics are present in simulation failures.
        ghdl = await _call(
            session,
            "cad_simulate",
            {
                "source": {"files": ["bad.vhd"], "language": "vhdl"},
                "top": "bad_syntax",
                "engine": "ghdl",
                "trace": "none",
            },
        )
        assert ghdl.structured_content["ok"] is False
        assert ghdl.structured_content["diagnostics"]
        assert any("missing" in item["message"] for item in ghdl.structured_content["diagnostics"])

        # BUG-13: malformed VCD is rejected.
        waveform = await _call(session, "cad_waveform", {"action": "info", "path": "malformed.vcd"})
        assert waveform.structured_content["ok"] is False
        assert waveform.structured_content["error"]["code"] == "invalid_input"

        # BUG-18: binary artifacts are not decoded as text.
        binary = await _call(session, "cad_artifact", {"action": "read_text", "path": "blob.bin"})
        assert binary.structured_content["ok"] is False
        assert binary.structured_content["error"]["code"] == "binary_artifact"

        # BUG-04: project [formal] defaults are applied.
        formal_run = await _call(
            session,
            "cad_verify",
            {"source": {"files": ["formal.sv"], "language": "verilog"}, "top": "testbench"},
            timeout=300,
        )
        assert formal_run.structured_content["mode"] == "cover"
        assert formal_run.structured_content["depth"] == 5

        # BUG-17: help output does not report a usage string as a version.
        icepack = await _call(session, "cad_tool_help", {"tool": "icepack"})
        verilator = await _call(session, "cad_tool_help", {"tool": "verilator"})
        assert icepack.structured_content["version"] is None or not icepack.structured_content["version"].startswith(
            "Usage:"
        )
        assert verilator.structured_content["usage"] != "NAME"

        # BUG-14: init writes root-relative paths.
        init_root = workspace / "init-subdir"
        (init_root / "rtl").mkdir(parents=True)
        (init_root / "rtl" / "counter.v").write_text(
            "module counter(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule\n",
            encoding="utf-8",
        )
        (init_root / "tb").mkdir()
        (init_root / "tb" / "tb_counter.v").write_text(
            "module tb_counter; reg clk; wire q; counter uut(.clk(clk), .q(q)); endmodule\n",
            encoding="utf-8",
        )
        init = await _call(
            session,
            "cad_project",
            {"action": "init", "root": "init-subdir", "write": True},
        )
        assert init.structured_content["ok"] is True
        written = tomllib.loads((init_root / "oss-cad-project.toml").read_text(encoding="utf-8"))
        assert written["project"]["sources"] == ["rtl/counter.v"]
        assert written["project"]["testbenches"] == ["tb/tb_counter.v"]
