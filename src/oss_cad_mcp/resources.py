"""MCP resources exposing the catalog, boards, jobs, and artifacts."""

from __future__ import annotations

import json
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.boards import list_boards
from oss_cad_mcp.workflows.common import read_tail


def register_resources(mcp: MCPServer, get_app: Callable[[], AppContext | None]) -> None:
    @mcp.resource("cad://catalog", mime_type="application/json", description="Complete OSS CAD Suite catalog.")
    def catalog_resource() -> str:
        app = get_app()
        if app is None or app.registry is None:
            return json.dumps({"error": "suite not available"})
        return app.registry.dump_json()

    @mcp.resource("cad://catalog/families", mime_type="application/json", description="Tool counts by family.")
    def catalog_families() -> str:
        app = get_app()
        if app is None or app.registry is None:
            return json.dumps({"error": "suite not available"})
        return json.dumps(app.registry.families(), indent=2)

    @mcp.resource("cad://boards", mime_type="application/json", description="Software-build board presets.")
    def boards_resource() -> str:
        return json.dumps([board.model_dump(mode="json") for board in list_boards()], indent=2)

    @mcp.resource("cad://help/{tool}", mime_type="text/markdown", description="Curated help for a suite tool.")
    async def help_resource(tool: str) -> str:
        app = get_app()
        if app is None or app.registry is None:
            return "OSS CAD Suite is not available."
        help_info = await app.registry.help(tool)
        lines = [
            f"# {help_info.tool}",
            "",
            help_info.summary,
            "",
            f"- binary: `{help_info.binary}`",
            f"- version: `{help_info.version or 'unknown'}`",
            f"- risk: `{help_info.risk}`",
            f"- recommended workflow: `{help_info.recommended_tool or 'cad_tool_run'}`",
        ]
        if help_info.examples:
            lines.extend(["", "## Examples", *[f"- `{example}`" for example in help_info.examples]])
        if help_info.notes:
            lines.extend(["", "## Notes", *[f"- {note}" for note in help_info.notes]])
        if help_info.help_text:
            lines.extend(["", "## Help output", "```text", help_info.help_text, "```"])
        return "\n".join(lines)

    @mcp.resource("cad://jobs/{job_id}", mime_type="application/json", description="Job manifest.")
    def job_resource(job_id: str) -> str:
        app = get_app()
        if app is None:
            return json.dumps({"error": "server not started"})
        job = app.jobs.get(job_id)
        if job is None:
            return json.dumps({"error": f"unknown job: {job_id}"})
        return job.manifest_path.read_text(encoding="utf-8") if job.manifest_path.exists() else json.dumps({})

    @mcp.resource(
        "cad://jobs/{job_id}/log/{stream}",
        mime_type="text/plain",
        description="Job stdout, stderr, or events log.",
    )
    def job_log_resource(job_id: str, stream: str) -> str:
        app = get_app()
        if app is None:
            return "server not started"
        job = app.jobs.get(job_id)
        if job is None:
            return f"unknown job: {job_id}"
        paths = {"stdout": job.stdout_path, "stderr": job.stderr_path, "events": job.events_path}
        path = paths.get(stream)
        if path is None:
            return f"unknown stream: {stream}"
        return read_tail(path, max_bytes=65_536)

    @mcp.resource(
        "cad://artifacts/{job_id}/{artifact_id}",
        mime_type="text/plain",
        description="Text artifact content or JSON metadata.",
    )
    def artifact_resource(job_id: str, artifact_id: str) -> str:
        app = get_app()
        if app is None:
            return "server not started"
        job = app.jobs.get(job_id)
        if job is None:
            return f"unknown job: {job_id}"
        artifact = app.artifacts.find(job, artifact_id)
        if artifact is None:
            return f"unknown artifact: {artifact_id}"
        try:
            text, truncated, total = app.artifacts.read_text(artifact, max_bytes=65_536)
        except Exception as exc:
            return json.dumps({"artifact": artifact.model_dump(mode="json"), "error": str(exc)})
        if artifact.mime_type.startswith("text/") or artifact.mime_type in {"application/json", "application/x-vcd"}:
            suffix = f"\n... truncated at {len(text)} of {total} bytes" if truncated else ""
            return text + suffix
        return json.dumps(artifact.model_dump(mode="json"), indent=2)
