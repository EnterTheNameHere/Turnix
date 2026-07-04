# file: backend/activation/activationDeclarationLoader.py
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from backend.activation.activationAdapterKind import ActivationAdapterKind
from backend.activation.activationDeclaration import ActivationDeclaration, createActivationDeclaration
from backend.activation.activationSpec import ActivationSpec, createActivationSpec
from backend.content.contentRoot import ContentRoot
from backend.core.errors import UsageError
from backend.core.json5Loader import loadJson5File


DECLARATION_SUFFIX = ".json5"


def loadActivationDeclarationFromRoot(
    *,
    contentRoot: ContentRoot,
    declarationPath: PurePosixPath,
) -> ActivationDeclaration:
    return loadActivationDeclarationFile(
        basePath=contentRoot.rootPath,
        declarationPath=declarationPath,
    )


def loadActivationDeclarationFile(
    *,
    basePath: Path,
    declarationPath: PurePosixPath,
) -> ActivationDeclaration:
    resolvedPath = resolveActivationDeclarationPath(
        basePath=basePath,
        declarationPath=declarationPath,
    )

    return parseActivationDeclarationText(
        sourcePath=resolvedPath,
    )


def resolveActivationDeclarationPath(
    *,
    basePath: Path,
    declarationPath: PurePosixPath,
) -> Path:
    if not isinstance(basePath, Path):
        raise UsageError(f"basePath must be a pathlib.Path, not {type(basePath).__name__}.")

    cleanDeclarationPath = validateActivationDeclarationPath(declarationPath)

    return basePath.joinpath(*cleanDeclarationPath.parts)


def validateActivationDeclarationPath(declarationPath: PurePosixPath) -> PurePosixPath:
    if not isinstance(declarationPath, PurePosixPath):
        raise UsageError(f"declarationPath must be a pathlib.PurePosixPath, not {type(declarationPath).__name__}.")

    declarationText = declarationPath.as_posix()
    if declarationText == ".":
        raise UsageError("declarationPath must not be empty.")

    if declarationPath.is_absolute():
        raise UsageError(f"declarationPath must be relative: {declarationText}.")

    if ".." in declarationPath.parts:
        raise UsageError(f"declarationPath must not contain '..': {declarationText}.")

    if declarationPath.suffix != DECLARATION_SUFFIX:
        raise UsageError(f"declarationPath must end with '{DECLARATION_SUFFIX}': {declarationText}.")

    return declarationPath


def parseActivationDeclarationText(
    *,
    sourcePath: Path,
) -> ActivationDeclaration:
    loaded = loadJson5File(
        path=sourcePath,
    )

    if not isinstance(loaded, dict):
        raise UsageError(f"Activation declaration must be an object: {sourcePath}")

    return parseActivationDeclarationObject(
        data=loaded,
        sourcePath=sourcePath,
    )


def parseActivationDeclarationObject(
    *,
    data: dict[str, Any],
    sourcePath: Path,
) -> ActivationDeclaration:
    planId = requireStringField(
        data=data,
        fieldName="planId",
        sourcePath=sourcePath,
    )

    rawEntries = cast(dict[str, Any],data.get("entries"))
    if not isinstance(rawEntries, list):
        raise UsageError(f"Activation declaration must be a list: {sourcePath}.")

    entries: list[ActivationSpec] = []
    for index, rawEntry in enumerate(rawEntries):
        if not isinstance(rawEntry, dict):
            raise UsageError(f"Activation declaration entries[{index}] must be an object: {sourcePath}.")

        entries.append(
            parseActivationSpecObject(
                data=rawEntry,
                sourcePath=sourcePath,
                index=index,
            )
        )

    return createActivationDeclaration(
        planId=planId,
        entries=tuple(entries),
    )


def parseActivationSpecObject(
    *,
    data: dict[str, Any],
    sourcePath: Path,
    index: int,
) -> ActivationSpec:
    entryId = requireStringField(
        data=data,
        fieldName="entryId",
        sourcePath=sourcePath,
        index=index,
    )

    ownerId = requireStringField(
        data=data,
        fieldName="ownerId",
        sourcePath=sourcePath,
        index=index,
    )

    adapterKindText = requireStringField(
        data=data,
        fieldName="adapterKind",
        sourcePath=sourcePath,
        index=index,
    )

    sourceText = requireStringField(
        data=data,
        fieldName="source",
        sourcePath=sourcePath,
        index=index,
    )

    callableName = requireStringField(
        data=data,
        fieldName="callableName",
        sourcePath=sourcePath,
        index=index,
    )

    try:
        adapterKind = ActivationAdapterKind(adapterKindText)
    except ValueError as err:
        raise UsageError(
            f"Unsupported activation adapter kind at entries[{index}].adapterKind "
            f"in {sourcePath}: {adapterKindText}"
        ) from err

    return createActivationSpec(
        entryId=entryId,
        ownerId=ownerId,
        adapterKind=adapterKind,
        source=PurePosixPath(sourceText),
        callableName=callableName,
    )


def requireStringField(
    *,
    data: dict[str, Any],
    fieldName: str,
    sourcePath: Path,
    index: int | None = None,
) -> str:
    value = data.get(fieldName)
    if isinstance(value, str):
        return value

    location = f"entries[{index}].{fieldName}" if index is not None else fieldName
    raise UsageError(f"Activation declaration field {location} must be a string: {sourcePath}.")

