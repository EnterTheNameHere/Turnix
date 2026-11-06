# file: backend/workspace/runtime.py ; version: 2
"""Actant-owned ephemeral filesystem workspaces for invocation-local tool inputs."""

from __future__ import annotations

import shutil
import tempfile
import hashlib
import os
import stat
import time
from dataclasses import dataclass
from threading import RLock
from pathlib import Path, PurePath

from backend.core.errors import ActantError

__all__ = ["EphemeralWorkspace", "WorkspaceError", "WorkspacePathError", "WorkspaceWriteError",
           "WorkspaceRead", "WorkspaceReadError", "WorkspaceFileIdentity"]


class WorkspaceError(ActantError, RuntimeError):
    """Base error for Actant-mediated ephemeral-workspace failures."""


class WorkspacePathError(WorkspaceError):
    """Raised when a requested workspace-relative path escapes its workspace."""


class WorkspaceWriteError(WorkspaceError):
    """Raised when Actant cannot materialize an ephemeral workspace artifact."""


class WorkspaceReadError(WorkspaceError):
    """Expose only a fixed read category, without private paths or raw causes."""

    def __init__(self, category):
        """Keep safe category evidence independent of the failed file contents."""
        super().__init__("Workspace read failed.")
        self.category = category


@dataclass(frozen=True, slots=True, repr=False)
class WorkspaceFileIdentity:
    """Observed descriptor metadata, not a stable portable file authority."""

    device: int
    inode: int
    size: int
    mtimeNs: int
    ctimeNs: int

    def __repr__(self):
        """Do not disclose local filesystem metadata in incidental diagnostics."""
        return "<WorkspaceFileIdentity>"

    def snapshot(self):
        """Return detached native metadata with no hidden active resource."""
        return dict(device=self.device, inode=self.inode, size=self.size,
                    mtimeNs=self.mtimeNs, ctimeNs=self.ctimeNs)


@dataclass(frozen=True, slots=True, repr=False)
class WorkspaceRead:
    """Own exact bounded bytes and observations, never an active file handle."""

    data: bytes
    relativePath: str
    observedTimeNs: int
    identity: WorkspaceFileIdentity

    def __repr__(self):
        """Prevent generic exception snapshotters from exposing private file data."""
        return "<WorkspaceRead>"

    @property
    def bytes(self):
        """Return the length of exactly the captured payload."""
        return len(self.data)

    @property
    def sha256(self):
        """Hash the already bounded immutable bytes, without another file read."""
        return hashlib.sha256(self.data).hexdigest()

    def snapshot(self):
        """Export detached metadata; private data remains explicitly separate."""
        return dict(schemaId="actant.workspace.read@1", relativePath=self.relativePath,
                    bytes=self.bytes, sha256=self.sha256, observedTimeNs=self.observedTimeNs,
                    identity=self.identity.snapshot())


def _identity(info):
    """Compare opened-file observations without claiming hostile-mutation exclusion."""
    return WorkspaceFileIdentity(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


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

    __slots__ = ("_closed", "_root", "_lane", "_pins")

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
        self._lane = RLock()
        self._pins = 0

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
        with self._lane:
            self._closed = True
            remove = self._pins == 0
        if remove:
            shutil.rmtree(self._root, ignore_errors=True)

    def _pin(self):
        """Keep files owned until exact process workers and ACK callback settle."""
        with self._lane:
            self._requireActive()
            self._pins += 1

    def _unpin(self):
        """Release one internal process lease; deletion remains best effort."""
        with self._lane:
            self._pins -= 1
            remove = self._closed and self._pins == 0
        if remove:
            shutil.rmtree(self._root, ignore_errors=True)

    def readBytes(self, relativePath, *, maxBytes):
        """Observe one regular contained file with a maxBytes+1 allocation bound.

        Detected path/metadata changes fail without retry. Same-size hostile
        rewrites with restored metadata are outside this observation contract.
        """
        if type(maxBytes) is not int or not 1 <= maxBytes <= 1048576:
            raise ValueError("Invalid workspace read bound.")
        category = None
        try:
            with self._lane:
                if self._closed:
                    raise WorkspaceReadError("inactive")
                raw = str(relativePath) if isinstance(relativePath, Path) else relativePath
                if (type(raw) is not str or not raw or "\x00" in raw or ":" in raw
                        or any(p in ("", ".", "..") for p in raw.replace("\\", "/").split("/"))):
                    raise WorkspaceReadError("invalid-path")
                path = self._resolveRelative(raw)
                cursor = self._root
                for part in (None, *Path(raw).parts):
                    if part is not None:
                        cursor = cursor / part
                    info = cursor.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                        raise WorkspaceReadError("reparse")
                if not stat.S_ISREG(info.st_mode):
                    raise WorkspaceReadError("not-regular")
                self._pins += 1
            try:
                with path.open("rb") as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise WorkspaceReadError("not-regular")
                    data = source.read(maxBytes + 1)
                    after = os.fstat(source.fileno())
                with self._lane:
                    if self._closed:
                        raise WorkspaceReadError("inactive")
                if len(data) > maxBytes:
                    raise WorkspaceReadError("over-limit")
                current = path.lstat()
                cursor = self._root
                for part in (None, *Path(raw).parts):
                    if part is not None:
                        cursor = cursor / part
                    info = cursor.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                        raise WorkspaceReadError("changed")
                if (stat.S_ISLNK(current.st_mode) or getattr(current, "st_file_attributes", 0) & 1024
                        or _identity(before) != _identity(after)
                        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                        != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)):
                    # Windows descriptor/path ctime observations can differ even
                    # for unchanged files. Compare ctime only within fstat above.
                    raise WorkspaceReadError("changed")
                with self._lane:
                    if self._closed:
                        raise WorkspaceReadError("inactive")
                return WorkspaceRead(data, raw, time.time_ns(), _identity(after))
            finally:
                self._unpin()
        except WorkspaceReadError:
            raise
        except FileNotFoundError:
            category = "not-found"
        except WorkspacePathError:
            category = "invalid-path"
        except (OSError, ValueError):
            category = "read-failed"
        raise WorkspaceReadError(category) from None

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
