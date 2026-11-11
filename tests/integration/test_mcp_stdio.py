from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from oss_cad_mcp.app import AppContext


@pytest.mark.integration
async def test_mcp_stdio_lists_tools_and_calls_status(app: AppContext) -> None:
    suite_root = app.suite.root if app.suite else None
    if suite_root is None:
        pytest.skip("suite unavailable")
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "oss_cad_mcp",
            "serve",
            "--workspace",
            str(app.workspace),
        ],
        env={**os.environ, "OSS_CAD_SUITE_ROOT": str(suite_root)},
        cwd=Path(__file__).resolve().parents[2],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert "cad_status" in names
        assert "cad_check" in names
        assert "cad_fpga_build" in names
        result = await session.call_tool("cad_status", {"request": {"deep": False}})
        assert not result.is_error
        assert result.structured_content
        assert result.structured_content["ok"] is True
        assert result.structured_content["suite_version"]
