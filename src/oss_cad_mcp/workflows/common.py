"""Shared workflow helpers."""

from __future__ import annotations

from pathlib import Path

from oss_cad_mcp.app import AppContext
from oss_cad_mcp.exec.runner import ProcessResult
from oss_cad_mcp.models.diagnostics import Diagnostic, DiagnosticCounts
from oss_cad_mcp.project import ResolvedSources


def ensure_tools(app: AppContext, *names: str) -> None:
    suite = app.require_suite()
    missing = [name for name in names if not suite.has(name)]
    if missing:
        from oss_cad_mcp.errors import PreflightFailed

        raise PreflightFailed(
            f"Required suite tool(s) are missing: {', '.join(missing)}",
            hint="Run cad_status to inspect the installed suite capabilities.",
        )


def work_path(job, *parts: str) -> Path:
    path = job.work_dir / "work"
    for part in parts:
        path = path / part
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def output_path(app: AppContext, job, kind: str, filename: str) -> Path:
    path = app.settings.build_path / kind / job.id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def source_files(resolved: ResolvedSources) -> list[str]:
    return [str(path) for path in resolved.files]


def include_args(prefix: str, dirs: list[Path]) -> list[str]:
    return [f"{prefix}{path}" for path in dirs]


def define_args(prefix: str, defines: dict[str, str]) -> list[str]:
    return [f"{prefix}{name}={value}" for name, value in sorted(defines.items())]


def count_diagnostics(diagnostics: list[Diagnostic]) -> DiagnosticCounts:
    return DiagnosticCounts(
        errors=sum(1 for item in diagnostics if item.severity == "error"),
        warnings=sum(1 for item in diagnostics if item.severity == "warning"),
        infos=sum(1 for item in diagnostics if item.severity in {"info", "note"}),
    )


def read_tail(path: Path, *, max_bytes: int = 16_384) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        data = fh.read()
    return data.decode("utf-8", errors="replace")


async def run_tool(
    app: AppContext,
    job,
    *,
    name: str,
    args: list[str],
    cwd: Path,
    timeout_s: float,
    step_name: str | None = None,
    executable: Path | None = None,
) -> ProcessResult:
    suite = app.require_suite()
    binary = executable or suite.binary(name)
    return await app.jobs.run_step(
        job,
        step_name or name,
        [str(binary), *args],
        tool=name,
        cwd=cwd,
        env=suite.run_env(),
        timeout_s=timeout_s,
    )


def tool_env(app: AppContext) -> dict[str, str]:
    return app.require_suite().run_env()


def merge_diagnostics(*items: list[Diagnostic]) -> list[Diagnostic]:
    merged: list[Diagnostic] = []
    seen: set[tuple[str, str | None, int | None, int | None, str]] = set()
    for group in items:
        for diagnostic in group:
            key = (
                diagnostic.engine,
                diagnostic.file,
                diagnostic.line,
                diagnostic.column,
                diagnostic.message,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(diagnostic)
    merged.sort(
        key=lambda item: (
            0 if item.severity == "error" else 1 if item.severity == "warning" else 2,
            item.file or "",
            item.line or 0,
            item.column or 0,
        )
    )
    return merged
