# file: backend/activation/activationDeclarationDiscovery.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.activation.activationDeclarationSource import createActivationDeclarationSource
from backend.content.contentRoot import ContentRoot
from backend.content.contentRootPath import resolveContentRootPath, validateContentRootRelativePath
from backend.core.errors import UsageError
from backend.core.validation import requireExactNonBlankString, typeName

if TYPE_CHECKING:
    from pathlib import PurePosixPath

    from backend.activation.activationDeclarationSource import ActivationDeclarationSource


TEMPORARY_ACTIVATION_DECLARATION_FILENAME = "activation.declaration.json5"
PACK_MANIFEST_FILENAME = "manifest.json5"


@dataclass(frozen=True)
class ActivationDeclarationDiscoveryScope:
    """
    Explicit temporary activation declaration discovery scope.

    This scope is intentionally narrow. It discovers temporary
    activation.declaration.json5 files only. It does not discover Pack
    manifests, parse Pack manifests, resolve dependencies, or scan all
    content kinds.
    """

    rootId: str
    searchBasePath: PurePosixPath
    filename: str


@dataclass(frozen=True)
class ActivationDeclarationDiscoveryReport:
    """
    Source discovery report for one content root and one discovery scope.

    Discovery produces source descriptors only. It does not load declarations,
    materialize plans, activate modules, or register capabilities.
    """

    rootId: str
    scope: ActivationDeclarationDiscoveryScope
    sources: tuple[ActivationDeclarationSource, ...]


def createActivationDeclarationDiscoveryScope(
    *,
    rootId: str,
    searchBasePath: PurePosixPath,
    filename: str = TEMPORARY_ACTIVATION_DECLARATION_FILENAME,
) -> ActivationDeclarationDiscoveryScope:
    cleanRootId = requireExactNonBlankString(rootId, "rootId")
    cleanSearchBasePath = validateContentRootRelativePath(
        relativePath=searchBasePath,
    )
    cleanFilename = validateActivationDeclarationDiscoveryFilename(
        filename=filename,
    )

    return ActivationDeclarationDiscoveryScope(
        rootId=cleanRootId,
        searchBasePath=cleanSearchBasePath,
        filename=cleanFilename,
    )


def validateActivationDeclarationDiscoveryFilename(
    *,
    filename: str,
) -> str:
    cleanFilename = requireExactNonBlankString(filename, "filename")

    if "/" in cleanFilename or "\\" in cleanFilename:
        raise UsageError(f"filename must be a filename only, not a path: {cleanFilename}.")

    if cleanFilename == PACK_MANIFEST_FILENAME:
        raise UsageError(
            f"{PACK_MANIFEST_FILENAME} is reserved for Pack manifests, not activation declaration discovery.",
        )

    if cleanFilename != TEMPORARY_ACTIVATION_DECLARATION_FILENAME:
        raise UsageError(
            "activation declaration discovery currently supports only "
            f"{TEMPORARY_ACTIVATION_DECLARATION_FILENAME}, not {cleanFilename}.",
        )

    return cleanFilename


def discoverActivationDeclarationSourcesInRoot(
    *,
    contentRoot: ContentRoot,
    scope: ActivationDeclarationDiscoveryScope,
) -> ActivationDeclarationDiscoveryReport:
    if not isinstance(contentRoot, ContentRoot):
        raise UsageError(f"contentRoot must be a ContentRoot, not {typeName(contentRoot)}.")

    if not isinstance(scope, ActivationDeclarationDiscoveryScope):
        raise UsageError(f"scope must be an ActivationDeclarationDiscoveryScope, not {typeName(scope)}.")

    if contentRoot.rootId != scope.rootId:
        raise UsageError(
            f"Discovery scope rootId does not match content root: "
            f"scope={scope.rootId}, contentRoot={contentRoot.rootId}.",
        )

    searchRootPath = resolveContentRootPath(
        contentRoot=contentRoot,
        relativePath=scope.searchBasePath,
    )

    if not searchRootPath.exists():
        raise UsageError(f"Activation declaration discovery path does not exist: {searchRootPath}.")

    if not searchRootPath.is_dir():
        raise UsageError(f"Activation declaration discovery path must be a directory: {searchRootPath}.")

    sources: list[ActivationDeclarationSource] = []

    for childPath in sorted(searchRootPath.iterdir(), key=lambda path: path.name):
        if not childPath.is_dir():
            continue

        declarationHostPath = childPath / scope.filename
        if not declarationHostPath.is_file():
            continue

        declarationPath = scope.searchBasePath / childPath.name / scope.filename

        sources.append(
            createActivationDeclarationSource(
                rootId=contentRoot.rootId,
                declarationPath=declarationPath,
            ),
        )

    return ActivationDeclarationDiscoveryReport(
        rootId=contentRoot.rootId,
        scope=scope,
        sources=tuple(sources),
    )


def hasDiscoveredActivationDeclarationSource(
    *,
    report: ActivationDeclarationDiscoveryReport,
    rootId: str,
    declarationPath: PurePosixPath,
) -> bool:
    if not isinstance(report, ActivationDeclarationDiscoveryReport):
        raise UsageError(
            f"report must be an ActivationDeclarationDiscoveryReport, not {typeName(report)}.")

    expectedRootId = requireExactNonBlankString(rootId, "rootId")
    expectedPathText = validateContentRootRelativePath(relativePath=declarationPath).as_posix()

    return any(
        source.rootId == expectedRootId and source.declarationPath.as_posix() == expectedPathText
        for source in report.sources
    )
