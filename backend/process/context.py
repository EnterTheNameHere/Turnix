# file: backend/process/context.py ; version: 2
"""Invocation-bound CodeEntry facade for Actant process execution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from backend.process.runtime import ProcessRunner


class ProcessFacade:
    """Restricts one CodeEntry invocation to host-configured process tools."""

    __slots__ = ("_requireValid", "_runner", "_state", "_prepared")

    def __init__(self, *, runner: ProcessRunner, requireValid: Callable[[], None]) -> None:
        """Binds process execution to one CodeEntryContext lifetime.

        Args:
            runner: ApplicationRun-owned configured-tool executor.
            requireValid: Invocation lifetime guard supplied by CodeEntryContext.
        """
        if not isinstance(runner, ProcessRunner):
            raise TypeError("runner must be a ProcessRunner.")
        self._runner = runner
        self._requireValid = requireValid
        self._state = None
        self._prepared = None

    def _bind(self, state):
        """Install an independent invocation token without retaining Context in workers."""
        self._state = state

    def prepare(self, toolName, arguments=(), *, workingDirectory, timeoutSeconds=30.0,
                stdoutBytes=262144, stderrBytes=262144, cleanupSeconds=2.0):
        """Freeze one exact configured argv request without launching a process."""
        from backend.process.capture import prepare
        self._requireValid()
        self._runner.requireReady()
        if self._state is None:
            raise RuntimeError("Process invocation binding is unavailable.")
        if self._prepared is not None and not self._prepared._used:
            raise RuntimeError("A prepared process already exists.")
        self._prepared = prepare(self._runner, self._state, toolName, arguments,
            workingDirectory=workingDirectory, timeoutSeconds=timeoutSeconds,
            stdoutBytes=stdoutBytes, stderrBytes=stderrBytes, cleanupSeconds=cleanupSeconds)
        return self._prepared

    def execute(self, prepared, *, beforeLaunch):
        """Consume an exact bound ticket after a durable pre-launch acknowledgement."""
        from backend.process.capture import execute
        self._requireValid()
        try:
            return execute(self._runner, self._state, prepared, beforeLaunch)
        finally:
            beforeLaunch = None

    def run(
        self,
        toolName: str,
        arguments: Sequence[str] = (),
        *,
        workingDirectory: str | Path | None = None,
        timeoutSeconds: float | None = None,
    ) -> dict[str, object]:
        """Runs one configured tool and returns generic execution evidence.

        Args:
            toolName: Logical tool name authorized by host configuration.
            arguments: Exact argument vector following the configured executable.
            workingDirectory: Optional existing child-process working directory.
            timeoutSeconds: Optional positive completion timeout.

        Returns:
            JSON-compatible generic process evidence. A nonzero exit code remains
            a normally completed result and is represented by ``exitCode``.

        Raises:
            RuntimeError: If the owning CodeEntryContext is no longer valid.
            ProcessExecutionError: If Actant cannot resolve or execute the tool
                through its configured process authority.
            TypeError: If arguments or timeout have invalid types.
            ValueError: If the working directory or timeout value is invalid.
        """
        self._requireValid()
        self._runner.requireReady()
        if self._state is not None and self._state.signal is not None:
            self._state.signal.raiseIfRequested()
        return self._runner.run(
            toolName,
            arguments,
            workingDirectory=workingDirectory,
            timeoutSeconds=timeoutSeconds,
        ).snapshot()
