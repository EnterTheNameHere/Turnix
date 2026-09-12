# file: tests/backend/application/test_workspaceProcessIntegration.py ; version: 1
"""Integration tests for invocation workspace inputs consumed by process tools."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from backend.application.applicationRuntime import ApplicationRuntime
from backend.context.codeEntryContext import CodeEntryIdentity
from backend.packs.runtime import PackResolver
from backend.registration import RegistrationScope


def _identity(runtime: ApplicationRuntime) -> CodeEntryIdentity:
    """Builds a CodeEntry identity for one workspace/process integration call."""
    application = runtime.applicationRun.application
    return CodeEntryIdentity(
        applicationId=application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.workspace-process",
        packVersion="1.0.0",
        codeEntryId="main",
        codeEntryInstanceId="test-workspace-process-entry",
        sourceSha256="0" * 64,
        implementationFormat="python",
        implementationId="test-workspace-process-implementation",
    )


def _runtime(tmpPath: Path) -> ApplicationRuntime:
    """Creates an initializing runtime with Python as a configured test tool."""
    runtime = ApplicationRuntime(
        appPackId="test.workspace-process",
        packResolver=PackResolver(roots=(tmpPath,)),
        config={
            "processTools": {
                "python": str(Path(sys.executable).resolve()),
            },
        },
    )
    runtime.beginInitialization()
    return runtime


def test_workspace_artifact_is_process_visible_and_removed_after_call(
    tmp_path: Path,
) -> None:
    """A tool can consume exact workspace bytes before context cleanup removes them."""
    runtime = _runtime(tmp_path)
    context = runtime.createContext(
        identity=_identity(runtime),
        packRoot=tmp_path,
        registrationScope=RegistrationScope(),
    )
    source = "print('workspace')\r\n"
    artifactPath = context.workspace.materializeText("candidate.py", source)

    result = context.process.run(
        "python",
        (
            "-c",
            "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
            artifactPath,
        ),
        workingDirectory=tmp_path,
    )

    assert result["exitCode"] == 0
    assert result["stdout"] == source
    assert Path(artifactPath).read_bytes() == source.encode("utf-8")

    workspace = context.workspace
    context.invalidate()

    assert not Path(artifactPath).exists()
    with pytest.raises(RuntimeError, match="no longer valid"):
        workspace.materializeText("late.py", "print('late')\n")
    runtime.close()
