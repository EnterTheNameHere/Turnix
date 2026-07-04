# file: backend/content/contentRoot.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, typeName


@dataclass(frozen=True)
class ContentRoot:
    """
    Explicit content root selected by platform code.

    This is not root discovery, Pack discovery, layer priority, dependency
    solving, version resolution, or Pack loading.
    """

    rootId: str
    rootPath: Path


def createContentRoot(
    *,
    rootId: str,
    rootPath: Path,
) -> ContentRoot:
    cleanRootId = requireExactNonBlankString(rootId, "rootId")

    if not isinstance(rootPath, Path):
        raise UsageError(f"rootPath must be a pathlib.Path, not {typeName(rootPath)}.")

    return ContentRoot(
        rootId=cleanRootId,
        rootPath=rootPath,
    )
