# file: tests/backend/process/test_configuration.py ; version: 1
"""Tests for runtime configuration of logical process tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.process.configuration import processToolDefinitionsFromConfig


def test_process_tool_definitions_from_config_maps_logical_names(tmp_path: Path) -> None:
    """Runtime configuration binds logical tool names to absolute executables."""
    executable = tmp_path / "ruff.exe"
    config = {"processTools": {"ruff": str(executable)}}

    definitions = processToolDefinitionsFromConfig(config)

    assert len(definitions) == 1
    assert definitions[0].name == "ruff"
    assert definitions[0].executable == executable


def test_process_tool_definitions_from_config_allows_no_tools() -> None:
    """Applications without external-tool authority require no process config."""
    assert processToolDefinitionsFromConfig(None) == ()
    assert processToolDefinitionsFromConfig({}) == ()


def test_process_tool_definitions_from_config_rejects_relative_executable() -> None:
    """Host configuration cannot delegate PATH resolution to CodeEntry execution."""
    with pytest.raises(ValueError, match="absolute"):
        processToolDefinitionsFromConfig({"processTools": {"ruff": "ruff"}})


def test_process_tool_definitions_from_config_rejects_non_mapping_tools() -> None:
    """Malformed host tool configuration fails before an ApplicationRun starts."""
    with pytest.raises(TypeError, match="processTools"):
        processToolDefinitionsFromConfig({"processTools": ["ruff"]})
