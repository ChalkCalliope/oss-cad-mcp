from __future__ import annotations

import pytest

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.tools.jobs import cad_artifact, cad_job
from oss_cad_mcp.tools.schemas import (
    ArtifactRequest,
    JobRequest,
    ProjectRequest,
    ToolRunRequest,
)
from oss_cad_mcp.tools.system import cad_project, cad_tool_run
from oss_cad_mcp.workflows.synthesize import run_synthesize
from tests.conftest import make_ctx


@pytest.mark.integration
async def test_project_init_and_validate(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_project(ctx, ProjectRequest(action="init", write=True))
    assert result.ok
    assert (app.workspace / "oss-cad-project.toml").exists()
    validated = await cad_project(ctx, ProjectRequest(action="validate"))
    assert validated.profile is not None
    assert validated.profile.modules


@pytest.mark.integration
async def test_raw_tool_run_and_job_log(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_tool_run(ctx, ToolRunRequest(tool="yosys", args=["-V"]))
    assert result.ok
    assert "Yosys" in result.stdout_tail
    jobs = await cad_job(ctx, JobRequest(action="list"))
    assert jobs.jobs
    log = await cad_job(
        ctx,
        JobRequest(action="log", job_id=result.job.id, stream="stdout", tail_lines=20),
    )
    assert "Yosys" in (log.log or "")


@pytest.mark.integration
async def test_hardware_is_blocked(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_tool_run(ctx, ToolRunRequest(tool="openFPGALoader", args=["--detect"]))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "hardware_disabled"


@pytest.mark.integration
async def test_artifact_text_read(app: AppContext) -> None:
    async def factory(job):
        return await run_synthesize(
            app,
            job,
            source=SourceSet(files=["rtl/blinky.sv"], language="systemverilog"),
            top="blinky",
            target="ice40",
            timeout_s=120,
        )

    job = await app.jobs.execute("synth", factory, timeout_s=120)
    assert job.status == "passed"
    assert job.result is not None
    artifact = job.result.netlist
    assert artifact is not None
    ctx = make_ctx(app)
    read = await cad_artifact(
        ctx,
        ArtifactRequest(
            action="read_text",
            job_id=job.id,
            artifact_id=artifact.id,
            max_bytes=200,
        ),
    )
    assert read.ok
    assert read.text is not None
    assert read.text.lstrip().startswith("{")
