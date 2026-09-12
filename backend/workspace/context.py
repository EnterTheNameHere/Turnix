# file: backend/workspace/context.py ; version: 1
"""Invocation-bound CodeEntry facade for ephemeral workspace materialization."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from backend.workspace.runtime import EphemeralWorkspace

__all__ = ["WorkspaceFacade"]


class WorkspaceFacade:
    """Restricts one CodeEntry invocation to its private ephemeral workspace."""

    __slots__ = ("_requireValid", "_workspace")

    def __init__(self, *, workspace: EphemeralWorkspace, requireValid: Callable[[], None]) -> None:
        """Binds one ephemeral workspace to a CodeEntryContext lifetime.

        Args:
            workspace: Private workspace owned by the invocation context.
            requireValid: Invocation lifetime guard supplied by CodeEntryContext.
        """
        if not isinstance(workspace, EphemeralWorkspace):
            raise TypeError("workspace must be an EphemeralWorkspace.")
        self._workspace = workspace
        self._requireValid = requireValid

    def materializeBytes(self, relativePath: str | Path, payload: bytes) -> str:
        """Materializes exact bytes and returns their process-visible path.

        The path is ephemeral and valid only while the owning CodeEntryContext
        remains active. Materialization is immediate and is not a persistent I/O
        transaction or authoritative-state commit.
        """
        self._requireValid()
        return str(self._workspace.materializeBytes(relativePath, payload))

    def materializeText(self, relativePath: str | Path, text: str) -> str:
        """Materializes exact UTF-8 text and returns its process-visible path."""
        self._requireValid()
        return str(self._workspace.materializeText(relativePath, text))
