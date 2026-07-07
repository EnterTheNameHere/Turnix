# file: backend/content/contentRoot.py
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from backend.core.errors import UsageError
from backend.core.ids import uuidv4hex
from backend.core.validation import requireExactNonBlankString, typeName


@dataclass(frozen=True)
class ContentRoot:
    """
    Filesystem boundary where Actant may look for content.

    This is not root discovery, Pack discovery, layer priority, dependency
    solving, version resolution, or Pack loading.
    """

    rootId: str
    rootPath: Path

    @property
    def writable(self) -> bool:
        return canCreateAndDeleteInDirectory(self.rootPath)


def canCreateAndDeleteInDirectory(path: Path) -> bool:
    if not path.exists():
        return False

    if not path.is_dir():
        return False

    probePath = path / f".actant-write-test-{uuidv4hex()}.tmp"

    try:
        with probePath.open("xb") as file:
            file.write(b"")

        probePath.unlink()

    except OSError:
        with contextlib.suppress(OSError):
            probePath.unlink()

        return False

    else:
        return True


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
