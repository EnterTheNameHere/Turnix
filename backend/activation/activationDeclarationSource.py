# file: backend/activation/activationDeclarationSource.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.activation.activationDeclarationLoader import validateActivationDeclarationPath
from backend.core.validation import requireExactNonBlankString

if TYPE_CHECKING:
    from pathlib import PurePosixPath


@dataclass(frozen=True)
class ActivationDeclarationSource:
    """
    Explicit source descriptor for an activation declaration.

    This is not declaration discovery, Pack discovery, source priority,
    dependency solving, or Pack loading.
    """

    rootId: str
    declarationPath: PurePosixPath


def createActivationDeclarationSource(
    *,
    rootId: str,
    declarationPath: PurePosixPath,
) -> ActivationDeclarationSource:
    return ActivationDeclarationSource(
        rootId=requireExactNonBlankString(rootId, "rootId"),
        declarationPath=validateActivationDeclarationPath(declarationPath),
    )
