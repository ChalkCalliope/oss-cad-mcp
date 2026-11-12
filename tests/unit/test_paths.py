from __future__ import annotations

from pathlib import Path

import pytest

from oss_cad_mcp.errors import PathNotAllowed
from oss_cad_mcp.exec.paths import PathPolicy


def test_relative_paths_resolve_to_workspace(tmp_path: Path) -> None:
    policy = PathPolicy.create(tmp_path)
    target = tmp_path / "rtl" / "top.sv"
    target.parent.mkdir()
    target.write_text("module top; endmodule")
    assert policy.resolve_read("rtl/top.sv") == target.resolve()
    assert policy.display(target) == "rtl/top.sv"


def test_outside_path_is_rejected(tmp_path: Path) -> None:
    policy = PathPolicy.create(tmp_path / "ws")
    with pytest.raises(PathNotAllowed):
        policy.resolve_read("/etc/passwd")


def test_glob_and_missing_pattern(tmp_path: Path) -> None:
    policy = PathPolicy.create(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.sv").write_text("module x; endmodule")
    (tmp_path / "a" / "y.sv").write_text("module y; endmodule")
    assert len(policy.glob("a/*.sv")) == 2
    with pytest.raises(PathNotAllowed):
        policy.glob("missing/*.sv", must_exist=True)


def test_raw_argv_outside_paths_are_rejected(tmp_path: Path) -> None:
    policy = PathPolicy.create(tmp_path)
    (tmp_path / "design.asc").write_text("dummy")

    policy.validate_argv_paths(["design.asc"], cwd=tmp_path)
    policy.validate_argv_paths(["-o", "out.bin"], cwd=tmp_path)

    with pytest.raises(PathNotAllowed):
        policy.validate_argv_paths(["/tmp/outside.bin"], cwd=tmp_path)
    with pytest.raises(PathNotAllowed):
        policy.validate_argv_paths(["-o", "/tmp/outside.bin"], cwd=tmp_path)
    with pytest.raises(PathNotAllowed):
        policy.validate_argv_paths(["../outside.bin"], cwd=tmp_path)
    with pytest.raises(PathNotAllowed):
        policy.validate_argv_paths(["+incdir+/tmp"], cwd=tmp_path)
    with pytest.raises(PathNotAllowed):
        policy.validate_argv_paths(["-p", "read_verilog /tmp/outside.v"], cwd=tmp_path)
