from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from oss_cad_mcp.app import AppContext


async def _call(session: ClientSession, tool: str, request: dict, timeout: float = 300.0):
    return await session.call_tool(tool, {"request": request}, read_timeout_seconds=timeout)


@pytest.mark.integration
async def test_bugfix_mcp_stdio(app: AppContext) -> None:
    """MCP-level coverage for the defects found in the full e2e validation."""
    if app.suite is None:
        pytest.skip("suite unavailable")
    workspace = app.workspace
    (workspace / "tb" / "sim_fail.v").write_text(
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
    (workspace / "tb" / "hang.v").write_text(
        """\
module hang;
    initial begin
        forever #10;
    end
endmodule
""",
        encoding="utf-8",
    )
    (workspace / "formal").mkdir()
    (workspace / "formal" / "blinky_formal.sv").write_text(
        "module blinky_formal; blinky dut(); endmodule\n",
        encoding="utf-8",
    )
    (workspace / "oss-cad-project.toml").write_text(
        '[project]\ntop = "blinky"\nsources = ["rtl/**/*.sv"]\n',
        encoding="utf-8",
    )
    (workspace / "missing.f").write_text("rtl/does_not_exist.sv\n", encoding="utf-8")
    (workspace / "library-netlist.json").write_text(
        json.dumps(
            {
                "modules": {
                    "top": {"ports": {}, "cells": {}, "netnames": {}},
                    "SB_LUT4": {
                        "attributes": {"blackbox": "00000000000000000000000000000001"},
                        "ports": {},
                        "cells": {},
                        "netnames": {},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "oss_cad_mcp", "serve", "--workspace", str(workspace)],
        env={**os.environ, "OSS_CAD_SUITE_ROOT": str(app.suite.root)},
        cwd=Path(__file__).resolve().parents[2],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        assert len(tools.tools) == 15

        page = await _call(session, "cad_tool_search", {"family": "formal", "limit": 5})
        full = await _call(session, "cad_tool_search", {"family": "formal", "limit": 200})
        assert page.structured_content["total"] == full.structured_content["total"]
        assert page.structured_content["total"] > len(page.structured_content["tools"])

        root = await _call(session, "cad_project", {"action": "inspect", "root": "/"})
        assert not root.is_error
        assert root.structured_content["ok"] is False
        assert root.structured_content["error"]["code"] == "path_not_allowed"

        failed_marker = await _call(
            session,
            "cad_simulate",
            {
                "source": {"files": ["tb/sim_fail.v"], "language": "verilog"},
                "top": "sim_fail",
                "engine": "iverilog",
                "trace": "none",
            },
        )
        assert failed_marker.structured_content["ok"] is False
        assert "TEST FAILED" in failed_marker.structured_content["stdout_tail"]

        timed_out = await _call(
            session,
            "cad_simulate",
            {
                "source": {"files": ["tb/hang.v"], "language": "verilog"},
                "top": "hang",
                "engine": "iverilog",
                "trace": "none",
                "timeout_s": 0.5,
            },
        )
        assert timed_out.structured_content["ok"] is False
        assert timed_out.structured_content["timed_out"] is True
        assert timed_out.structured_content["job"]["status"] == "timeout"

        missing = await _call(
            session,
            "cad_check",
            {"source": {"filelist": "missing.f", "language": "verilog"}, "top": "blinky", "engines": ["iverilog"]},
        )
        assert missing.structured_content["ok"] is False
        assert missing.structured_content["error"]["code"] == "invalid_input"
        assert "does_not_exist.sv" in missing.structured_content["error"]["message"]

        profile = await _call(session, "cad_project", {"action": "inspect"})
        assert profile.structured_content["profile"]["top_candidates"] == ["blinky"]
        assert profile.structured_content["profile"]["formal_files"] == ["formal/blinky_formal.sv"]

        netlist = await _call(session, "cad_netlist", {"action": "stats", "path": "library-netlist.json"})
        assert netlist.structured_content["module_count"] == 1
        netlist = await _call(
            session,
            "cad_netlist",
            {"action": "stats", "path": "library-netlist.json", "include_library": True},
        )
        assert netlist.structured_content["module_count"] == 2
