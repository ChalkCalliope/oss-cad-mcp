"""Input schemas for MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from oss_cad_mcp.models.common import SourceSet


class BaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusRequest(BaseRequest):
    deep: bool = Field(default=False, description="Probe all tools and the sandbox; slower.")


class ProjectRequest(BaseRequest):
    action: Literal["inspect", "init", "validate"] = "inspect"
    root: str | None = Field(default=None, description="Project root; defaults to the workspace.")
    write: bool = Field(default=False, description="Allow init to write oss-cad-project.toml.")
    deep: bool = Field(default=False, description="Reserved for AST-level inspection.")


class CheckRequest(BaseRequest):
    source: SourceSet = Field(default_factory=SourceSet)
    top: str | None = None
    level: Literal["parse", "lint", "elaborate"] = "lint"
    engines: list[Literal["auto", "slang", "verilator", "iverilog", "ghdl"]] | None = None
    timeout_s: float = Field(default=180.0, gt=0, le=3600)


class SimulateRequest(BaseRequest):
    source: SourceSet = Field(default_factory=SourceSet)
    top: str | None = None
    engine: Literal["auto", "iverilog", "verilator", "ghdl"] = "auto"
    trace: Literal["auto", "none", "vcd", "fst"] = "auto"
    plusargs: list[str] = Field(default_factory=list)
    timeout_s: float = Field(default=300.0, gt=0, le=7200)
    seed: int | None = None
    expect_fail: bool = False


class SynthesizeRequest(BaseRequest):
    source: SourceSet = Field(default_factory=SourceSet)
    top: str | None = None
    target: Literal["generic", "ice40", "ecp5", "gowin", "nexus", "machxo2", "gatemate"] = "generic"
    flatten: bool = False
    abc9: bool = False
    timeout_s: float = Field(default=300.0, gt=0, le=3600)


class VerifyRequest(BaseRequest):
    mode: Literal["prove", "bmc", "cover", "equiv", "mutation"] = "prove"
    source: SourceSet = Field(default_factory=SourceSet)
    top: str | None = None
    engine: str = "smtbmc"
    depth: int = Field(default=20, ge=1, le=10000)
    config_file: str | None = None
    timeout_s: float = Field(default=600.0, gt=0, le=86_400)
    expect_fail: bool = False


class FpgaBuildRequest(BaseRequest):
    source: SourceSet = Field(default_factory=SourceSet)
    top: str | None = None
    board: str | None = None
    family: Literal["ice40", "ecp5"] | None = None
    device: str | None = None
    package: str | None = None
    speed: str | None = None
    constraints: list[str] = Field(default_factory=list)
    allow_unconstrained: bool = Field(
        default=False,
        description="Allow an early bring-up build with unconstrained I/O.",
    )
    frequency_mhz: float | None = Field(default=None, gt=0)
    seed: int | None = None
    placer: str | None = None
    router: str | None = None
    timeout_s: float = Field(default=600.0, gt=0, le=86_400)


class WaveformRequest(BaseRequest):
    action: Literal["info", "signals", "sample", "summarize", "export", "convert"] = "info"
    path: str
    signals: list[str] = Field(default_factory=list)
    start: str | int | float | None = None
    end: str | int | float | None = None
    max_samples: int = Field(default=1_000, ge=1, le=100_000)
    max_signals: int = Field(default=64, ge=1, le=1_024)
    output_format: Literal["vcd", "fst"] | None = None
    timeout_s: float = Field(default=180.0, gt=0, le=3600)


class NetlistRequest(BaseRequest):
    action: Literal["stats", "modules", "cells", "nets", "fanout", "diff"] = "stats"
    path: str
    module: str | None = None
    cell_type: str | None = None
    name: str | None = None
    other: str | None = None
    limit: int = Field(default=200, ge=1, le=5_000)


class TimingRequest(BaseRequest):
    action: Literal["parse", "icetime"] = "parse"
    path: str
    device: str | None = None
    clock_mhz: float | None = Field(default=None, gt=0)
    timeout_s: float = Field(default=180.0, gt=0, le=3600)


class JobRequest(BaseRequest):
    action: Literal["list", "status", "wait", "log", "cancel", "rerun"] = "list"
    job_id: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    timeout_s: float = Field(default=60.0, gt=0, le=86_400)
    stream: Literal["stdout", "stderr", "events"] = "stdout"
    tail_lines: int = Field(default=200, ge=1, le=20_000)
    offset: int = Field(default=0, ge=0)


class ArtifactRequest(BaseRequest):
    action: Literal["list", "stat", "read_text", "hash"] = "list"
    job_id: str | None = None
    artifact_id: str | None = None
    path: str | None = None
    offset: int = Field(default=0, ge=0)
    max_bytes: int = Field(default=65_536, ge=1, le=4_194_304)


class ToolSearchRequest(BaseRequest):
    query: str = ""
    family: str | None = None
    risk: str | None = None
    include_internal: bool = False
    include_hardware: bool = False
    limit: int = Field(default=20, ge=1, le=200)


class ToolHelpRequest(BaseRequest):
    tool: str


class ToolRunRequest(BaseRequest):
    tool: str
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_s: float = Field(default=300.0, gt=0, le=86_400)
    stdin: str | None = None
    allow_unsafe: bool = False
    confirm: bool = False
