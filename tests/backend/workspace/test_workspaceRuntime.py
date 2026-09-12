# file: tests/backend/workspace/test_workspaceRuntime.py ; version: 1
"""Tests for Actant-owned ephemeral filesystem workspaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.workspace import EphemeralWorkspace, WorkspacePathError


def test_materializeBytesPublishesExactProcessVisiblePayload() -> None:
    """Materialized bytes are immediately visible and byte-identical."""
    workspace = EphemeralWorkspace()
    try:
        payload = b"line-1\r\nline-2\n\x00tail"
        path = workspace.materializeBytes("nested/candidate.bin", payload)

        assert path.is_absolute()
        assert path.read_bytes() == payload
        assert path.is_relative_to(workspace.root)
    finally:
        workspace.close()


def test_materializeTextUsesExactUtf8WithoutNewlineNormalization() -> None:
    """Text materialization writes exactly its UTF-8 encoding."""
    workspace = EphemeralWorkspace()
    try:
        text = "alpha\r\nbeta\nžluťoučký"
        path = workspace.materializeText("candidate.py", text)

        assert path.read_bytes() == text.encode("utf-8")
    finally:
        workspace.close()


def test_workspaceRejectsAbsoluteAndParentTraversal(tmp_path: Path) -> None:
    """Pack-selected artifact names cannot escape the private workspace root."""
    workspace = EphemeralWorkspace()
    try:
        with pytest.raises(WorkspacePathError):
            workspace.materializeText(tmp_path / "outside.py", "pass")
        with pytest.raises(WorkspacePathError):
            workspace.materializeText("../outside.py", "pass")
    finally:
        workspace.close()


def test_closeRemovesWorkspaceAndEndsAuthority() -> None:
    """Closing an ephemeral workspace removes its files and prevents reuse."""
    workspace = EphemeralWorkspace()
    root = workspace.root
    path = workspace.materializeText("candidate.py", "pass\n")
    assert path.exists()

    workspace.close()

    assert not root.exists()
    with pytest.raises(RuntimeError, match="already closed"):
        workspace.materializeText("later.py", "pass\n")


def test_closeIsIdempotent() -> None:
    """Repeated cleanup is harmless for failure-path and finally-block use."""
    workspace = EphemeralWorkspace()
    workspace.close()
    workspace.close()
