# file: backend/llm/llmProviderRegistry.py ; version 3
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.validation import requireExactNonBlankString, requireInstance, typeName
from backend.llm.errors import (
    LlmProviderAlreadyRegisteredError,
    LlmProviderContractError,
    LlmProviderNotRegisteredError,
)
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

if TYPE_CHECKING:
    from backend.llm.llmTypes import LlmStreamProvider

__all__: list[str] = [
    "LlmStreamProviderRegistrationEntry",
    "LlmStreamProviderRegistry",
]


@dataclass(frozen=True, slots=True)
class LlmStreamProviderRegistrationEntry:
    """Represents one named provider registration and its runtime owner."""

    providerName: str
    ownerId: PackCodeEntryInstanceId
    provider: LlmStreamProvider

    def __post_init__(self) -> None:
        """Validates the LLM stream-provider registration entry."""
        requireExactNonBlankString(self.providerName, "providerName")

        requireInstance(self.ownerId, PackCodeEntryInstanceId, "ownerId")

        _validateProviderContract(self.provider)


class LlmStreamProviderRegistry:
    """Stores runtime-owned named LLM stream-provider registrations."""

    def __init__(self) -> None:
        """Initializes an LLM stream-provider registry."""
        self._streamProviders: dict[
            str,
            LlmStreamProviderRegistrationEntry,
        ] = {}

    def register(
        self,
        *,
        providerName: str,
        ownerId: PackCodeEntryInstanceId,
        provider: LlmStreamProvider,
    ) -> None:
        """Registers one named LLM stream provider."""
        entry = LlmStreamProviderRegistrationEntry(
            providerName=providerName,
            ownerId=ownerId,
            provider=provider,
        )

        existingEntry = self._streamProviders.get(entry.providerName)
        if existingEntry is not None:
            raise LlmProviderAlreadyRegisteredError(
                f"Provider {entry.providerName!r} is already registered "
                f"by {existingEntry.ownerId!r}.",
            )

        self._streamProviders[entry.providerName] = entry

    def require(self, providerName: str) -> LlmStreamProvider:
        """Returns the named provider or raises when it is not registered."""
        validatedProviderName = requireExactNonBlankString(
            providerName,
            "providerName",
        )
        existingEntry = self._streamProviders.get(validatedProviderName)
        if existingEntry is None:
            raise LlmProviderNotRegisteredError(
                f"Provider {validatedProviderName} is not registered.",
            )
        return existingEntry.provider

    def has(self, *, providerName: str) -> bool:
        """Returns whether the named provider is registered."""
        cleanProviderName = requireExactNonBlankString(
            providerName,
            "providerName",
        )
        return cleanProviderName in self._streamProviders

    def snapshot(self) -> tuple[LlmStreamProviderRegistrationEntry, ...]:
        """Returns an immutable provider snapshot ordered by provider name."""
        return tuple(
            self._streamProviders[name]
            for name in sorted(self._streamProviders)
        )


def _validateProviderContract(provider: LlmStreamProvider) -> None:
    """Validates the required LLM stream-provider members."""
    missingMembers = [
        member
        for member in ("getExecutionProfile", "stream")
        if not callable(getattr(provider, member, None))
    ]

    if missingMembers:
        raise LlmProviderContractError(
            f"{typeName(provider)} does not expose callable "
            f"{', '.join(missingMembers)}.",
        )
