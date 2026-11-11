"""OSS CAD Suite MCP server."""

from __future__ import annotations

__version__ = "0.1.0"
API_VERSION = 1

__all__ = ["API_VERSION", "__version__"]


def main() -> None:
    """Console-script entry point."""
    from oss_cad_mcp.cli import main as cli_main

    cli_main()
