# file: backend/io/actantIo.py
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.content.contentRootCatalog import ContentRootCatalog, getContentRoot
from backend.content.contentRootPath import resolveContentRootPath
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, requireRelativePurePosixPath, typeName
from backend.io.jsonio import JsonObject, loadJson5File, loadJsonFile, writeJsonFile, writeTextFileAtomically

if TYPE_CHECKING:
    from pathlib import Path, PurePosixPath


class ActantIo:
    """
    Root-relative I/O boundary.

    This object provides controlled file access by content root and POSIX path.
    It does not provide raw filesystem access, permission persistence, or Pack
    trust decisions. Writes are allowed only when the selected content root is
    currently writable.
    """

    def __init__(self, *, catalog: ContentRootCatalog) -> None:
        self._catalog = catalog

    def resolveReadPath(self, *, rootId: str, relativePath: PurePosixPath) -> Path:
        clearPath = requireRelativePurePosixPath(relativePath, "relativePath")
        clearRootId = requireExactNonBlankString(rootId, "rootId")
        contentRoot = getContentRoot(catalog=self._catalog, rootId=clearRootId)
        return resolveContentRootPath(contentRoot=contentRoot, relativePath=clearPath)

    def resolveWritePath(self, *, rootId: str, relativePath: PurePosixPath) -> Path:
        clearPath = requireRelativePurePosixPath(relativePath, "relativePath")
        clearRootId = requireExactNonBlankString(rootId, "rootId")
        contentRoot = getContentRoot(catalog=self._catalog, rootId=clearRootId)

        if not contentRoot.writable:
            raise UsageError(f"Content root is not writable: {clearRootId}.")

        return resolveContentRootPath(contentRoot=contentRoot, relativePath=clearPath)

    def readJson5(self, *, rootId: str, relativePath: PurePosixPath) -> JsonObject:
        path = self.resolveReadPath(rootId=rootId, relativePath=relativePath)
        return loadJson5File(path)

    def readJson(self, *, rootId: str, relativePath: PurePosixPath) -> JsonObject:
        path = self.resolveReadPath(rootId=rootId, relativePath=relativePath)
        return loadJsonFile(path)

    def readText(self, *, rootId: str, relativePath: PurePosixPath) -> str:
        path = self.resolveReadPath(rootId=rootId, relativePath=relativePath)

        try:
            return path.read_text(encoding="utf-8")

        except UnicodeDecodeError as err:
            raise UsageError(f"Failed to decode text file {path} as UTF-8: {err}.") from err
        except OSError as err:
            raise UsageError(f"Failed to read text file {path}: {err}.") from err

    def writeJson(self, *, rootId: str, relativePath: PurePosixPath, data: JsonObject) -> None:
        path = self.resolveWritePath(rootId=rootId, relativePath=relativePath)
        writeJsonFile(path, data)

    def writeText(self, *, rootId: str, relativePath: PurePosixPath, text: str) -> None:
        if not isinstance(text, str):
            raise UsageError(f"text must be a string, not {typeName(text)}.")

        path = self.resolveWritePath(rootId=rootId, relativePath=relativePath)
        writeTextFileAtomically(path=path, text=text)
