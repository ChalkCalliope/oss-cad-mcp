"""Models returned by high-level workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from oss_cad_mcp.models.common import ArtifactInfo, ErrorInfo, JobRef, ToolInfo
from oss_cad_mcp.models.diagnostics import Diagnostic
from oss_cad_mcp.models.project import ProjectProfile


class SimulationReport(BaseModel):
    ok: bool
    engine: str
    top: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    finish_reason: str | None = None
    simulated_time: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    waveform: ArtifactInfo | None = None
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class SynthesisReport(BaseModel):
    ok: bool
    target: str
    top: str | None = None
    cells_by_type: dict[str, int] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    netlist: ArtifactInfo | None = None
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class PropertyStatus(BaseModel):
    name: str
    kind: str | None = None
    status: Literal["PASS", "FAIL", "UNKNOWN", "ERROR"] = "UNKNOWN"
    depth: int | None = None
    location: str | None = None


class VerificationReport(BaseModel):
    ok: bool
    mode: str
    status: Literal["passed", "failed", "unknown", "error"] = "unknown"
    engine: str | None = None
    depth: int | None = None
    properties: list[PropertyStatus] = Field(default_factory=list)
    trace: ArtifactInfo | None = None
    log_tail: str = ""
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class StageReport(BaseModel):
    name: str
    tool: str
    ok: bool
    exit_code: int | None = None
    duration_s: float = 0.0
    summary: str = ""


class ResourceUsage(BaseModel):
    used: int = 0
    available: int = 0
    percent: float | None = None


class ClockTiming(BaseModel):
    name: str
    achieved_mhz: float | None = None
    constraint_mhz: float | None = None
    met: bool | None = None


class PathStep(BaseModel):
    type: str | None = None
    delay_ns: float | None = None
    from_cell: str | None = None
    to_cell: str | None = None
    net: str | None = None
    sources: list[str] = Field(default_factory=list)


class CriticalPath(BaseModel):
    from_node: str | None = None
    to_node: str | None = None
    delay_ns: float | None = None
    logic_ns: float | None = None
    routing_ns: float | None = None
    steps: list[PathStep] = Field(default_factory=list)


class TimingReport(BaseModel):
    ok: bool
    source: str | None = None
    clocks: list[ClockTiming] = Field(default_factory=list)
    critical_paths: list[CriticalPath] = Field(default_factory=list)
    met: bool | None = None
    max_frequency_mhz: float | None = None
    job: JobRef | None = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class FpgaBuildReport(BaseModel):
    ok: bool
    family: str
    device: str | None = None
    package: str | None = None
    speed: str | None = None
    top: str | None = None
    stages: list[StageReport] = Field(default_factory=list)
    utilization: dict[str, ResourceUsage] = Field(default_factory=dict)
    timing: TimingReport | None = None
    timing_met: bool | None = None
    bitstream: ArtifactInfo | None = None
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class SignalInfo(BaseModel):
    name: str
    width: int = 1
    kind: str = "wire"
    scope: str = ""


class WaveformInfo(BaseModel):
    path: str
    format: str
    timescale: str | None = None
    signal_count: int = 0
    max_time: int | None = None
    scopes: list[str] = Field(default_factory=list)
    signals: list[SignalInfo] = Field(default_factory=list)
    signals_truncated: bool = False


class WaveformSample(BaseModel):
    time: int
    values: dict[str, str] = Field(default_factory=dict)


class WaveformReport(BaseModel):
    ok: bool
    action: str
    info: WaveformInfo | None = None
    samples: list[WaveformSample] = Field(default_factory=list)
    samples_truncated: bool = False
    signal_stats: dict[str, dict[str, Any]] = Field(default_factory=dict)
    artifact: ArtifactInfo | None = None
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class ModuleInfo(BaseModel):
    name: str
    ports: int = 0
    cells: int = 0
    wires: int = 0
    memories: int = 0


class NetlistMatch(BaseModel):
    module: str
    name: str
    type: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class NetlistReport(BaseModel):
    ok: bool
    action: str
    path: str
    module_count: int = 0
    modules: list[ModuleInfo] = Field(default_factory=list)
    cells_by_type: dict[str, int] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    matches: list[NetlistMatch] = Field(default_factory=list)
    truncated: bool = False
    total_matches: int | None = None
    artifact: ArtifactInfo | None = None
    job: JobRef | None = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


class ToolSearchResult(BaseModel):
    ok: bool = True
    query: str = ""
    total: int = 0
    tools: list[ToolInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None


class BoardInfo(BaseModel):
    id: str
    name: str
    family: str
    device: str
    package: str | None = None
    speed: str | None = None
    programmer: str | None = None
    constraints: str | None = None
    default_frequency_mhz: float | None = None
    notes: str | None = None


class JobActionReport(BaseModel):
    ok: bool
    action: str
    job: JobRef | None = None
    jobs: list[JobRef] = Field(default_factory=list)
    log: str | None = None
    artifact: ArtifactInfo | None = None
    value: str | None = None
    error: ErrorInfo | None = None
    summary: str = ""


class ProjectActionReport(BaseModel):
    ok: bool
    action: str
    profile: ProjectProfile | None = None
    path: str | None = None
    written: bool = False
    job: JobRef | None = None
    error: ErrorInfo | None = None
    summary: str = ""


class ArtifactText(BaseModel):
    artifact: ArtifactInfo
    text: str
    truncated: bool = False
    total_bytes: int = 0


class ArtifactActionReport(BaseModel):
    ok: bool
    action: str
    job: JobRef | None = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    artifact: ArtifactInfo | None = None
    text: str | None = None
    truncated: bool = False
    total_bytes: int = 0
    value: str | None = None
    error: ErrorInfo | None = None
    summary: str = ""


class ToolRunReport(BaseModel):
    ok: bool
    tool: str
    argv: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""
    started_at: datetime | None = None
    duration_s: float | None = None
