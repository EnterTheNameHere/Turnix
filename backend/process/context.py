# file: backend/process/context.py ; version: 1
"""Invocation-bound CodeEntry facade for Actant process execution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from backend.process.runtime import ProcessRunner


class ProcessFacade:
    """Restricts one CodeEntry invocation to host-configured process tools."""

    __slots__ = ("_requireValid", "_runner")

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
        return self._runner.run(
            toolName,
            arguments,
            workingDirectory=workingDirectory,
            timeoutSeconds=timeoutSeconds,
        ).snapshot()
