# file: backend/bootstrap/bootstrapActivationSources.py
from __future__ import annotations

from pathlib import PurePosixPath

from backend.activation.activationDeclarationSource import (
    ActivationDeclarationSource,
    createActivationDeclarationSource,
)


def createBootstrapActivationDeclarationSources() -> tuple[ActivationDeclarationSource, ...]:
    """
    Return explicit bootstrap activation declaration sources.

    This is not filesystem discovery. The source list is intentionally
    hardcoded until discovery exists.
    """
    return (
        createActivationDeclarationSource(
            rootId="repo",
            declarationPath=PurePosixPath("first-party/bootstrap/activation.declaration.json5"),
        ),
    )
