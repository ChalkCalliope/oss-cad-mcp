"""MCP server factory and lifespan."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer

from oss_cad_mcp import __version__
from oss_cad_mcp.app import AppContext, build_app
from oss_cad_mcp.config import Settings, load_settings
from oss_cad_mcp.prompts import register_prompts
from oss_cad_mcp.resources import register_resources
from oss_cad_mcp.tools import register_tools

INSTRUCTIONS = """\
You are connected to an OSS CAD Suite toolchain through oss-cad-mcp.

Prefer intent-level tools over raw commands:
- cad_status: check suite/workspace readiness first when unsure.
- cad_project: inspect or validate the project and discover tops/testbenches/constraints.
- cad_check: parse, lint, or elaborate SystemVerilog/Verilog/VHDL.
- cad_simulate: compile and run a testbench; returns diagnostics and a waveform artifact.
- cad_synthesize: Yosys synthesis with structured cell statistics.
- cad_verify: SBY formal proof/BMC/cover, or EQY/MCY with an existing config.
- cad_fpga_build: software-only iCE40/ECP5 synth -> PnR -> pack flow.
- cad_waveform / cad_netlist / cad_timing: inspect generated artifacts.
- cad_job: poll long-running jobs; a timed-out call returns a running job handle.
- cad_tool_search / cad_tool_help / cad_tool_run: progressive disclosure for the rest of
  the 155-binary catalog.

Use cad_tool_run only when no workflow fits. Do not guess CLI flags; use cad_tool_help.
Each tool accepts one nested `request` object matching its published schema; for example
`cad_status` is called with `{"request": {"deep": false}}`.
Read artifact paths instead of re-running expensive tools. Hardware programming is
intentionally disabled in v0.1. Verified platform: Ubuntu 26.04 on WSL2 x86_64;
macOS and native Linux are not verified for full support.
"""


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_server(settings: Settings | None = None) -> MCPServer:
    effective = settings or load_settings()
    configure_logging(effective.log_level)
    app_ref: dict[str, AppContext] = {}

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
        app = build_app(effective)
        app_ref["app"] = app
        try:
            yield app
        finally:
            app_ref.pop("app", None)
            await app.jobs.shutdown()

    mcp = MCPServer(
        name="oss-cad-mcp",
        title="OSS CAD Suite MCP",
        description="Intent-level HDL workflows over OSS CAD Suite.",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        log_level=effective.log_level.upper(),  # type: ignore[arg-type]
    )
    register_tools(mcp)
    register_resources(mcp, lambda: app_ref.get("app"))
    register_prompts(mcp)
    return mcp


mcp = create_server()

__all__ = ["INSTRUCTIONS", "create_server", "mcp"]


def run(transport: str = "stdio", **kwargs: Any) -> None:
    mcp.run(transport=transport, **kwargs)  # type: ignore[arg-type]
