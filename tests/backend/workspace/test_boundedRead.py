# file: tests/backend/workspace/test_boundedRead.py ; version: 1
"""Bounded exact workspace observations, detected changes and process lease lifetime."""
from pathlib import Path
import hashlib
import os

import pytest

from backend.workspace import EphemeralWorkspace, WorkspaceReadError


def test_exact_bytes_identity_and_read_limit(monkeypatch):
    """Read cap+1 once and derive hash/length from exact raw CRLF bytes."""
    workspace = EphemeralWorkspace()
    path = workspace.materializeBytes("candidate.py", b"x\r\n\xc3\xa9")
    opened, sizes = Path.open, []
    class ReadProxy:
        """Record actual read sizes without replacing native descriptor observations."""

        def __init__(self, source):
            """Own the wrapped native file only for the context manager."""
            self.source = source

        def __enter__(self):
            """Provide the bounded read observer."""
            return self

        def __exit__(self, *args):
            """Close the exact wrapped descriptor."""
            self.source.close()

        def fileno(self):
            """Expose the original descriptor for fstat identity."""
            return self.source.fileno()

        def read(self, size):
            """Reject any accidentally unbounded or oversized read."""
            sizes.append(size)
            assert size == 6
            return self.source.read(size)
    def tracked(file, *args, **kwargs):
        """Instrument only the selected native workspace read."""
        value = opened(file, *args, **kwargs)
        return ReadProxy(value) if file == path else value
    monkeypatch.setattr(Path, "open", tracked)
    try:
        result = workspace.readBytes("candidate.py", maxBytes=5)
        assert result.data == b"x\r\n\xc3\xa9" and result.bytes == 5
        assert result.sha256 == hashlib.sha256(result.data).hexdigest()
        assert result.snapshot()["identity"]["size"] == 5
        assert sizes == [6] and result.data not in repr(result).encode()
    finally:
        workspace.close()


@pytest.mark.parametrize("path", ["../x", "x/../y", "/tmp/x", "C:/x", "x:stream", "x//y", ".", ""])
def test_reject_paths_without_private_diagnostics(path):
    """Reject traversal and platform-specific alternate path namespaces."""
    workspace = EphemeralWorkspace()
    try:
        with pytest.raises(WorkspaceReadError):
            workspace.readBytes(path, maxBytes=4)
    finally:
        workspace.close()


def test_oversize_missing_directory_and_inactive():
    """Unavailable bytes remain explicit fixed categories, not partial successful files."""
    workspace = EphemeralWorkspace()
    workspace.materializeText("nested/file.py", "oversized")
    for path, cap, expected in (("nested/file.py", 2, "over-limit"),
                                ("missing", 2, "not-found"), ("nested", 2, "not-regular")):
        with pytest.raises(WorkspaceReadError) as error:
            workspace.readBytes(path, maxBytes=cap)
        assert error.value.category == expected
    workspace.close()
    with pytest.raises(WorkspaceReadError) as error:
        workspace.readBytes("nested/file.py", maxBytes=20)
    assert error.value.category == "inactive"


def test_detect_metadata_mutation_without_second_read(monkeypatch):
    """Reject an opened file that changes during observation without retry."""
    workspace = EphemeralWorkspace()
    path = workspace.materializeBytes("candidate", b"a")
    actual = os.fstat
    calls = []
    def changing(descriptor):
        """Change after first native identity snapshot, preserving actual file access."""
        info = actual(descriptor)
        calls.append(1)
        if len(calls) == 1:
            path.write_bytes(b"different")
        return info
    monkeypatch.setattr(os, "fstat", changing)
    try:
        with pytest.raises(WorkspaceReadError) as error:
            workspace.readBytes("candidate", maxBytes=64)
        assert error.value.category == "changed"
        assert len(calls) == 2
    finally:
        workspace.close()


def test_process_pin_defers_close_but_revokes_reads():
    """Close ends workspace authority immediately and defers deletion to exact owner."""
    workspace = EphemeralWorkspace()
    path = workspace.materializeBytes("source", b"private")
    workspace._pin()
    workspace.close()
    assert path.exists()
    with pytest.raises(WorkspaceReadError):
        workspace.readBytes("source", maxBytes=10)
    workspace._unpin()
    assert not path.exists()


def test_reparse_observation_rejected_before_file_open(monkeypatch):
    """Native reparse-point metadata denies even an otherwise contained file."""
    from types import SimpleNamespace
    workspace = EphemeralWorkspace()
    path = workspace.materializeBytes("candidate", b"private")
    actual = Path.lstat
    def reparse(file, *args, **kwargs):
        """Expose Windows-style reparse metadata for the selected path."""
        info = actual(file, *args, **kwargs)
        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=1024) if file == path else info
    monkeypatch.setattr(Path, "lstat", reparse)
    try:
        with pytest.raises(WorkspaceReadError) as error:
            workspace.readBytes("candidate", maxBytes=32)
        assert error.value.category == "reparse"
    finally:
        monkeypatch.undo()
        workspace.close()


def test_close_during_bounded_read_returns_no_live_snapshot(monkeypatch):
    """Lifetime revocation during actual descriptor read retains then deletes safely."""
    workspace = EphemeralWorkspace()
    path = workspace.materializeBytes("candidate", b"private")
    actual = Path.open
    class ClosingRead:
        """End workspace lifetime while the exact read pin still owns its files."""

        def __init__(self, source):
            """Wrap the native descriptor for one read."""
            self.source = source

        def __enter__(self):
            """Return the instrumented reader."""
            return self

        def __exit__(self, *args):
            """Release native descriptor before deferred directory deletion."""
            self.source.close()

        def fileno(self):
            """Return exact underlying descriptor for metadata checks."""
            return self.source.fileno()

        def read(self, size):
            """Revoke lifetime during read without deleting still-pinned files."""
            workspace.close()
            assert path.exists()
            return self.source.read(size)
    def opened(file, *args, **kwargs):
        """Instrument only the exact file under test."""
        value = actual(file, *args, **kwargs)
        return ClosingRead(value) if file == path else value
    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(WorkspaceReadError) as error:
        workspace.readBytes("candidate", maxBytes=32)
    assert error.value.category == "inactive"
    assert not path.exists()


def test_path_ctime_difference_preserves_descriptor_observation(monkeypatch):
    """Different Windows path/descriptor ctime semantics do not fabricate mutation."""
    from types import SimpleNamespace
    workspace = EphemeralWorkspace()
    path = workspace.materializeBytes("candidate", b"unchanged")
    actual = Path.lstat
    def pathMetadata(file, *args, **kwargs):
        """Vary only path ctime; opened descriptor metadata remains unchanged."""
        info = actual(file, *args, **kwargs)
        if file != path:
            return info
        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0,
            st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns+1000)
    monkeypatch.setattr(Path, "lstat", pathMetadata)
    try:
        assert workspace.readBytes("candidate", maxBytes=32).data == b"unchanged"
    finally:
        monkeypatch.undo()
        workspace.close()
