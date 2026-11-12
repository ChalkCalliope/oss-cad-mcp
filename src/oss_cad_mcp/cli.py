"""Command-line entry point for oss-cad-mcp."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console

from oss_cad_mcp import __version__
from oss_cad_mcp.app import build_app
from oss_cad_mcp.config import load_settings
from oss_cad_mcp.errors import CadError
from oss_cad_mcp.server import create_server
from oss_cad_mcp.tools.system import build_status

app = typer.Typer(
    name="oss-cad-mcp",
    help="Intent-level MCP server for OSS CAD Suite.",
    add_completion=False,
    invoke_without_command=True,
)
console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def callback(
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """OSS CAD Suite MCP server."""


@app.command()
def serve(
    suite_root: Path | None = typer.Option(None, "--suite-root", help="OSS CAD Suite directory."),
    workspace: Path | None = typer.Option(None, "--workspace", help="Project workspace."),
    profile: str = typer.Option("core", "--profile", help="core, standard, or full."),
    transport: str = typer.Option("stdio", "--transport", help="stdio or streamable-http."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    config_file: Path | None = typer.Option(None, "--config", help="Server TOML config."),
) -> None:
    """Serve MCP over stdio (default) or streamable HTTP."""
    settings = load_settings(
        config_file=config_file,
        suite_root=suite_root,
        workspace=workspace,
        profile=profile,
    )
    server = create_server(settings)
    if transport == "stdio":
        server.run(transport="stdio")
    elif transport == "streamable-http":
        server.run(transport="streamable-http", host=host, port=port)
    else:
        raise typer.BadParameter("transport must be 'stdio' or 'streamable-http'")


@app.command()
def doctor(
    suite_root: Path | None = typer.Option(None, "--suite-root"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    deep: bool = typer.Option(False, "--deep", help="Probe all tools and the sandbox."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    config_file: Path | None = typer.Option(None, "--config"),
) -> None:
    """Check suite discovery, workspace, host toolchain, and capabilities."""
    settings = load_settings(config_file=config_file, suite_root=suite_root, workspace=workspace)
    ctx = build_app(settings)
    report = build_status(ctx, deep=deep)
    asyncio.run(ctx.jobs.shutdown())
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        console.print(f"[bold]oss-cad-mcp[/bold] {report.package_version}")
        console.print(f"suite: {report.suite_root or 'not found'} ({report.suite_version or 'unknown'})")
        console.print(f"platform: {report.platform} [{report.platform_status}]")
        console.print(f"workspace: {report.workspace}")
        console.print(f"capabilities: {report.capabilities.model_dump()}")
        console.print(f"toolchain: {report.toolchain.model_dump()}")
        for warning in report.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")
    if not report.ok:
        raise typer.Exit(1)


@app.command("catalog")
def catalog_command(
    query: str = typer.Argument("", help="Search query."),
    family: str | None = typer.Option(None, "--family"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    include_internal: bool = typer.Option(False, "--include-internal"),
    include_hardware: bool = typer.Option(False, "--include-hardware"),
    json_output: bool = typer.Option(False, "--json"),
    suite_root: Path | None = typer.Option(None, "--suite-root"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    config_file: Path | None = typer.Option(None, "--config"),
) -> None:
    """Search the OSS CAD Suite tool catalog."""
    settings = load_settings(config_file=config_file, suite_root=suite_root, workspace=workspace)
    ctx = build_app(settings)
    try:
        registry = ctx.require_registry()
    except CadError as exc:
        console.print(f"[red]error:[/red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]hint:[/yellow] {exc.hint}")
        asyncio.run(ctx.jobs.shutdown())
        raise typer.Exit(1) from exc
    entries = registry.search(
        query,
        family=family,
        include_internal=include_internal,
        include_hardware=include_hardware,
        limit=limit,
    )
    if json_output:
        typer.echo(json.dumps([entry.to_info().model_dump(mode="json") for entry in entries], indent=2))
    else:
        for entry in entries:
            console.print(f"[bold]{entry.name}[/bold] [{entry.family}] {entry.summary}")
    asyncio.run(ctx.jobs.shutdown())


@app.command("tool-help")
def tool_help_command(
    tool: str,
    suite_root: Path | None = typer.Option(None, "--suite-root"),
    workspace: Path | None = typer.Option(None, "--workspace"),
    config_file: Path | None = typer.Option(None, "--config"),
) -> None:
    """Print curated help for one suite tool."""
    settings = load_settings(config_file=config_file, suite_root=suite_root, workspace=workspace)
    ctx = build_app(settings)

    async def run() -> None:
        try:
            help_info = await ctx.require_registry().help(tool)
        except CadError as exc:
            typer.echo(f"error: {exc.message}", err=True)
            if exc.hint:
                typer.echo(f"hint: {exc.hint}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"{help_info.tool}: {help_info.summary}")
        if help_info.version:
            typer.echo(f"version: {help_info.version}")
        if help_info.usage:
            typer.echo(f"usage: {help_info.usage}")
        if help_info.help_text:
            typer.echo(help_info.help_text)
        for note in help_info.notes:
            typer.echo(f"note: {note}")

    asyncio.run(run())
    asyncio.run(ctx.jobs.shutdown())


@app.command("update-suite")
def update_suite_command(
    path: Path | None = typer.Argument(None, help="Suite root to install/update."),
    check: bool = typer.Option(False, "--check", help="Show available version without downloading."),
    force: bool = typer.Option(False, "--force", help="Reinstall; replace a non-suite directory."),
    no_backup: bool = typer.Option(False, "--no-backup", help="Remove the previous installation."),
    no_resume: bool = typer.Option(False, "--no-resume", help="Discard partial download."),
    no_validate: bool = typer.Option(False, "--no-validate", help="Skip the yosys -V runtime check."),
    release_tag: str | None = typer.Option(None, "--release-tag", help="Install a specific release tag."),
    timeout: float = typer.Option(60.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Download and install the latest daily OSS CAD Suite release."""
    from oss_cad_mcp.suite_update import UpdateError, UpdateOptions
    from oss_cad_mcp.suite_update import update_suite as run_update

    options = UpdateOptions(
        path=path,
        check=check,
        force=force,
        keep_backup=not no_backup,
        resume=not no_resume,
        release_tag=release_tag,
        validate_runtime=not no_validate,
        timeout_s=timeout,
    )
    try:
        result = run_update(options)
    except (UpdateError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        typer.echo("Interrupted; a partial download may remain for resume.", err=True)
        raise typer.Exit(130) from exc
    typer.echo(result.message)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
