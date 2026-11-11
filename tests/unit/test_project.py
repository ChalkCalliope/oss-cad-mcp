from __future__ import annotations

from pathlib import Path

import pytest

from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.exec.paths import PathPolicy
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.project import inspect_project, parse_filelist, resolve_sources


def test_project_detection(project: Path) -> None:
    policy = PathPolicy.create(project)
    profile = inspect_project(project, paths=policy)
    assert "blinky" in profile.modules
    assert "blinky" in profile.top_candidates
    assert "tb_blinky" in profile.testbenches
    assert "clk" in profile.clocks
    assert any(item.kind == "pcf" for item in profile.constraints)
    assert profile.source_files == ["rtl/blinky.sv"]
    assert profile.testbench_files == ["tb/tb_blinky.sv"]


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


def test_filelist_is_an_explicit_source_set(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "tb").mkdir()
    (tmp_path / "rtl" / "dut.sv").write_text("module dut; endmodule")
    (tmp_path / "tb" / "tb_dut.sv").write_text("module tb_dut; endmodule")
    filelist = tmp_path / "files.f"
    filelist.write_text("rtl/dut.sv\n")

    policy = PathPolicy.create(tmp_path)
    resolved = resolve_sources(
        SourceSet(filelist=str(filelist), language="systemverilog"),
        project=None,
        paths=policy,
    )

    assert [path.name for path in resolved.files] == ["dut.sv"]


def test_filelist_missing_entry_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "dut.sv").write_text("module dut; endmodule")
    filelist = tmp_path / "files.f"
    filelist.write_text("rtl/dut.sv\nrtl/missing.sv\n")

    with pytest.raises(InvalidInput) as excinfo:
        resolve_sources(
            SourceSet(filelist=str(filelist), language="systemverilog"),
            project=None,
            paths=PathPolicy.create(tmp_path),
        )

    assert "missing.sv" in str(excinfo.value)
    assert excinfo.value.details == {"missing": [str((tmp_path / "rtl" / "missing.sv").resolve())]}


def test_formal_wrappers_are_not_top_candidates(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "formal").mkdir()
    (tmp_path / "rtl" / "top.v").write_text("module top; endmodule\n", encoding="utf-8")
    (tmp_path / "formal" / "top_formal.sv").write_text("module top_formal; top dut(); endmodule\n", encoding="utf-8")
    (tmp_path / "oss-cad-project.toml").write_text(
        '[project]\ntop = "top"\nsources = ["rtl/**/*.v"]\n',
        encoding="utf-8",
    )

    profile = inspect_project(tmp_path, paths=PathPolicy.create(tmp_path))

    assert profile.top_candidates == ["top"]
    assert profile.formal_files == ["formal/top_formal.sv"]
    assert profile.source_files == ["rtl/top.v"]


def test_vhdl_project_detection(tmp_path: Path) -> None:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "tb").mkdir()
    (tmp_path / "rtl" / "pwm.vhd").write_text(
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
"""
    )
    (tmp_path / "tb" / "tb_pwm.vhd").write_text(
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
"""
    )

    profile = inspect_project(tmp_path, paths=PathPolicy.create(tmp_path))

    assert profile.top_candidates == ["pwm"]
    assert profile.testbenches == ["tb_pwm"]
    assert "clk" in profile.clocks
    assert profile.source_files == ["rtl/pwm.vhd"]
    assert profile.testbench_files == ["tb/tb_pwm.vhd"]


def test_project_root_under_build_directory(tmp_path: Path) -> None:
    root = tmp_path / "build" / "project"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "top.v").write_text("module top; endmodule\n")

    profile = inspect_project(root, paths=PathPolicy.create(root))

    assert profile.hdl_files == ["rtl/top.v"]
    assert profile.top_candidates == ["top"]
