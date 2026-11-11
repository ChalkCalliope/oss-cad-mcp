"""Project discovery and configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from oss_cad_mcp.models.common import ErrorInfo


class ConstraintFile(BaseModel):
    path: str
    kind: Literal["pcf", "lpf", "cst", "sdc", "xdc", "qsf", "unknown"]
    target: str | None = None


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board: str | None = None
    family: Literal["ice40", "ecp5", "gowin", "nexus", "machxo2", "gatemate", "generic"] | None = None
    device: str | None = None
    package: str | None = None
    speed: str | None = None
    frequency_mhz: float | None = Field(default=None, gt=0)
    constraints: list[str] = Field(default_factory=list)
    nextpnr_opts: list[str] = Field(default_factory=list)
    pack_opts: list[str] = Field(default_factory=list)


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["auto", "iverilog", "verilator", "ghdl"] = "auto"
    testbench: str | None = None
    trace: Literal["auto", "none", "vcd", "fst"] = "auto"


class FormalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = "smtbmc"
    depth: int = Field(default=20, ge=1, le=10000)
    mode: Literal["prove", "bmc", "cover"] = "prove"


class ProjectConfig(BaseModel):
    """Contents of oss-cad-project.toml."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    top: str | None = None
    sources: list[str] = Field(default_factory=list)
    testbenches: list[str] = Field(default_factory=list)
    include_dirs: list[str] = Field(default_factory=list)
    defines: dict[str, str] = Field(default_factory=dict)
    language: Literal["auto", "verilog", "systemverilog", "vhdl", "mixed"] = "auto"
    std: str | None = None
    target: TargetConfig = Field(default_factory=TargetConfig)
    sim: SimulationConfig = Field(default_factory=SimulationConfig)
    formal: FormalConfig = Field(default_factory=FormalConfig)
    exclude: list[str] = Field(default_factory=list)


class ProjectProfile(BaseModel):
    """Detected project structure."""

    root: str
    config_file: str | None = None
    config: ProjectConfig | None = None
    hdl_files: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    top_candidates: list[str] = Field(default_factory=list)
    testbenches: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    constraints: list[ConstraintFile] = Field(default_factory=list)
    clocks: list[str] = Field(default_factory=list)
    build_files: list[str] = Field(default_factory=list)
    board: str | None = None
    target: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
