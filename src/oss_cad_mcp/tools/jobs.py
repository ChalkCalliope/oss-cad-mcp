"""MCP tools for jobs and artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from mcp.server.mcpserver import Context

from oss_cad_mcp.models.common import ErrorInfo
from oss_cad_mcp.models.results import ArtifactActionReport, JobActionReport
from oss_cad_mcp.tools.schemas import ArtifactRequest, JobRequest
from oss_cad_mcp.tools.system import _error, get_app


def _job_not_found(job_id: str | None) -> JobActionReport:
    return JobActionReport(
        ok=False,
        action="status",
        error=ErrorInfo(code="job_not_found", message=f"Unknown job: {job_id}"),
        summary="Job not found.",
    )


def _read_lines(path: Path, *, tail_lines: int, offset: int = 0) -> tuple[str, int]:
    if not path.exists():
        return "", 0
    total = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(min(offset, total))
        data = fh.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-tail_lines:]), total


async def cad_job(ctx: Context, request: JobRequest) -> JobActionReport:
    """List, poll, wait for, log, or cancel server jobs."""
    app = get_app(ctx)
    if request.action == "list":
        jobs = app.jobs.list(request.limit)
        return JobActionReport(
            ok=True,
            action=request.action,
            jobs=[job.to_ref() for job in jobs],
            summary=f"{len(jobs)} job(s).",
        )
    job = app.jobs.get(request.job_id or "")
    if job is None:
        return _job_not_found(request.job_id)
    if request.action == "status":
        return JobActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref(),
            summary=job.summary or f"Job is {job.status}.",
        )
    if request.action == "wait":
        await app.jobs.wait(job, request.timeout_s)
        return JobActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref(),
            summary=job.summary or f"Job is {job.status}.",
        )
    if request.action == "cancel":
        await app.jobs.cancel(job)
        return JobActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref(),
            summary=f"Job {job.id} is {job.status}.",
        )
    if request.action == "log":
        paths = {
            "stdout": job.stdout_path,
            "stderr": job.stderr_path,
            "events": job.events_path,
        }
        if not job.steps and request.stream == "stdout":
            paths["stdout"] = job.work_dir / "stdout.log"
        text, total = _read_lines(paths[request.stream], tail_lines=request.tail_lines, offset=request.offset)
        return JobActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref(),
            log=text,
            summary=f"{len(text)} bytes of {request.stream} log (file size {total}).",
        )
    return JobActionReport(
        ok=False,
        action=request.action,
        job=job.to_ref(),
        error=ErrorInfo(
            code="not_supported",
            message="Job rerun is not implemented in v0.1.",
            hint="Re-submit the original workflow with the same inputs.",
        ),
        summary="Rerun is not supported.",
    )


def _find_artifact(app, job_id: str | None, artifact_id: str | None):
    jobs = [app.jobs.get(job_id)] if job_id else app.jobs.list(200)
    for job in jobs:
        if job is None:
            continue
        if artifact_id:
            artifact = app.artifacts.find(job, artifact_id)
            if artifact is not None:
                return job, artifact
        elif job.artifacts:
            return job, job.artifacts[-1]
    return None, None


async def cad_artifact(ctx: Context, request: ArtifactRequest) -> ArtifactActionReport:
    """List or read job artifacts and text files inside the workspace."""
    app = get_app(ctx)
    if request.action == "list":
        if request.job_id:
            job = app.jobs.get(request.job_id)
        else:
            # Listing without a job should show a useful artifact set, not the
            # most recent job if that job happened to produce no artifacts.
            job = next((item for item in app.jobs.list(200) if item.artifacts), None)
        if job is None:
            return ArtifactActionReport(
                ok=False,
                action=request.action,
                error=ErrorInfo(code="job_not_found", message="No job with artifacts found."),
                summary="No job with artifacts found.",
            )
        return ArtifactActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref(),
            artifacts=job.artifacts,
            summary=f"{len(job.artifacts)} artifact(s).",
        )

    job = app.jobs.get(request.job_id) if request.job_id else None

    # An explicit path must take precedence over implicit artifact discovery.
    # Otherwise a workspace with any existing artifact can make a path-based
    # read return an unrelated file.
    if request.path and not request.artifact_id:
        try:
            path = app.paths.resolve_read(request.path, must_exist=True)
        except Exception as exc:
            return ArtifactActionReport(ok=False, action=request.action, error=_error(exc), summary=str(exc))
        if not path.is_file():
            return ArtifactActionReport(
                ok=False,
                action=request.action,
                error=ErrorInfo(code="not_a_file", message=f"Not a file: {path}"),
                summary="Not a file.",
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if request.action == "hash":
            return ArtifactActionReport(
                ok=True,
                action=request.action,
                job=job.to_ref() if job else None,
                value=digest,
                summary="SHA-256 computed.",
            )
        if request.action == "stat":
            return ArtifactActionReport(
                ok=True,
                action=request.action,
                job=job.to_ref() if job else None,
                value=digest,
                total_bytes=len(data),
                summary=f"{app.paths.display(path)} ({len(data)} bytes).",
            )
        mime = app.artifacts.mime_type(path)
        if request.action == "read_text" and not app.artifacts.is_text_mime(mime):
            return ArtifactActionReport(
                ok=False,
                action=request.action,
                job=job.to_ref() if job else None,
                total_bytes=len(data),
                value=digest,
                error=ErrorInfo(
                    code="binary_artifact",
                    message=f"{app.paths.display(path)} is {mime}, not a text file.",
                    hint="Use action='stat' or action='hash' for binary artifacts.",
                    details={"path": app.paths.display(path), "mime_type": mime},
                ),
                summary="Binary artifact cannot be read as text.",
            )
        text = data[request.offset : request.offset + request.max_bytes].decode("utf-8", errors="replace")
        return ArtifactActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref() if job else None,
            text=text,
            truncated=len(data) > request.offset + request.max_bytes,
            total_bytes=len(data),
            value=digest,
            summary=f"Read {len(text)} of {len(data)} bytes from {app.paths.display(path)}.",
        )

    job, artifact = _find_artifact(app, request.job_id, request.artifact_id)
    if artifact is not None:
        if request.action == "stat":
            return ArtifactActionReport(
                ok=True,
                action=request.action,
                job=job.to_ref() if job else None,
                artifact=artifact,
                summary=f"{artifact.kind}: {artifact.path} ({artifact.size_bytes} bytes).",
            )
        if request.action == "hash":
            return ArtifactActionReport(
                ok=True,
                action=request.action,
                job=job.to_ref() if job else None,
                artifact=artifact,
                value=artifact.sha256,
                summary="SHA-256 computed.",
            )
        try:
            text, truncated, total = app.artifacts.read_text(
                artifact, offset=request.offset, max_bytes=request.max_bytes
            )
        except Exception as exc:
            return ArtifactActionReport(
                ok=False,
                action=request.action,
                job=job.to_ref() if job else None,
                artifact=artifact,
                error=_error(exc),
                summary=str(exc),
            )
        return ArtifactActionReport(
            ok=True,
            action=request.action,
            job=job.to_ref() if job else None,
            artifact=artifact,
            text=text,
            truncated=truncated,
            total_bytes=total,
            summary=f"Read {len(text)} of {total} bytes.",
        )
    return ArtifactActionReport(
        ok=False,
        action=request.action,
        error=ErrorInfo(
            code="artifact_not_found",
            message=(
                f"Artifact {request.artifact_id!r} was not found."
                if request.artifact_id
                else "No matching artifact or path was provided."
            ),
            hint="Use cad_artifact(action='list', job_id=...) first.",
            details={"job_id": request.job_id, "artifact_id": request.artifact_id, "path": request.path},
        ),
        summary="Artifact not found.",
    )
