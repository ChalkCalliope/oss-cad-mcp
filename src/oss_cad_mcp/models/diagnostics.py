"""Unified HDL diagnostic models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["error", "warning", "info", "note"]


class Diagnostic(BaseModel):
    """A normalized compiler or linter diagnostic."""

    engine: str = Field(description="slang, verilator, iverilog, ghdl, yosys, ...")
    severity: Severity
    message: str
    code: str | None = None
    file: str | None = Field(default=None, description="Workspace-relative path when possible.")
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=1)
    snippet: str | None = None
    url: str | None = None


class DiagnosticCounts(BaseModel):
    errors: int = 0
    warnings: int = 0
    infos: int = 0


class HierarchyNode(BaseModel):
    """One elaborated hierarchy node."""

    name: str
    module: str | None = None
    depth: int = 0
    parameters: dict[str, str] = Field(default_factory=dict)
    children: list[HierarchyNode] = Field(default_factory=list)


class CheckReport(BaseModel):
    """Result of parse, lint, or elaborate."""

    ok: bool
    level: Literal["parse", "lint", "elaborate"]
    engines: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    counts: DiagnosticCounts = Field(default_factory=DiagnosticCounts)
    hierarchy: list[HierarchyNode] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    top: str | None = None
    job: JobRef
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
    summary: str = ""


from oss_cad_mcp.models.common import ArtifactInfo, ErrorInfo, JobRef  # noqa: E402

HierarchyNode.model_rebuild()
CheckReport.model_rebuild()
