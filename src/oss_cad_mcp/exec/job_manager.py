"""Job lifecycle, manifests, and step logs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oss_cad_mcp.exec.runner import AsyncRunner, ProcessResult
from oss_cad_mcp.models.common import ArtifactInfo, ErrorInfo, JobInfo, JobRef, JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@dataclass
class StepRecord:
    name: str
    tool: str
    argv: list[str]
    cwd: str
    exit_code: int | None
    duration_s: float
    timed_out: bool = False
    stdout: str | None = None
    stderr: str | None = None


@dataclass
class Job:
    id: str
    kind: str
    work_dir: Path
    created_at: datetime
    status: JobStatus = "pending"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str | None = None
    command: list[str] = field(default_factory=list)
    cwd: str | None = None
    error: ErrorInfo | None = None
    result: Any | None = None
    artifacts: list[ArtifactInfo] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    task: asyncio.Task[Any] | None = None
    cancel_requested: bool = False

    @property
    def manifest_path(self) -> Path:
        return self.work_dir / "manifest.json"

    @property
    def stdout_path(self) -> Path:
        return self.work_dir / "stdout.log"

    @property
    def stderr_path(self) -> Path:
        return self.work_dir / "stderr.log"

    @property
    def events_path(self) -> Path:
        return self.work_dir / "events.jsonl"

    @property
    def log_uri(self) -> str:
        return f"cad://jobs/{self.id}/log/stdout"

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.ended_at or utcnow()
        return max(0.0, (end - self.started_at).total_seconds())

    def to_info(self) -> JobInfo:
        return JobInfo(
            id=self.id,
            kind=self.kind,
            status=self.status,
            summary=self.summary,
            log_uri=self.log_uri,
            created_at=self.created_at,
            started_at=self.started_at,
            ended_at=self.ended_at,
            duration_s=self.duration_s,
            exit_code=(self.steps[-1].exit_code if self.steps else None),
            command=self.command,
            cwd=self.cwd,
            work_dir=str(self.work_dir),
            artifacts=self.artifacts,
            error=self.error,
        )

    def to_ref(self) -> JobRef:
        return JobRef(
            id=self.id,
            kind=self.kind,
            status=self.status,
            summary=self.summary,
            log_uri=self.log_uri,
        )

    def write_manifest(self) -> None:
        payload = {
            "schema_version": 1,
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_s": self.duration_s,
            "summary": self.summary,
            "command": self.command,
            "cwd": self.cwd,
            "error": self.error.model_dump(mode="json") if self.error else None,
            "result": _jsonable(self.result),
            "artifacts": [artifact.model_dump(mode="json") for artifact in self.artifacts],
            "steps": [
                {
                    "name": step.name,
                    "tool": step.tool,
                    "argv": step.argv,
                    "cwd": step.cwd,
                    "exit_code": step.exit_code,
                    "duration_s": step.duration_s,
                    "timed_out": step.timed_out,
                    "stdout": step.stdout,
                    "stderr": step.stderr,
                }
                for step in self.steps
            ],
        }
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def append_event(self, event: dict[str, Any]) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": utcnow().isoformat(), **event}) + "\n")


class JobManager:
    """Creates, runs, and tracks jobs while limiting concurrent work."""

    _TIMEOUT_GRACE_S = 2.0

    def __init__(
        self,
        *,
        runs_dir: Path,
        runner: AsyncRunner,
        max_jobs: int = 4,
    ) -> None:
        self.runs_dir = runs_dir
        self.runner = runner
        self.max_jobs = max_jobs
        self._jobs: dict[str, Job] = {}
        self._semaphore = asyncio.Semaphore(max_jobs)
        self._shutdown = False

    def load_existing(self, limit: int = 100) -> None:
        """Restore job metadata from manifests after a server restart."""
        if not self.runs_dir.exists():
            return
        manifests = sorted(
            self.runs_dir.glob("*/manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in manifests[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job_id = str(data["job_id"])
                created = datetime.fromisoformat(str(data["created_at"]))
                started = datetime.fromisoformat(str(data["started_at"])) if data.get("started_at") else None
                ended = datetime.fromisoformat(str(data["ended_at"])) if data.get("ended_at") else None
                artifacts = [
                    ArtifactInfo.model_validate(item) for item in data.get("artifacts", []) if isinstance(item, dict)
                ]
                steps = [
                    StepRecord(
                        name=str(item.get("name", "")),
                        tool=str(item.get("tool", "")),
                        argv=[str(arg) for arg in item.get("argv", [])],
                        cwd=str(item.get("cwd", "")),
                        exit_code=item.get("exit_code"),
                        duration_s=float(item.get("duration_s", 0.0)),
                        timed_out=bool(item.get("timed_out", False)),
                        stdout=item.get("stdout"),
                        stderr=item.get("stderr"),
                    )
                    for item in data.get("steps", [])
                    if isinstance(item, dict)
                ]
                status = str(data.get("status", "interrupted"))
                if status in {"pending", "running"}:
                    status = "interrupted"
                    ended = ended or utcnow()
                job = Job(
                    id=job_id,
                    kind=str(data.get("kind", "unknown")),
                    work_dir=path.parent,
                    created_at=created,
                    status=status,  # type: ignore[arg-type]
                    started_at=started,
                    ended_at=ended,
                    summary=data.get("summary")
                    or ("Server restarted while the job was running." if status == "interrupted" else None),
                    command=[str(arg) for arg in data.get("command", [])],
                    cwd=data.get("cwd"),
                    error=ErrorInfo.model_validate(data["error"]) if data.get("error") else None,
                    result=data.get("result"),
                    artifacts=artifacts,
                    steps=steps,
                )
                self._jobs[job.id] = job
            except OSError, ValueError, KeyError, TypeError:
                continue

    def create(self, kind: str, *, work_dir: Path | None = None) -> Job:
        job_id = str(uuid.uuid7())
        directory = work_dir or (self.runs_dir / job_id)
        directory.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=job_id,
            kind=kind,
            work_dir=directory,
            created_at=utcnow(),
            cwd=str(directory),
        )
        job.write_manifest()
        self._jobs[job.id] = job
        return job

    def blocked(self, kind: str, *, summary: str, error: ErrorInfo) -> Job:
        """Create a terminal job for work rejected before execution."""
        job = self.create(kind)
        job.status = "failed"
        job.summary = summary
        job.error = error
        job.ended_at = utcnow()
        job.append_event({"event": "blocked", "error": error.model_dump(mode="json")})
        job.write_manifest()
        return job

    async def execute(
        self,
        kind: str,
        coro_factory: Callable[[Job], Awaitable[Any]],
        *,
        timeout_s: float,
        summary: str | None = None,
    ) -> Job:
        job = self.create(kind)
        job.task = asyncio.create_task(self._run(job, coro_factory))
        try:
            await asyncio.wait_for(asyncio.shield(job.task), timeout=timeout_s)
        except TimeoutError:
            job.append_event({"event": "wait_timeout", "timeout_s": timeout_s})
            # Give the inner runner a short grace period to finish killing the
            # process and finalize the job, so callers see a terminal result
            # rather than a transient "running" job.
            with contextlib.suppress(asyncio.CancelledError, TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(job.task), timeout=self._TIMEOUT_GRACE_S)
        except asyncio.CancelledError:
            # A cad_job cancellation is a normal tool outcome, not a request
            # cancellation. Returning the job lets the workflow wrapper report
            # a structured job_cancelled result to the client.
            if job.cancel_requested:
                job.append_event({"event": "cancel_returned"})
                return job
            raise
        except Exception:
            # _run has already recorded the failure in the manifest.
            pass
        return job

    async def _run(self, job: Job, coro_factory: Callable[[Job], Awaitable[Any]]) -> None:
        async with self._semaphore:
            if self._shutdown:
                job.status = "cancelled"
                job.ended_at = utcnow()
                job.write_manifest()
                return
            job.status = "running"
            job.started_at = utcnow()
            job.append_event({"event": "started", "kind": job.kind})
            job.write_manifest()
            try:
                result = await coro_factory(job)
                job.result = result
                ok = bool(getattr(result, "ok", True))
                timed_out = bool(getattr(result, "timed_out", False))
                job.status = "timeout" if timed_out else ("passed" if ok else "failed")
                job.summary = getattr(result, "summary", None) or job.summary
                job.error = getattr(result, "error", None)
            except asyncio.CancelledError:
                job.status = "cancelled"
                job.ended_at = utcnow()
                job.summary = "Job cancelled."
                job.append_event({"event": "cancelled"})
                job.write_manifest()
                raise
            except Exception as exc:  # keep a manifest for unexpected failures
                from oss_cad_mcp.errors import CadError

                job.status = "failed"
                job.summary = f"Job failed: {exc}"
                if isinstance(exc, CadError):
                    job.error = exc.to_info()
                else:
                    job.error = ErrorInfo(code="internal_error", message=str(exc))
                job.append_event({"event": "error", "message": str(exc)})
                job.write_manifest()
                raise
            finally:
                if job.ended_at is None:
                    job.ended_at = utcnow()
                if job.result is not None and hasattr(job.result, "job"):
                    with contextlib.suppress(AttributeError, TypeError):
                        job.result.job = job.to_ref()
                job.write_manifest()
                job.append_event({"event": "finished", "status": job.status})

    async def run_step(
        self,
        job: Job,
        name: str,
        argv: list[str],
        *,
        tool: str,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float | None = None,
        stdin_text: str | None = None,
    ) -> ProcessResult:
        step_index = len(job.steps) + 1
        step_dir = job.work_dir / "steps"
        step_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        stdout_path = step_dir / f"{step_index:02d}-{safe_name}.stdout.log"
        stderr_path = step_dir / f"{step_index:02d}-{safe_name}.stderr.log"
        job.command = list(argv)
        job.cwd = str(cwd)
        job.append_event({"event": "step_start", "name": name, "argv": argv})
        result = await self.runner.run_streaming(
            argv,
            cwd=cwd,
            env=env,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_s=timeout_s,
            stdin_text=stdin_text,
        )
        record = StepRecord(
            name=name,
            tool=tool,
            argv=list(argv),
            cwd=str(cwd),
            exit_code=result.exit_code,
            duration_s=result.duration_s,
            timed_out=result.timed_out,
            stdout=str(stdout_path),
            stderr=str(stderr_path),
        )
        job.steps.append(record)
        self._append_log(job.stdout_path, stdout_path)
        self._append_log(job.stderr_path, stderr_path)
        job.append_event(
            {
                "event": "step_end",
                "name": name,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_s": result.duration_s,
            }
        )
        job.write_manifest()
        return result

    @staticmethod
    def _append_log(target: Path, source: Path) -> None:
        if not source.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, target.open("ab") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 20) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    async def wait(self, job: Job, timeout_s: float) -> Job:
        if job.task is not None and not job.task.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(job.task), timeout=timeout_s)
        return job

    async def cancel(self, job: Job) -> Job:
        if job.task is not None and not job.task.done():
            job.cancel_requested = True
            job.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await job.task
        if job.status not in {"passed", "failed", "cancelled"}:
            job.status = "cancelled"
            job.ended_at = utcnow()
            job.summary = "Job cancelled."
            job.write_manifest()
        return job

    async def shutdown(self) -> None:
        self._shutdown = True
        running = [job for job in self._jobs.values() if job.task is not None and not job.task.done()]
        for job in running:
            if job.task is not None:
                job.task.cancel()
        for job in running:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                if job.task is not None:
                    await job.task
            job.status = "interrupted"
            job.ended_at = utcnow()
            job.summary = "Server shut down while the job was running."
            job.write_manifest()

    def add_artifact(self, job: Job, artifact: ArtifactInfo) -> None:
        job.artifacts.append(artifact)
        job.write_manifest()

    def remove_workdir(self, job: Job) -> None:
        shutil.rmtree(job.work_dir, ignore_errors=True)
