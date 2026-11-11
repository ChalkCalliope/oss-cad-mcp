"""Register MCP tools on the server."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from oss_cad_mcp.tools.jobs import cad_artifact, cad_job
from oss_cad_mcp.tools.system import (
    cad_project,
    cad_status,
    cad_tool_help,
    cad_tool_run,
    cad_tool_search,
)
from oss_cad_mcp.tools.workflows import (
    cad_check,
    cad_fpga_build,
    cad_netlist,
    cad_simulate,
    cad_synthesize,
    cad_timing,
    cad_verify,
    cad_waveform,
)


def register_tools(mcp: MCPServer) -> None:
    """Register the curated v0.1 software-focused tool surface."""

    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_status)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(
        cad_project
    )
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_check)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(
        cad_simulate
    )
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(
        cad_synthesize
    )
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(
        cad_verify
    )
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(
        cad_fpga_build
    )
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(
        cad_waveform
    )
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_netlist)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_timing)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))(cad_job)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_artifact)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_tool_search)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(cad_tool_help)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))(
        cad_tool_run
    )
