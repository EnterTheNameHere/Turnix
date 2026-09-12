# file: backend/workspace/runtime.py ; version: 1
"""Actant-owned ephemeral filesystem workspaces for invocation-local tool inputs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path, PurePath

from backend.core.errors import ActantError

__all__ = ["EphemeralWorkspace", "WorkspaceError", "WorkspacePathError", "WorkspaceWriteError"]


class WorkspaceError(ActantError, RuntimeError):
    """Base error for Actant-mediated ephemeral-workspace failures."""


class WorkspacePathError(WorkspaceError):
    """Raised when a requested workspace-relative path escapes its workspace."""


class WorkspaceWriteError(WorkspaceError):
    """Raised when Actant cannot materialize an ephemeral workspace artifact."""


class EphemeralWorkspace:
    """One non-authoritative filesystem workspace with explicit lifetime.

    The workspace exists only to make invocation-local bytes visible to external
    tools and similar mediated consumers. Materialization is immediate and is
    deliberately outside persistent ManagedIoTransaction publication. Files in
    this workspace are not committed state, retained artifacts, or durable I/O.

    The workspace owns a private operating-system temporary directory. Callers
    may address files only by relative paths beneath that directory. Closing the
    workspace removes the directory best-effort and permanently ends its use.
    """

    __slots__ = ("_closed", "_root")

    def __init__(self) -> None:
        """Creates a private Actant-owned temporary directory.

        Raises:
            WorkspaceWriteError: If the operating system cannot create the
                workspace directory.
        """
        try:
            self._root = Path(tempfile.mkdtemp(prefix="actant-work-"))
        except OSError as err:
            raise WorkspaceWriteError(f"Failed to create ephemeral workspace: {err}.") from err
        self._closed = False

    @property
    def root(self) -> Path:
        """Returns the private workspace root while the workspace is active."""
        self._requireActive()
        return self._root

    def materializeBytes(self, relativePath: str | Path, payload: bytes) -> Path:
        """Publishes exact bytes immediately beneath the workspace root.

        Args:
            relativePath: Relative file path within this workspace. Absolute
                paths, empty paths, ``.`` and parent traversal are rejected.
            payload: Exact built-in bytes to expose to filesystem consumers.

        Returns:
            Absolute path of the fully written process-visible artifact.

        Raises:
            RuntimeError: If this workspace is already closed.
            TypeError: If payload is not exact built-in bytes.
            WorkspacePathError: If the requested relative path is invalid or
                escapes the workspace.
            WorkspaceWriteError: If directory creation or file publication
                fails.
        """
        self._requireActive()
        if type(payload) is not bytes:
            raise TypeError("payload must be exact built-in bytes.")
        destination = self._resolveRelative(relativePath)
        temporary = destination.with_name(f".{destination.name}.actant.tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(payload)
            temporary.replace(destination)
        except OSError as err:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkspaceWriteError(
                f"Failed to materialize ephemeral workspace file {destination}: {err}.",
            ) from err
        return destination

    def materializeText(self, relativePath: str | Path, text: str) -> Path:
        """Publishes exact UTF-8 encoding of text beneath the workspace root.

        No newline normalization, dedenting, or content transformation is
        performed. The returned file therefore contains exactly
        ``text.encode('utf-8')``.
        """
        self._requireActive()
        if type(text) is not str:
            raise TypeError("text must be an exact built-in string.")
        return self.materializeBytes(relativePath, text.encode("utf-8"))

    def close(self) -> None:
        """Ends workspace authority and removes all ephemeral files best-effort."""
        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self._root, ignore_errors=True)

    def _resolveRelative(self, relativePath: str | Path) -> Path:
        """Resolves a caller path while enforcing containment in this workspace."""
        if isinstance(relativePath, Path):
            raw = relativePath
        elif type(relativePath) is str and relativePath:
            raw = Path(relativePath)
        else:
            raise WorkspacePathError("Workspace path must be a non-empty relative string or pathlib.Path.")
        if raw.is_absolute():
            raise WorkspacePathError("Workspace path must be relative.")
        pure = PurePath(raw)
        if not pure.parts or pure == PurePath(".") or any(part == ".." for part in pure.parts):
            raise WorkspacePathError("Workspace path must name a file beneath the workspace root.")
        destination = (self._root / raw).resolve()
        try:
            destination.relative_to(self._root.resolve())
        except ValueError as err:
            raise WorkspacePathError("Workspace path escapes the workspace root.") from err
        return destination

    def _requireActive(self) -> None:
        """Rejects operations after the workspace lifetime has ended."""
        if self._closed:
            raise RuntimeError("EphemeralWorkspace is already closed.")
