"""Shared Pydantic models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "pending",
    "running",
    "passed",
    "failed",
    "timeout",
    "cancelled",
    "interrupted",
]

Risk = Literal[
    "read_only",
    "writes_files",
    "executes_code",
    "hardware",
    "gui",
    "network",
    "destructive",
]


class ErrorInfo(BaseModel):
    """Actionable error returned in a tool result."""

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error message.")
    hint: str | None = Field(default=None, description="Suggested next action.")
    details: dict[str, Any] | None = Field(default=None, description="Additional structured context.")
    retryable: bool = Field(default=False, description="Whether retrying may succeed.")


class ArtifactInfo(BaseModel):
    """A file produced by or consumed in a job."""

    id: str
    job_id: str
    kind: str
    path: str = Field(description="Workspace-relative path when possible.")
    size_bytes: int
    sha256: str
    mime_type: str
    description: str | None = None
    created_at: datetime


class JobRef(BaseModel):
    """Compact reference to a job."""

    id: str
    kind: str
    status: JobStatus
    summary: str | None = None
    log_uri: str | None = None


class JobInfo(JobRef):
    """Full job state."""

    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    exit_code: int | None = None
    command: list[str] = Field(default_factory=list)
    cwd: str | None = None
    work_dir: str | None = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None


class ToolInfo(BaseModel):
    """One binary in the OSS CAD Suite catalog."""

    name: str
    binary: str
    family: str
    summary: str
    risk: Risk = "executes_code"
    agent_facing: bool = True
    gui: bool = False
    hardware: bool = False
    internal: bool = False
    tags: list[str] = Field(default_factory=list)
    version: str | None = None
    help_uri: str | None = None
    recommended_tool: str | None = None


class ToolHelp(BaseModel):
    """Help and usage information for a catalog entry."""

    tool: str
    summary: str
    binary: str
    version: str | None = None
    usage: str | None = None
    help_text: str | None = None
    examples: list[str] = Field(default_factory=list)
    risk: Risk
    recommended_tool: str | None = None
    notes: list[str] = Field(default_factory=list)


class SourceSet(BaseModel):
    """Input HDL sources and preprocessing options."""

    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(default_factory=list, description="Source files or globs.")
    filelist: str | None = Field(default=None, description="Verilog filelist (.f).")
    include_dirs: list[str] = Field(default_factory=list)
    defines: dict[str, str] = Field(default_factory=dict)
    top: str | None = None
    language: Literal["auto", "verilog", "systemverilog", "vhdl", "mixed"] = "auto"
    std: str | None = Field(default=None, description="Language standard, e.g. 1800-2017 or 08.")


class CapabilityReport(BaseModel):
    """Preflight capabilities of the installed suite."""

    synthesis: bool = False
    simulation: bool = False
    formal: bool = False
    ice40: bool = False
    ecp5: bool = False
    gowin: bool = False
    vhdl: bool = False
    verilator: bool = False
    iverilog: bool = False
    waveform: bool = False
    fpga_build: bool = False


class ToolchainInfo(BaseModel):
    """Host toolchain availability."""

    cxx: str | None = None
    make: str | None = None
    git: str | None = None
    display: bool = False
    wsl: bool = False


class HardwareInfo(BaseModel):
    """Hardware availability summary. Hardware programming is not in v0.1."""

    usb_visible: bool = False
    devices: list[str] = Field(default_factory=list)
    note: str | None = None


class StatusReport(BaseModel):
    """Server and suite status."""

    ok: bool
    package_version: str
    api_version: int
    platform: str
    platform_status: str
    wsl: bool = False
    suite_root: str | None = None
    suite_version: str | None = None
    workspace: str
    profile: str
    tools_present: int = 0
    tools_missing: list[str] = Field(default_factory=list)
    capabilities: CapabilityReport = Field(default_factory=CapabilityReport)
    toolchain: ToolchainInfo = Field(default_factory=ToolchainInfo)
    hardware: HardwareInfo = Field(default_factory=HardwareInfo)
    sandbox_available: bool = False
    sandbox_note: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
