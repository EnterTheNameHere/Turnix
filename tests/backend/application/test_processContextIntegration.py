# file: tests/backend/application/test_processContextIntegration.py ; version: 2
"""Integration tests for ApplicationRun-owned CodeEntry process authority."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from backend.application.applicationRuntime import ApplicationRuntime
from backend.context.codeEntryContext import CodeEntryIdentity
from backend.packs.runtime import PackResolver
from backend.process import ProcessExecutionError
from backend.registration import RegistrationScope


def _identity(runtime: ApplicationRuntime) -> CodeEntryIdentity:
    """Builds deterministic-enough CodeEntry identity for one runtime test call."""
    application = runtime.applicationRun.application
    return CodeEntryIdentity(
        applicationId=application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.process",
        packVersion="1.0.0",
        codeEntryId="main",
        codeEntryInstanceId="test-process-entry",
        sourceSha256="0" * 64,
        implementationFormat="python",
        implementationId="test-process-implementation",
    )


def _runtime(tmpPath: Path, *, config: dict[str, object] | None = None) -> ApplicationRuntime:
    """Creates an initializing runtime whose process tools come from test config."""
    runtime = ApplicationRuntime(
        appPackId="test.process",
        packResolver=PackResolver(roots=(tmpPath,)),
        config=config,
    )
    runtime.beginInitialization()
    return runtime


def test_context_process_runs_host_configured_tool(tmp_path: Path) -> None:
    """CodeEntryContext exposes a configured tool but not executable selection."""
    runtime = _runtime(
        tmp_path,
        config={"processTools": {"python": str(Path(sys.executable).resolve())}},
    )
    context = runtime.createContext(
        identity=_identity(runtime),
        packRoot=tmp_path,
        registrationScope=RegistrationScope(),
    )

    result = context.process.run(
        "python",
        ("-c", "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)"),
        workingDirectory=tmp_path,
    )

    assert result["toolName"] == "python"
    assert result["exitCode"] == 7
    assert result["stdout"] == "out\n"
    assert result["stderr"].endswith("err\n")
    assert result["arguments"][0] == "-c"
    runtime.close()


def test_context_process_rejects_unconfigured_tool(tmp_path: Path) -> None:
    """Logical tool lookup fails when host configuration grants no such tool."""
    runtime = _runtime(tmp_path)
    context = runtime.createContext(
        identity=_identity(runtime),
        packRoot=tmp_path,
        registrationScope=RegistrationScope(),
    )

    with pytest.raises(ProcessExecutionError, match="not configured"):
        context.process.run("ruff", workingDirectory=tmp_path)
    runtime.close()


def test_context_process_authority_ends_with_context(tmp_path: Path) -> None:
    """A retained process facade cannot execute after its CodeEntry call ends."""
    runtime = _runtime(
        tmp_path,
        config={"processTools": {"python": str(Path(sys.executable).resolve())}},
    )
    context = runtime.createContext(
        identity=_identity(runtime),
        packRoot=tmp_path,
        registrationScope=RegistrationScope(),
    )
    process = context.process
    context.invalidate()

    with pytest.raises(RuntimeError, match="no longer valid"):
        process.run("python", ("-c", "print('must not execute')"), workingDirectory=tmp_path)
    runtime.close()


def test_process_configuration_is_detached_from_caller(tmp_path: Path) -> None:
    """Mutating caller config cannot retarget an ApplicationRun's process tool."""
    config: dict[str, object] = {
        "processTools": {"python": str(Path(sys.executable).resolve())},
    }
    runtime = _runtime(tmp_path, config=config)
    tools = config["processTools"]
    assert isinstance(tools, dict)
    tools["python"] = str(tmp_path / "replacement.exe")
    context = runtime.createContext(
        identity=_identity(runtime),
        packRoot=tmp_path,
        registrationScope=RegistrationScope(),
    )

    result = context.process.run(
        "python",
        ("-c", "print('original')"),
        workingDirectory=tmp_path,
    )

    assert result["exitCode"] == 0
    assert result["stdout"] == "original\n"
    runtime.close()
