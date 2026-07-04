# file: backend/activation/activationDeclarationSourceProvider.py
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.activation.activationDeclarationSource import ActivationDeclarationSource


class ActivationDeclarationSourceProvider(Protocol):
    """
    Boundary for code that supplies activation declaration sources.

    Discovery will later plug into this seam by producing:

        tuple[ActivationDeclarationSource, ...]

    This protocol does not define discovery behaviour.
    """

    def getActivationDeclarationSources(self) -> tuple[ActivationDeclarationSource, ...]:
        ...
