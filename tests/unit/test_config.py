from __future__ import annotations

from pathlib import Path

import pytest

from oss_cad_mcp.config import ProjectConfigError, Settings, load_settings


def test_settings_paths(tmp_path: Path) -> None:
    settings = Settings(workspace=tmp_path)
    assert settings.workspace_path == tmp_path.resolve()
    assert settings.build_path == (tmp_path / "build").resolve()
    assert settings.runs_path == (tmp_path / ".oss-cad-mcp" / "runs").resolve()


def test_toml_and_cli_precedence(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'workspace = "{tmp_path / "from-toml"}"\nprofile = "standard"\n')
    monkeypatch.setenv("OSS_CAD_MCP_PROFILE", "core")
    settings = load_settings(config_file=config, profile="full")
    assert settings.workspace == tmp_path / "from-toml"
    assert settings.profile == "full"


def test_project_config_error_has_stable_code(tmp_path: Path) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[project\nname = 'broken'\n", encoding="utf-8")
    with pytest.raises(ProjectConfigError) as excinfo:
        load_settings(config_file=config)
    assert excinfo.value.code == "project_config_error"
