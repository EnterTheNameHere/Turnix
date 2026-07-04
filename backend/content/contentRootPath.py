# file: backend/content/contentRootPath.py
from __future__ import annotations

from pathlib import Path, PurePosixPath

from backend.content.contentRoot import ContentRoot
from backend.core.errors import UsageError
from backend.core.validation import typeName


def resolveContentRootPath(
    *,
    contentRoot: ContentRoot,
    relativePath: PurePosixPath,
) -> Path:
    cleanRelativePath = validateContentRootRelativePath(
        relativePath=relativePath,
    )

    if not isinstance(contentRoot, ContentRoot):
        raise UsageError(f"contentRoot must be a ContentRoot, not {typeName(contentRoot)}.")

    return contentRoot.rootPath.joinpath(*cleanRelativePath.parts)


def validateContentRootRelativePath(
    *,
    relativePath: PurePosixPath,
) -> PurePosixPath:
    if not isinstance(relativePath, PurePosixPath):
        raise UsageError(f"relativePath must be a pathlib.PurePosixPath, not {typeName(relativePath)}.")

    relativeText = relativePath.as_posix()

    if relativeText == ".":
        raise UsageError("relativePath must not be empty.")

    if relativePath.is_absolute():
        raise UsageError(f"relativePath must not be absolute: {relativeText}.")

    if ".." in relativePath.parts:
        raise UsageError(f"relativePath must not contain '..': {relativeText}.")

    return relativePath
