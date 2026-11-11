from __future__ import annotations

from pathlib import Path

from oss_cad_mcp.exec.paths import PathPolicy
from oss_cad_mcp.project import inspect_project, parse_filelist


def test_project_detection(project: Path) -> None:
    policy = PathPolicy.create(project)
    profile = inspect_project(project, paths=policy)
    assert "blinky" in profile.modules
    assert "blinky" in profile.top_candidates
    assert "tb_blinky" in profile.testbenches
    assert "clk" in profile.clocks
    assert any(item.kind == "pcf" for item in profile.constraints)


def test_filelist_parser(tmp_path: Path) -> None:
    (tmp_path / "a.sv").write_text("module a; endmodule")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.sv").write_text("module b; endmodule")
    filelist = tmp_path / "files.f"
    filelist.write_text(
        "\n".join(
            [
                "+incdir+include",
                "+define+SYNTHESIS=1",
                "a.sv",
                "-f nested/extra.f",
            ]
        )
    )
    (tmp_path / "nested" / "extra.f").write_text("b.sv")
    files, includes, defines = parse_filelist(filelist)
    assert {path.name for path in files} == {"a.sv", "b.sv"}
    assert includes and includes[0].name == "include"
    assert defines == {"SYNTHESIS": "1"}
