"""Application context shared by tools and workflows."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from oss_cad_mcp.config import Settings
from oss_cad_mcp.errors import SuiteNotFound
from oss_cad_mcp.exec.artifacts import ArtifactStore
from oss_cad_mcp.exec.job_manager import JobManager
from oss_cad_mcp.exec.paths import PathPolicy
from oss_cad_mcp.exec.runner import AsyncRunner
from oss_cad_mcp.registry import Catalog
from oss_cad_mcp.suite import Suite


@dataclass
class AppContext:
    settings: Settings
    paths: PathPolicy
    runner: AsyncRunner
    jobs: JobManager
    artifacts: ArtifactStore
    suite: Suite | None = None
    registry: Catalog | None = None
    suite_error: str | None = None
    _synth_commands: dict[str, bool] = field(default_factory=dict, repr=False)

    def require_suite(self) -> Suite:
        if self.suite is None:
            raise SuiteNotFound(
                "OSS CAD Suite is not available.",
                hint="Set OSS_CAD_SUITE_ROOT or launch oss-cad-mcp with --suite-root.",
                details={"error": self.suite_error},
            )
        return self.suite

    def supports_synth_command(self, command: str) -> bool:
        cached = self._synth_commands.get(command)
        if cached is not None:
            return cached
        suite = self.require_suite()
        _, text = suite.run_text("yosys", ["-Q", "-p", f"help {command}"], timeout=15.0)
        supported = "No such command" not in text
        self._synth_commands[command] = supported
        return supported

    def require_registry(self) -> Catalog:
        self.require_suite()
        if self.registry is None:
            raise SuiteNotFound("Tool catalog is not available.")
        return self.registry

    @property
    def workspace(self) -> Path:
        return self.settings.workspace_path


def build_app(settings: Settings) -> AppContext:
    workspace = settings.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    settings.build_path.mkdir(parents=True, exist_ok=True)
    settings.runs_path.mkdir(parents=True, exist_ok=True)
    settings.state_path.mkdir(parents=True, exist_ok=True)

    suite: Suite | None = None
    suite_error: str | None = None
    try:
        suite = Suite.discover(settings.suite_root)
    except SuiteNotFound as exc:
        suite_error = str(exc)

    read_roots: list[Path] = [workspace, settings.cache_path]
    if suite is not None:
        read_roots.append(suite.root)
    write_roots = [settings.build_path, settings.state_path, Path(tempfile.gettempdir()) / "oss-cad-mcp"]
    write_roots[-1].mkdir(parents=True, exist_ok=True)
    paths = PathPolicy.create(workspace, read_roots=tuple(read_roots), write_roots=tuple(write_roots))
    runner = AsyncRunner(
        default_timeout_s=settings.default_timeout_s,
        max_output_bytes=settings.max_output_bytes,
    )
    jobs = JobManager(
        runs_dir=settings.runs_path,
        runner=runner,
        max_jobs=settings.max_jobs,
    )
    jobs.load_existing()
    artifacts = ArtifactStore(paths)
    registry = Catalog(suite, cache_dir=settings.cache_path) if suite is not None else None
    if registry is not None:
        registry.load_version_cache()
    return AppContext(
        settings=settings,
        paths=paths,
        runner=runner,
        jobs=jobs,
        artifacts=artifacts,
        suite=suite,
        registry=registry,
        suite_error=suite_error,
    )
