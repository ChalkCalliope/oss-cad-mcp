from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.exec.paths import PathPolicy
from oss_cad_mcp.workflows.netlist import inspect_netlist


def _app(tmp_path: Path) -> AppContext:
    return cast(AppContext, SimpleNamespace(paths=PathPolicy.create(tmp_path)))


def _netlist(tmp_path: Path) -> str:
    data = {
        "modules": {
            "top": {
                "ports": {},
                "netnames": {"state": {"bits": [2]}, "other": {"bits": [3]}},
                "cells": {
                    "ff": {
                        "type": "$_DFF_P_",
                        "port_directions": {"Q": "output", "D": "input", "C": "input"},
                        "connections": {"Q": [2], "D": [3], "C": [4]},
                    },
                    "load": {
                        "type": "$_AND_",
                        "port_directions": {"A": "input", "B": "input", "Y": "output"},
                        "connections": {"A": [2], "B": [3], "Y": [5]},
                    },
                },
            },
            "other": {
                "ports": {},
                "netnames": {"state": {"bits": [10]}},
                "cells": {
                    "load2": {
                        "type": "$_NOT_",
                        "port_directions": {"A": "input", "Y": "output"},
                        "connections": {"A": [10], "Y": [11]},
                    }
                },
            },
        }
    }
    path = tmp_path / "netlist.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return "netlist.json"


def test_fanout_separates_nets_and_excludes_drivers(tmp_path: Path) -> None:
    app = _app(tmp_path)
    path = _netlist(tmp_path)

    report = inspect_netlist(app, action="fanout", path=path, name="state", limit=10)

    assert [match.module for match in report.matches] == ["other", "top"]
    assert [match.name for match in report.matches] == ["state", "state"]
    top = next(match for match in report.matches if match.module == "top")
    loads = top.details["loads"]
    assert [load["name"] for load in loads] == ["load"]
    assert loads[0]["details"]["port"] == "A"
    assert top.details["total_loads"] == 1
    assert report.truncated is False


def test_fanout_reports_truncation(tmp_path: Path) -> None:
    app = _app(tmp_path)
    path = _netlist(tmp_path)

    report = inspect_netlist(app, action="fanout", path=path, name="state", limit=1)

    assert len(report.matches) == 1
    assert report.truncated is True
    assert report.total_matches == 2
