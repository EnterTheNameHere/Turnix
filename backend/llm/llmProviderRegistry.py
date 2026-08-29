# file: backend/llm/llmProviderRegistry.py ; version: 6
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from backend.core.validation import (
    requireExactNonBlankString,
    requireInstance,
    typeName,
)
from backend.llm.errors import (
    LlmProviderAlreadyRegisteredError,
    LlmProviderContractError,
    LlmProviderNotRegisteredError,
)
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

if TYPE_CHECKING:
    from backend.llm.llmTypes import LlmStreamProvider

__all__: list[str] = [
    "LlmStreamProviderRegistration",
    "LlmStreamProviderRegistrationEntry",
    "LlmStreamProviderRegistry",
]


@dataclass(frozen=True, slots=True)
class LlmStreamProviderRegistrationEntry:
    """
    Represents one immutable named LLM stream-provider registration entry.

    providerName is the exact registry key used to select the provider.
    Provider names are validated but never normalized, trimmed, case-folded, or
    otherwise rewritten.

    ownerId identifies the loaded Pack code-entry instance responsible for the
    registration. Runtime ownership remains part of the captured entry so
    execution, diagnostics, tracing, and Pack lifecycle management can
    attribute the provider implementation to its actual producer.

    provider is the registered implementation of the LlmStreamProvider
    structural contract.

    Registration entries are immutable data, not lifecycle capabilities. A
    processing run may safely retain an entry after the corresponding registry
    registration has been removed. Holding an entry does not grant authority to
    unregister or otherwise mutate registry state.

    The provider object itself is not recursively frozen. Provider
    implementations shared across processing runs are responsible for behaviour
    safe for their intended execution model.
    """

    providerName: str
    ownerId: PackCodeEntryInstanceId
    provider: LlmStreamProvider

    def __post_init__(self) -> None:
        """Validates the provider identity, ownership, and structural contract."""
        requireExactNonBlankString(self.providerName, "providerName")
        requireInstance(self.ownerId, PackCodeEntryInstanceId, "ownerId")
        _validateProviderContract(self.provider)


class LlmStreamProviderRegistration:
    """
    Controls the lifecycle of one specific provider registration.

    A registration handle is returned by LlmStreamProviderRegistry.register().
    It grants authority to remove only the exact registration created by that
    call.

    unregister() is identity-safe. If the registration has already been
    removed, or its provider name has subsequently been reused by another
    registration, the stale handle cannot remove the newer registration.

    The handle does not keep the registration discoverable. Registry removal
    affects future provider lookup immediately, while processing runs that
    already captured the immutable registration entry may continue using that
    entry according to the surrounding Pack/runtime lifecycle policy.
    """

    __slots__ = (
        "_entry",
        "_registry",
    )

    def __init__(
        self,
        *,
        registry: LlmStreamProviderRegistry,
        entry: LlmStreamProviderRegistrationEntry,
    ) -> None:
        """
        Initializes a lifecycle handle for one exact registry entry.

        This constructor is intended for LlmStreamProviderRegistry. Extension
        code obtains handles through register().
        """
        requireInstance(registry, LlmStreamProviderRegistry, "registry")
        requireInstance(entry, LlmStreamProviderRegistrationEntry, "entry")

        self._registry = registry
        self._entry = entry

    @property
    def entry(self) -> LlmStreamProviderRegistrationEntry:
        """
        Returns the immutable provider-registration entry owned by this handle.

        The returned entry may remain useful as immutable registration
        information after unregister() removes it from registry discovery.
        """
        return self._entry

    def unregister(self) -> bool:
        """
        Removes this exact provider registration if it is still active.

        Returns:
            True if this call removed the registration. False if the exact
            registration was already absent or had been replaced by a later
            registration using the same provider name.

        This operation is idempotent. Repeated calls after successful removal
        return False.

        A stale handle never unregisters a newer registration.

        """
        return self._registry._unregisterEntry(self._entry)


class LlmStreamProviderRegistry:
    """
    Owns named runtime registrations of LLM stream providers.

    Provider names are unique within one registry. register() is additive:
    registering a name that already exists is rejected rather than silently
    replacing the existing provider.

    register() returns an LlmStreamProviderRegistration lifecycle handle.
    Normal code unregisters through that handle, which can remove only the
    exact registration created by the corresponding register() call.

    requireRegistration() returns only the immutable registration entry. This
    deliberately separates provider discovery from registry-mutation authority:
    processing runs may retain registration information and the provider object
    without gaining the ability to unregister it.

    unregisterOwnedBy() is the bulk cleanup boundary for Pack/runtime lifecycle
    management. It removes every registration currently owned by one exact
    PackCodeEntryInstanceId. It is idempotent when that owner has no active
    registrations.

    Registry access is synchronized. Concurrent registration, lookup,
    membership checks, unregistration, bulk cleanup, and snapshot acquisition
    cannot observe partial registry mutation.

    Synchronization protects registry state only. It does not serialize
    provider execution and does not make provider implementations thread-safe.

    Removing a registration affects provider discovery for subsequently
    resolving runs. It does not invalidate immutable entries already captured
    by active runs; final lifetime management of Pack-owned executable code
    belongs to the surrounding Pack/runtime lifecycle.
    """

    __slots__ = (
        "_lock",
        "_streamProviders",
    )

    def __init__(self) -> None:
        """Initializes an empty synchronized LLM stream-provider registry."""
        self._lock = Lock()
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
    ) -> LlmStreamProviderRegistration:
        """
        Registers one named LLM stream provider for future resolution.

        Registration-entry construction and structural provider validation
        occur before the registry lock is acquired. The lock protects the
        uniqueness check and registry mutation.

        Args:
            providerName:
                Exact registry name through which the provider is selected.
            ownerId:
                Loaded Pack code-entry instance responsible for the provider.
            provider:
                Provider implementation to register.

        Returns:
            A lifecycle handle capable of unregistering only this exact
            registration.

        Raises:
            TypeError:
                If providerName or ownerId violates its runtime type contract.
            ValueError:
                If providerName is blank or contains surrounding whitespace.
            LlmProviderContractError:
                If provider does not satisfy the required structural provider
                contract.
            LlmProviderAlreadyRegisteredError:
                If providerName is already registered.

        """
        entry = LlmStreamProviderRegistrationEntry(
            providerName=providerName,
            ownerId=ownerId,
            provider=provider,
        )

        with self._lock:
            existingEntry = self._streamProviders.get(entry.providerName)
            if existingEntry is not None:
                raise LlmProviderAlreadyRegisteredError(
                    "LLM provider name is already registered; "
                    f"providerName={entry.providerName!r}, "
                    f"existingOwnerId={existingEntry.ownerId!r}, "
                    f"requestedOwnerId={entry.ownerId!r}.",
                )

            self._streamProviders[entry.providerName] = entry

        return LlmStreamProviderRegistration(registry=self, entry=entry)

    def requireRegistration(
        self,
        providerName: str,
    ) -> LlmStreamProviderRegistrationEntry:
        """
        Returns one currently registered provider entry.

        Args:
            providerName:
                Exact registered provider name.

        Returns:
            The immutable registration entry associated with providerName.

        Raises:
            TypeError:
                If providerName is not an exact built-in string.
            ValueError:
                If providerName is blank or contains surrounding whitespace.
            LlmProviderNotRegisteredError:
                If no active registration exists under providerName.

        """
        cleanProviderName = requireExactNonBlankString(
            providerName,
            "providerName",
        )

        with self._lock:
            existingEntry = self._streamProviders.get(cleanProviderName)

        if existingEntry is None:
            raise LlmProviderNotRegisteredError(
                "LLM provider is not registered; "
                f"providerName={cleanProviderName!r}.",
            )

        return existingEntry

    def has(
        self,
        *,
        providerName: str,
    ) -> bool:
        """
        Returns whether an exact provider name is currently registered.

        Args:
            providerName:
                Exact provider name to test.

        Returns:
            True if an active registration exists under providerName.
            Otherwise False.

        """
        cleanProviderName = requireExactNonBlankString(
            providerName,
            "providerName",
        )

        with self._lock:
            return cleanProviderName in self._streamProviders

    def unregisterOwnedBy(
        self,
        *,
        ownerId: PackCodeEntryInstanceId,
    ) -> tuple[LlmStreamProviderRegistrationEntry, ...]:
        """
        Removes all active provider registrations owned by one Pack instance.

        This is the registry's bulk cleanup boundary for Pack/runtime lifecycle
        management. Ownership is matched using the exact
        PackCodeEntryInstanceId value, so cleanup of an older Pack code-entry
        instance cannot remove registrations belonging to a newer instance.

        Args:
            ownerId:
                Pack code-entry instance whose active registrations should be
                removed.

        Returns:
            Immutable registration entries that were removed, ordered
            lexicographically by provider name. An empty tuple means that the
            owner had no active provider registrations.

        This operation is idempotent with respect to an owner that has no
        current registrations.

        """
        requireInstance(ownerId, PackCodeEntryInstanceId, "ownerId")

        with self._lock:
            removedEntries = tuple(
                entry
                for entry in self._streamProviders.values()
                if entry.ownerId == ownerId
            )

            for entry in removedEntries:
                del self._streamProviders[entry.providerName]

        return tuple(
            sorted(
                removedEntries,
                key=lambda entry: entry.providerName,
            ),
        )

    def snapshot(self) -> tuple[LlmStreamProviderRegistrationEntry, ...]:
        """
        Returns an immutable deterministic snapshot of active registrations.

        Registrations are ordered lexicographically by their exact provider
        names. Later registry mutation does not alter the returned tuple or its
        immutable registration entries.
        """
        with self._lock:
            return tuple(
                self._streamProviders[name]
                for name in sorted(self._streamProviders)
            )

    def _unregisterEntry(
        self,
        entry: LlmStreamProviderRegistrationEntry,
    ) -> bool:
        """
        Removes one exact registration entry if it is still active.

        Identity comparison is deliberate. Matching only providerName, ownerId,
        or provider object would allow a stale lifecycle handle to remove a
        later registration that reused some or all of those values.

        Returns:
            True if entry was the active registry entry and was removed.
            Otherwise False.

        """
        requireInstance(entry, LlmStreamProviderRegistrationEntry, "entry")

        with self._lock:
            currentEntry = self._streamProviders.get(entry.providerName)

            if currentEntry is not entry:
                return False

            del self._streamProviders[entry.providerName]
            return True


def _validateProviderContract(provider: LlmStreamProvider) -> None:
    """
    Validates the structural runtime contract required of LLM stream provider.

    Structural validation verifies only that the provider exposes callable
    getExecutionProfile() and stream() members. It does not execute provider
    code or establish the semantic correctness of values later produced by
    those methods.

    Raises:
        LlmProviderContractError:
            If provider is missing one or more required callable members.

    """
    requiredMembers = ("getExecutionProfile", "stream")
    missingMembers = tuple(
        member
        for member in requiredMembers
        if not callable(getattr(provider, member, None))
    )

    if missingMembers:
        missingText = ", ".join(f"{member}()" for member in missingMembers)
        raise LlmProviderContractError(
            "LLM stream provider is missing required callable members; "
            f"missing={missingText}, "
            f"received={typeName(provider)}.",
        )
