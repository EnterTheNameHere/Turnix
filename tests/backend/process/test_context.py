# file: tests/backend/process/test_context.py ; version: 1
"""Tests for invocation-scoped CodeEntry process authority."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from backend.process import ProcessRunner, ProcessToolDefinition, ProcessToolRegistry
from backend.process.context import ProcessFacade


def _runner() -> ProcessRunner:
    """Returns a runner exposing the active Python interpreter as a test tool."""
    return ProcessRunner(
        ProcessToolRegistry(
            (
                ProcessToolDefinition(
                    name="python",
                    executable=Path(sys.executable).resolve(),
                ),
            ),
        ),
    )


def test_process_facade_runs_configured_tool() -> None:
    """Facade returns generic evidence from its ApplicationRun-owned runner."""
    facade = ProcessFacade(runner=_runner(), requireValid=lambda: None)

    result = facade.run("python", ("-c", "print('facade')"))

    assert result["toolName"] == "python"
    assert result["exitCode"] == 0
    assert result["stdout"] == "facade\n"


def test_process_facade_checks_invocation_lifetime_before_execution() -> None:
    """A retained facade cannot execute after its CodeEntry invocation ends."""
    def reject() -> None:
        """Models CodeEntryContext's invalidated invocation guard."""
        raise RuntimeError("expired")

    facade = ProcessFacade(runner=_runner(), requireValid=reject)

    with pytest.raises(RuntimeError, match="expired"):
        facade.run("python", ("-c", "print('must not run')"))
