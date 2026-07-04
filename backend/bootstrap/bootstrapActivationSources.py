# file: backend/bootstrap/bootstrapActivationSources.py
from __future__ import annotations

from pathlib import PurePosixPath

from backend.activation.activationDeclarationDiscovery import (
    ActivationDeclarationDiscoveryScope,
    createActivationDeclarationDiscoveryScope,
)
from backend.activation.activationDeclarationSource import (
    ActivationDeclarationSource,
    createActivationDeclarationSource,
)


def createBootstrapActivationDeclarationSources() -> tuple[ActivationDeclarationSource, ...]:
    """
    Return explicit bootstrap activation declaration sources.

    This is not filesystem discovery. The source list is intentionally
    hardcoded until discovery is used by the caller.
    """
    return (
        createActivationDeclarationSource(
            rootId="repo",
            declarationPath=PurePosixPath("first-party/bootstrap/activation.declaration.json5"),
        ),
    )


def createBootstrapActivationDeclarationDiscoveryScope() -> ActivationDeclarationDiscoveryScope:
    """
    Return the explicit temporary bootstrap activation declaration discovery scope.

    This is still activation-only discovery. It does not discover Pack manifests.
    """
    return createActivationDeclarationDiscoveryScope(
        rootId="repo",
        searchBasePath=PurePosixPath("first-party"),
    )
