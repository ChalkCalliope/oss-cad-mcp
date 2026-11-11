"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from oss_cad_mcp.app import AppContext, build_app
from oss_cad_mcp.config import Settings


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "tb").mkdir()
    (tmp_path / "rtl" / "blinky.sv").write_text(
        """\
module blinky(input logic clk, input logic rst, output logic [3:0] cnt);
  always_ff @(posedge clk) begin
    if (rst) cnt <= '0;
    else cnt <= cnt + 1'b1;
  end
endmodule
""",
        encoding="utf-8",
    )
    (tmp_path / "tb" / "tb_blinky.sv").write_text(
        """\
module tb_blinky;
  logic clk = 0;
  logic rst = 1;
  logic [3:0] cnt;
  blinky uut(.clk(clk), .rst(rst), .cnt(cnt));
  always #5 clk = ~clk;
  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, tb_blinky);
    repeat (3) @(posedge clk);
    rst <= 0;
    repeat (20) @(posedge clk);
    $display("cnt=%0d", cnt);
    $finish;
  end
endmodule
""",
        encoding="utf-8",
    )
    (tmp_path / "pins.pcf").write_text(
        """\
set_io clk 35
set_io rst 11
set_io cnt[0] 39
set_io cnt[1] 40
set_io cnt[2] 41
set_io cnt[3] 42
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def settings(project: Path) -> Settings:
    return Settings(workspace=project, default_timeout_s=180.0)


@pytest.fixture
def app(settings: Settings) -> Iterator[AppContext]:
    if not shutil.which("yosys") and not Path.home().joinpath("Kits/oss-cad-suite/bin/yosys").exists():
        pytest.skip("OSS CAD Suite is not installed")
    context = build_app(settings)
    yield context
    import asyncio

    asyncio.run(context.jobs.shutdown())


def make_ctx(app: AppContext):
    """Minimal Context stand-in for calling tool functions directly."""

    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires an installed OSS CAD Suite")
