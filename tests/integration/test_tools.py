from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.tools.jobs import cad_artifact, cad_job
from oss_cad_mcp.tools.schemas import (
    ArtifactRequest,
    JobRequest,
    ProjectRequest,
    StatusRequest,
    ToolHelpRequest,
    ToolRunRequest,
    ToolSearchRequest,
)
from oss_cad_mcp.tools.system import cad_project, cad_status, cad_tool_help, cad_tool_run, cad_tool_search
from oss_cad_mcp.workflows.netlist import inspect_netlist
from oss_cad_mcp.workflows.synthesize import run_synthesize
from tests.conftest import make_ctx


@pytest.mark.integration
async def test_project_init_and_validate(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_project(ctx, ProjectRequest(action="init", write=True))
    assert result.ok
    config_path = app.workspace / "oss-cad-project.toml"
    assert config_path.exists()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert config["project"]["sources"] == ["rtl/blinky.sv"]
    assert config["project"]["testbenches"] == ["tb/tb_blinky.sv"]
    assert config["sim"]["testbench"] == "tb_blinky"
    validated = await cad_project(ctx, ProjectRequest(action="validate"))
    assert validated.ok
    assert validated.profile is not None
    assert validated.profile.modules


@pytest.mark.integration
async def test_project_init_vhdl(app: AppContext) -> None:
    root = app.workspace / "vhdl_init"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "pwm.vhd").write_text(
        """\
library ieee;
use ieee.std_logic_1164.all;
entity pwm is
    port (clk : in std_logic; pwm_out : out std_logic);
end entity pwm;
architecture rtl of pwm is
begin
    process (clk) begin
        if rising_edge(clk) then
            pwm_out <= '1';
        end if;
    end process;
end architecture rtl;
""",
        encoding="utf-8",
    )
    (root / "tb" / "tb_pwm.vhd").write_text(
        """\
library ieee;
use ieee.std_logic_1164.all;
entity tb_pwm is end entity tb_pwm;
architecture sim of tb_pwm is
    signal clk : std_logic := '0';
    signal pwm_out : std_logic;
begin
    dut : entity work.pwm port map (clk => clk, pwm_out => pwm_out);
end architecture sim;
""",
        encoding="utf-8",
    )

    ctx = make_ctx(app)
    result = await cad_project(ctx, ProjectRequest(action="init", root="vhdl_init", write=True))
    assert result.ok
    config_path = root / "oss-cad-project.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert config["project"]["top"] == "pwm"
    assert config["project"]["sources"] == ["rtl/pwm.vhd"]
    assert config["project"]["testbenches"] == ["tb/tb_pwm.vhd"]
    assert config["sim"]["testbench"] == "tb_pwm"

    validated = await cad_project(ctx, ProjectRequest(action="validate", root="vhdl_init"))
    assert validated.ok


@pytest.mark.integration
async def test_raw_tool_run_and_job_log(app: AppContext) -> None:
    app.settings.allow_raw = True
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
    app.settings.allow_raw = True
    ctx = make_ctx(app)
    result = await cad_tool_run(ctx, ToolRunRequest(tool="openFPGALoader", args=["--detect"]))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "hardware_disabled"
    jobs = await cad_job(ctx, JobRequest(action="list"))
    blocked = next(job for job in jobs.jobs if job.id == result.job.id)
    assert blocked.status == "failed"


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


@pytest.mark.integration
async def test_artifact_path_precedes_implicit_artifact(app: AppContext) -> None:
    artifact_path = app.settings.build_path / "existing-artifact.txt"
    artifact_path.write_text("existing artifact\n", encoding="utf-8")
    job = app.jobs.create("test")
    app.artifacts.register(job, artifact_path, kind="report")

    requested = app.workspace / "requested.txt"
    requested.write_text("requested path\n", encoding="utf-8")

    ctx = make_ctx(app)
    read = await cad_artifact(ctx, ArtifactRequest(action="read_text", path="requested.txt"))
    assert read.ok
    assert read.text == "requested path\n"
    assert read.job is None


@pytest.mark.integration
async def test_artifact_list_prefers_job_with_artifacts(app: AppContext) -> None:
    artifact_path = app.settings.build_path / "listed-artifact.txt"
    artifact_path.write_text("listed\n", encoding="utf-8")
    with_artifacts = app.jobs.create("test")
    app.artifacts.register(with_artifacts, artifact_path, kind="report")
    await asyncio.sleep(0.01)
    app.jobs.create("test")

    ctx = make_ctx(app)
    listed = await cad_artifact(ctx, ArtifactRequest(action="list"))
    assert listed.ok
    assert listed.job is not None
    assert listed.job.id == with_artifacts.id
    assert listed.artifacts


@pytest.mark.integration
async def test_cancel_running_job_returns_normally(app: AppContext) -> None:
    started = asyncio.Event()

    async def factory(job):
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(app.jobs.execute("sleep", factory, timeout_s=60))
    await asyncio.wait_for(started.wait(), timeout=2)
    job = next(item for item in app.jobs.list(5) if item.kind == "sleep")

    ctx = make_ctx(app)
    cancelled = await cad_job(ctx, JobRequest(action="cancel", job_id=job.id))
    assert cancelled.ok
    assert cancelled.job is not None
    assert cancelled.job.status == "cancelled"

    completed = await asyncio.wait_for(task, timeout=2)
    assert completed.status == "cancelled"
    status = await cad_status(ctx, StatusRequest(deep=False))
    assert status.ok


@pytest.mark.integration
async def test_tool_help_unknown_returns_structured_error(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_tool_help(ctx, ToolHelpRequest(tool="does-not-exist"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "tool_not_found"


@pytest.mark.integration
def test_fanout_missing_net_returns_no_matches(app: AppContext) -> None:
    netlist = app.workspace / "netlist.json"
    netlist.write_text(
        json.dumps(
            {
                "modules": {
                    "top": {
                        "ports": {},
                        "cells": {},
                        "netnames": {"known": {"bits": [2]}},
                    },
                    "$internal": {
                        "ports": {},
                        "cells": {},
                        "netnames": {"internal_net": {"bits": [3]}},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = inspect_netlist(app, action="fanout", path="netlist.json", name="missing")
    assert report.ok
    assert report.matches == []
    assert report.module_count == 1

    with_internal = inspect_netlist(
        app,
        action="stats",
        path="netlist.json",
        include_internal=True,
    )
    assert with_internal.module_count == 2


@pytest.mark.integration
async def test_project_root_error_is_structured(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_project(ctx, ProjectRequest(action="inspect", root="/"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "path_not_allowed"


@pytest.mark.integration
async def test_invalid_project_toml_has_project_config_code(app: AppContext) -> None:
    (app.workspace / "oss-cad-project.toml").write_text("[project\nname = 'broken'\n", encoding="utf-8")
    ctx = make_ctx(app)
    result = await cad_project(ctx, ProjectRequest(action="inspect"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "project_config_error"


@pytest.mark.integration
async def test_tool_search_total_is_not_page_limited(app: AppContext) -> None:
    ctx = make_ctx(app)
    page = await cad_tool_search(ctx, ToolSearchRequest(family="formal", limit=5))
    full = await cad_tool_search(ctx, ToolSearchRequest(family="formal", limit=200))
    assert page.ok
    assert len(page.tools) == 5
    assert page.total == full.total
    assert page.total > len(page.tools)


@pytest.mark.integration
def test_netlist_hides_blackbox_library_modules(app: AppContext) -> None:
    netlist = app.workspace / "library-netlist.json"
    netlist.write_text(
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

    design = inspect_netlist(app, action="stats", path="library-netlist.json")
    assert design.module_count == 1
    assert [module.name for module in design.modules] == ["top"]

    library = inspect_netlist(app, action="stats", path="library-netlist.json", include_library=True)
    assert library.module_count == 2


def _no_suite_app(tmp_path):
    from oss_cad_mcp.app import build_app
    from oss_cad_mcp.config import Settings

    app = build_app(Settings(workspace=tmp_path))
    app.suite = None
    app.registry = None
    app.suite_error = "simulated missing suite"
    return app


async def test_no_suite_discovery_tools_return_structured_errors(tmp_path) -> None:
    app = _no_suite_app(tmp_path)
    ctx = make_ctx(app)
    try:
        search = await cad_tool_search(ctx, ToolSearchRequest())
        assert search.ok is False
        assert search.error is not None
        assert search.error.code == "suite_not_found"

        help_result = await cad_tool_help(ctx, ToolHelpRequest(tool="yosys"))
        assert help_result.ok is False
        assert help_result.error is not None
        assert help_result.error.code == "suite_not_found"

        app.settings.allow_raw = True
        run = await cad_tool_run(ctx, ToolRunRequest(tool="yosys", args=["-V"]))
        assert run.ok is False
        assert run.error is not None
        assert run.error.code == "suite_not_found"
    finally:
        await app.jobs.shutdown()


def test_cli_no_suite_has_no_traceback(tmp_path) -> None:
    import os
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "OSS_CAD_SUITE_ROOT": "/nonexistent-suite",
        "PYTHONPATH": str(repo / "src"),
    }
    for args in (["catalog", "yosys"], ["tool-help", "yosys"]):
        proc = subprocess.run(
            [sys.executable, "-m", "oss_cad_mcp", *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=repo,
            timeout=60,
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
        assert "suite" in proc.stderr.lower()


@pytest.mark.integration
async def test_project_init_reopens_with_root_workspace(app: AppContext) -> None:
    from oss_cad_mcp.app import build_app
    from oss_cad_mcp.config import Settings

    root = app.workspace / "reopen"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "counter.v").write_text(
        "module counter(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule\n",
        encoding="utf-8",
    )
    (root / "tb" / "tb_counter.v").write_text(
        "module tb_counter; reg clk; wire q; counter uut(.clk(clk), .q(q)); endmodule\n",
        encoding="utf-8",
    )

    ctx = make_ctx(app)
    init = await cad_project(ctx, ProjectRequest(action="init", root="reopen", write=True))
    assert init.ok
    config = tomllib.loads((root / "oss-cad-project.toml").read_text(encoding="utf-8"))
    assert config["project"]["sources"] == ["rtl/counter.v"]
    assert config["project"]["testbenches"] == ["tb/tb_counter.v"]

    reopened = build_app(Settings(workspace=root))
    try:
        reopened_ctx = make_ctx(reopened)
        validated = await cad_project(reopened_ctx, ProjectRequest(action="validate"))
        assert validated.ok
    finally:
        await reopened.jobs.shutdown()


@pytest.mark.integration
async def test_binary_read_text_is_rejected(app: AppContext) -> None:
    ctx = make_ctx(app)
    (app.workspace / "blob.bin").write_bytes(b"\x00\x01\x02\xfe\xffbinary")
    result = await cad_artifact(ctx, ArtifactRequest(action="read_text", path="blob.bin"))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "binary_artifact"
    assert result.total_bytes == 11
    assert result.value

    stat = await cad_artifact(ctx, ArtifactRequest(action="stat", path="blob.bin"))
    assert stat.ok is True


@pytest.mark.integration
async def test_raw_execution_disabled_by_default(app: AppContext) -> None:
    ctx = make_ctx(app)
    result = await cad_tool_run(ctx, ToolRunRequest(tool="yosys", args=["-V"]))
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "raw_disabled"
