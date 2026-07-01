# file: backend/core/paths.py
from __future__ import annotations

from functools import cache
from pathlib import Path

from backend.core.errors import InternalError


@cache
def getRepoRoot() -> Path:
    """
    Returns the repository root for the checked-out implementation tree.

    This is not Workspace resolution and does not create RuntimeHost ownership.
    It is only an early bootstrap helper for trusted backend code.
    """
    return findRepoRoot()


def findRepoRoot(startPath: Path | None = None) -> Path:
    if startPath is None:
        startPath = Path(__file__).resolve()

    current = startPath if startPath.is_dir() else startPath.parent

    for candidate in [current, *current.parents]:
        if isRepoRoot(candidate):
            return candidate

    raise InternalError(f"Could not find repository root starting from {startPath}")


def isRepoRoot(path: Path) -> bool:
    """
    Checks if the given path is a repository root.
    """
    return (
        (path / "docs").is_dir()
        and (path / "backend").is_dir()
        and (path / "setup.ps1").is_file()
    )
