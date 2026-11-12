from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.parsers import icetime, iverilog, sby, slang, verilator, yosys
from oss_cad_mcp.parsers.vcd import parse_header, scan, validate_vcd


def test_slang_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "diag.json"
    path.write_text(
        json.dumps(
            [
                {
                    "severity": "error",
                    "message": "expected expression",
                    "code": "ExpectedExpression",
                    "location": str(tmp_path / "bad.sv") + ":2:23",
                }
            ]
        )
    )
    diagnostics = slang.parse_diagnostics(path, base=tmp_path)
    assert diagnostics[0].engine == "slang"
    assert diagnostics[0].line == 2
    assert diagnostics[0].column == 23
    assert diagnostics[0].file == "bad.sv"


def test_verilator_sarif_filters_synthetic_error(tmp_path: Path) -> None:
    path = tmp_path / "diag.sarif.json"
    path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "results": [
                            {
                                "level": "warning",
                                "message": {"text": "width mismatch"},
                                "ruleId": "WIDTH",
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": (tmp_path / "top.sv").as_uri()},
                                            "region": {"startLine": 3, "startColumn": 4, "endColumn": 8},
                                        }
                                    }
                                ],
                            },
                            {"level": "error", "message": {"text": "Exiting due to 1 error(s)"}},
                        ]
                    }
                ]
            }
        )
    )
    diagnostics = verilator.parse_sarif(path, base=tmp_path)
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == "warning"
    assert diagnostics[0].file == "top.sv"
    assert diagnostics[0].line == 3


def test_yosys_stat(tmp_path: Path) -> None:
    path = tmp_path / "stat.json"
    path.write_text(
        json.dumps(
            {
                "modules": {"top": {"num_cells_by_type": {"SB_LUT4": 2, "SB_DFF": 1}}},
                "design": {
                    "num_cells": 3,
                    "num_wires": 5,
                    "num_wire_bits": 8,
                    "modules": {},
                },
            }
        )
    )
    summary = yosys.summarize_stat(yosys.parse_stat_json(path))
    assert summary["num_cells"] == 3
    assert summary["cells_by_type"] == {"SB_LUT4": 2, "SB_DFF": 1}


def test_nextpnr_report(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            {
                "fmax": {"clk": {"achieved": 120.0, "constraint": 50.0}},
                "utilization": {"LC": {"used": 10, "available": 100}},
                "critical_paths": [
                    {
                        "from": "clk",
                        "to": "out",
                        "path": [
                            {"delay": 1.0, "type": "logic", "from": {"cell": "a"}, "to": {"cell": "b"}},
                            {"delay": 2.0, "type": "routing", "from": {"cell": "b"}, "to": {"cell": "c"}},
                        ],
                    }
                ],
            }
        )
    )
    from oss_cad_mcp.parsers.nextpnr import parse_report

    timing, utilization = parse_report(path)
    assert timing.met is True
    assert timing.max_frequency_mhz == 120.0
    assert utilization["LC"].used == 10
    assert timing.critical_paths[0].delay_ns == 3.0


def test_icetime_json(tmp_path: Path) -> None:
    path = tmp_path / "timing.json"
    path.write_text(
        json.dumps(
            [
                [
                    {"cell": "a", "cell_type": "LUT", "delay_ns": 1.0},
                    {"cell": "b", "cell_type": "DFF", "delay_ns": 2.5},
                ]
            ]
        )
    )
    report = icetime.parse_icetime(path, "Timing estimate: 2.5 ns (400 MHz)", clock_mhz=50)
    assert report.max_frequency_mhz == 400.0
    assert report.met is True
    assert report.critical_paths[0].delay_ns == 2.5


def test_sby_status() -> None:
    text = "\n".join(
        [
            "PASS",
            json.dumps(
                {
                    "name": "tb.assert_1",
                    "kind": "ASSERT",
                    "status": "PASS",
                    "depth": 4,
                    "location": "tb.sv:3.1-3.20",
                }
            ),
        ]
    )
    statuses = sby.parse_status_jsonl(text)
    assert statuses[0].name == "tb.assert_1"
    assert statuses[0].status == "PASS"
    assert sby.parse_summary("SBY DONE (PASS, rc=0)")[0] == "passed"


def test_text_diagnostics() -> None:
    iv = iverilog.parse_iverilog("top.sv:2: syntax error near ';'", base=Path.cwd())
    assert iv[0].engine == "iverilog"
    assert iv[0].line == 2
    gh = iverilog.parse_ghdl("top.vhd:4:5:error: no declaration for signal x", base=Path.cwd())
    assert gh[0].engine == "ghdl"
    assert gh[0].severity == "error"


def test_vcd_header_and_scan(tmp_path: Path) -> None:
    path = tmp_path / "wave.vcd"
    path.write_text(
        """\
$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 4 " cnt [3:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 "
#5
1!
#10
0!
b0001 "
"""
    )
    header = parse_header(path)
    assert header.timescale == "1ns"
    assert [var.path for var in header.vars] == ["tb.clk", "tb.cnt"]
    result = scan(path, signals=["tb.cnt"], max_samples=10)
    assert result.samples[-1].values["tb.cnt"] == "0001"
    assert result.stats["tb.cnt"]["transitions"] == 2


def test_slang_hierarchy_structured_output() -> None:
    text = "\n".join(
        [
            'Module="stream_top" Instance="stream_top" File="rtl/stream_top.sv" Parameters: DEPTH=32\'d8, WIDTH=32\'d32',
            'Module="axi_lite_regs" Instance="stream_top.u_regs" File="rtl/axi_lite_regs.sv"',
            'Module="stream_fifo" Instance="stream_top.u_fifo" File="rtl/stream_fifo.sv" Parameters: DEPTH=32\'d8, PTR_W=32\'d3',
            'Module="stream_fifo" Instance="stream_top.u_fifo.u_inner" File="rtl/stream_fifo.sv"',
            "rtl/top.sv:5:2: error: something failed",
            "",
            "Top level design units:",
            "    stream_top",
        ]
    )
    roots = slang.parse_hierarchy(text)
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "stream_top"
    assert root.module == "stream_top"
    assert root.parameters == {"DEPTH": "32'd8", "WIDTH": "32'd32"}
    assert [child.name for child in root.children] == ["u_regs", "u_fifo"]
    fifo = root.children[1]
    assert fifo.module == "stream_fifo"
    assert fifo.parameters == {"DEPTH": "32'd8", "PTR_W": "32'd3"}
    assert [child.name for child in fifo.children] == ["u_inner"]


def test_vcd_validation_rejects_non_vcd(tmp_path: Path) -> None:
    path = tmp_path / "bad.vcd"
    path.write_text("not a vcd\n", encoding="utf-8")
    header = parse_header(path)
    with pytest.raises(InvalidInput):
        validate_vcd(path, header)


def test_vcd_validation_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.vcd"
    path.write_text("", encoding="utf-8")
    header = parse_header(path)
    with pytest.raises(InvalidInput):
        validate_vcd(path, header)


def test_vcd_validation_rejects_zero_signal_dump_by_default(tmp_path: Path) -> None:
    path = tmp_path / "empty-valid.vcd"
    path.write_text("$timescale 1ns $end\n$enddefinitions $end\n", encoding="utf-8")
    header = parse_header(path)
    with pytest.raises(InvalidInput):
        validate_vcd(path, header)
    validate_vcd(path, header, allow_empty=True)
