"""Server configuration loaded from TOML and environment variables."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_config_path() -> Path:
    return Path(platformdirs.user_config_dir("oss-cad-mcp")) / "config.toml"


class Settings(BaseSettings):
    """Effective server settings.

    Precedence used by :func:`load_settings`:
    CLI overrides > ``OSS_CAD_MCP_*`` environment variables > TOML > defaults.
    """

    model_config = SettingsConfigDict(env_prefix="OSS_CAD_MCP_", extra="ignore")

    suite_root: Path | None = None
    workspace: Path = Field(default_factory=Path.cwd)
    profile: Literal["core", "standard", "full"] = "core"
    allow_raw: bool = True
    allow_unsafe: bool = False
    max_jobs: int = Field(default=4, ge=1, le=64)
    default_timeout_s: float = Field(default=300.0, gt=0, le=86_400)
    max_output_bytes: int = Field(default=65_536, ge=1_024, le=16_777_216)
    build_dir: Path | None = None
    data_dir: Path | None = None
    log_level: str = "INFO"

    @field_validator("workspace", "suite_root", "build_dir", "data_dir", mode="before")
    @classmethod
    def _expand_path(cls, value: Any) -> Any:
        if value is None:
            return None
        return Path(os.path.expandvars(os.path.expanduser(str(value))))

    @property
    def workspace_path(self) -> Path:
        return self.workspace.expanduser().resolve()

    @property
    def build_path(self) -> Path:
        path = self.build_dir or (self.workspace_path / "build")
        return path.expanduser().resolve()

    @property
    def state_path(self) -> Path:
        path = self.data_dir or (self.workspace_path / ".oss-cad-mcp")
        return path.expanduser().resolve()

    @property
    def runs_path(self) -> Path:
        return self.state_path / "runs"

    @property
    def cache_path(self) -> Path:
        return Path(platformdirs.user_cache_dir("oss-cad-mcp"))


class ProjectConfigError(ValueError):
    """Raised when oss-cad-project.toml cannot be parsed."""


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectConfigError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectConfigError(f"{path} must contain a TOML table")
    return data


def _env_overrides() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in Settings.model_fields:
        env_name = f"OSS_CAD_MCP_{name.upper()}"
        if env_name in os.environ:
            values[name] = os.environ[env_name]
    return values


def load_settings(
    *,
    config_file: Path | str | None = None,
    **overrides: Any,
) -> Settings:
    """Load settings with CLI > env > TOML > defaults precedence."""

    path = Path(config_file).expanduser() if config_file else default_config_path()
    data = _read_toml(path)
    data.update(_env_overrides())
    data.update({key: value for key, value in overrides.items() if value is not None})
    return Settings(**data)


class RuntimePaths(BaseModel):
    """Resolved runtime paths, useful for diagnostics."""

    workspace: Path
    suite_root: Path | None
    build: Path
    runs: Path
    cache: Path
