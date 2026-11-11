"""High-level workflows composed from OSS CAD Suite tools."""

from oss_cad_mcp.workflows.check import run_check
from oss_cad_mcp.workflows.formal import run_verify
from oss_cad_mcp.workflows.fpga import run_fpga_build
from oss_cad_mcp.workflows.netlist import inspect_netlist
from oss_cad_mcp.workflows.simulate import run_simulate
from oss_cad_mcp.workflows.synthesize import run_synthesize
from oss_cad_mcp.workflows.timing import parse_timing_report, run_icetime
from oss_cad_mcp.workflows.waveform import run_waveform

__all__ = [
    "inspect_netlist",
    "parse_timing_report",
    "run_check",
    "run_fpga_build",
    "run_icetime",
    "run_simulate",
    "run_synthesize",
    "run_verify",
    "run_waveform",
]
