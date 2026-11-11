"""Artifact registration and bounded reading."""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path

from oss_cad_mcp.exec.job_manager import Job
from oss_cad_mcp.exec.paths import PathPolicy
from oss_cad_mcp.models.common import ArtifactInfo


class ArtifactStore:
    def __init__(self, paths: PathPolicy) -> None:
        self.paths = paths

    def register(
        self,
        job: Job,
        path: str | Path,
        *,
        kind: str,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> ArtifactInfo:
        resolved = Path(path).expanduser()
        resolved = (self.paths.workspace / resolved).resolve() if not resolved.is_absolute() else resolved.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Artifact does not exist: {resolved}")
        size, digest = self._hash(resolved)
        artifact = ArtifactInfo(
            id=f"art_{uuid.uuid4().hex[:12]}",
            job_id=job.id,
            kind=kind,
            path=self.paths.display(resolved),
            size_bytes=size,
            sha256=digest,
            mime_type=mime_type or self._mime_type(resolved),
            description=description,
            created_at=datetime.now(UTC),
        )
        job.artifacts.append(artifact)
        job.write_manifest()
        return artifact

    def find(self, job: Job, artifact_id: str) -> ArtifactInfo | None:
        for artifact in job.artifacts:
            if artifact.id == artifact_id:
                return artifact
        return None

    def resolve(self, artifact: ArtifactInfo) -> Path:
        return self.paths.resolve_read(artifact.path)

    def read_text(self, artifact: ArtifactInfo, *, offset: int = 0, max_bytes: int = 65_536) -> tuple[str, bool, int]:
        path = self.resolve(artifact)
        total = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, offset))
            data = fh.read(max_bytes + 1)
        truncated = len(data) > max_bytes or offset + max_bytes < total
        return data[:max_bytes].decode("utf-8", errors="replace"), truncated, total

    @staticmethod
    def _hash(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    @staticmethod
    def _mime_type(path: Path) -> str:
        suffix = path.suffix.lower()
        custom = {
            ".v": "text/x-verilog",
            ".sv": "text/x-systemverilog",
            ".vhd": "text/x-vhdl",
            ".vhdl": "text/x-vhdl",
            ".vcd": "application/x-vcd",
            ".fst": "application/x-fst",
            ".asc": "text/plain",
            ".json": "application/json",
            ".txt": "text/plain",
            ".log": "text/plain",
            ".bin": "application/octet-stream",
            ".bit": "application/octet-stream",
            ".fs": "application/octet-stream",
        }
        return custom.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
