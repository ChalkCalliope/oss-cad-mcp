from __future__ import annotations

import pytest

from oss_cad_mcp.app import AppContext


@pytest.mark.integration
def test_catalog_covers_suite(app: AppContext) -> None:
    assert app.suite is not None
    assert app.registry is not None
    suite_names = set(app.suite.tool_names())
    assert set(app.registry.names()) == suite_names
    assert app.registry.count() == len(suite_names)
    assert app.registry.agent_facing_count() > 50


@pytest.mark.integration
def test_catalog_search_recommends_workflows(app: AppContext) -> None:
    assert app.registry is not None
    entries = app.registry.search("formal")
    names = {entry.name for entry in entries}
    assert "sby" in names
    assert all(entry.family for entry in entries)


@pytest.mark.integration
def test_hardware_tools_are_not_agent_facing(app: AppContext) -> None:
    assert app.registry is not None
    entry = app.registry.get("openFPGALoader")
    assert entry.hardware is True
    assert entry.agent_facing is False


@pytest.mark.integration
def test_search_include_flags_with_empty_query(app: AppContext) -> None:
    assert app.registry is not None

    hardware = app.registry.search("", risk="hardware", include_hardware=True, limit=200)
    assert hardware
    assert any(entry.hardware for entry in hardware)

    internal = app.registry.search("", include_internal=True, limit=200)
    assert any(entry.internal for entry in internal)
