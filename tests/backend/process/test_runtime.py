# file: tests/backend/process/test_runtime.py ; version: 1
"""Tests for Actant-mediated configured external-tool execution."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from backend.process import ProcessExecutionError, ProcessRunner, ProcessToolDefinition, ProcessToolRegistry


def _pythonTool() -> ProcessToolDefinition:
    """Returns a configured tool definition for the active test interpreter."""
    return ProcessToolDefinition(name="python-test", executable=Path(sys.executable).resolve())


def test_runCapturesCompletedNonzeroInvocation(tmp_path: Path) -> None:
    """A nonzero child exit remains ordinary structured tool evidence."""
    runner = ProcessRunner(ProcessToolRegistry((_pythonTool(),)))

    result = runner.run(
        "python-test",
        ("-c", "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)"),
        workingDirectory=tmp_path,
    )

    assert result.exitCode == 7
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.arguments[0] == "-c"
    assert result.workingDirectory == str(tmp_path.resolve())
    assert result.durationNs >= 0
    assert result.endedTimeNs >= result.startedTimeNs


def test_runAppliesConfiguredEnvironmentWithoutCodeEntryExecutableAuthority(tmp_path: Path) -> None:
    """Host tool configuration supplies environment while callers supply only argv."""
    definition = ProcessToolDefinition(
        name="python-test",
        executable=Path(sys.executable).resolve(),
        environment={"ACTANT_PROCESS_TEST": "configured"},
    )
    runner = ProcessRunner(ProcessToolRegistry((definition,)))

    result = runner.run(
        "python-test",
        ("-c", "import os; print(os.environ['ACTANT_PROCESS_TEST'])"),
        workingDirectory=tmp_path,
    )

    assert result.exitCode == 0
    assert result.stdout == "configured\n"


def test_runRejectsUnknownLogicalTool() -> None:
    """A caller cannot name an executable that was not configured as a tool."""
    runner = ProcessRunner(ProcessToolRegistry())

    with pytest.raises(ProcessExecutionError, match="not configured"):
        runner.run("python")


def test_runReportsMissingConfiguredExecutableAsInfrastructureFailure(tmp_path: Path) -> None:
    """A configured executable that cannot resolve fails before ordinary completion."""
    definition = ProcessToolDefinition(name="missing", executable=(tmp_path / "missing.exe").resolve())
    runner = ProcessRunner(ProcessToolRegistry((definition,)))

    with pytest.raises(ProcessExecutionError, match="cannot be resolved"):
        runner.run("missing", workingDirectory=tmp_path)


def test_runReportsTimeoutAsInfrastructureFailure(tmp_path: Path) -> None:
    """Timeout prevents normal completion and therefore raises execution failure."""
    runner = ProcessRunner(ProcessToolRegistry((_pythonTool(),)))

    with pytest.raises(ProcessExecutionError, match="failed to execute"):
        runner.run(
            "python-test",
            ("-c", "import time; time.sleep(2)"),
            workingDirectory=tmp_path,
            timeoutSeconds=0.01,
        )


def test_toolDefinitionRequiresAbsoluteExecutable() -> None:
    """Host configuration cannot defer executable resolution to child PATH lookup."""
    with pytest.raises(ValueError, match="absolute path"):
        ProcessToolDefinition(name="ruff", executable=Path("ruff"))


def test_registryRejectsDuplicateLogicalToolNames() -> None:
    """One logical tool name has exactly one authority binding in a registry."""
    definition = _pythonTool()
    registry = ProcessToolRegistry((definition,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)


def test_snapshotIsJsonCompatibleProjection(tmp_path: Path) -> None:
    """ProcessResult exposes generic evidence without leaking mutable result state."""
    runner = ProcessRunner(ProcessToolRegistry((_pythonTool(),)))

    snapshot = runner.run(
        "python-test",
        ("-c", "print('ok')"),
        workingDirectory=tmp_path,
    ).snapshot()

    assert snapshot["toolName"] == "python-test"
    assert snapshot["arguments"] == ["-c", "print('ok')"]
    assert snapshot["exitCode"] == 0
    assert snapshot["stdout"] == "ok\n"
