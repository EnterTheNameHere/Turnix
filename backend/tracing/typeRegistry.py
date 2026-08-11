# file: backend/tracing/typeRegistry.py ; version: 2
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.collections import immutableMapping
from backend.core.validation import requireBool, requireInstance
from backend.tracing.errors import (
    TraceTypeConflictError,
    TraceTypeDefinitionCollisionError,
    TraceTypeDefinitionNotFoundError,
)
from backend.tracing.ids import TraceTypeDefinitionId
from backend.tracing.typeDefinitions import TraceTypeDefinition

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__: list[str] = [
    "TraceTypeRegistration",
    "TraceTypeRegistry",
]


@dataclass(frozen=True, slots=True)
class TraceTypeRegistration:
    """
    Reports the result of one trace-type registration operation.

    The returned definition is the canonical active instance held by the
    registry. ``isNew`` is true only when the operation inserted a previously
    unknown definition; reuse of an identical existing definition reports
    false.
    """

    definition: TraceTypeDefinition
    isNew: bool

    def __post_init__(self) -> None:
        """
        Validates the registration result.

        Raises:
            TypeError:
                If definition is not a TraceTypeDefinition or isNew is not an
                exact built-in boolean.

        """
        requireInstance(self.definition, TraceTypeDefinition, "definition")
        requireBool(self.isNew, "isNew")


class TraceTypeRegistry:
    """
    Owns active trace-type definitions for one Actant lifecycle.

    Definitions are indexed both by trace-type name and deterministic
    content-derived identity. A name may refer to only one definition during
    the lifecycle, and one deterministic identity may represent only one
    definition value.

    Registration and lookup are safe across threads. Snapshot retrieval does
    not expose the registry's mutable internal mappings.
    """

    def __init__(self) -> None:
        """Initializes an empty trace-type registry."""
        self._lock = threading.RLock()
        self._definitionIdByName: dict[str, TraceTypeDefinitionId] = {}
        self._definitionById: dict[
            TraceTypeDefinitionId,
            TraceTypeDefinition,
        ] = {}

    def register(
        self,
        definition: TraceTypeDefinition,
    ) -> TraceTypeRegistration:
        """
        Registers or reuses one active trace-type definition.

        Re-registering an identical definition reuses the registry's existing
        definition instance and reports ``isNew=False``.

        A trace-type name cannot change definition during the registry
        lifecycle. Likewise, deterministic identity equality is verified
        against complete definition equality so an identity collision cannot
        silently alias different definition content.

        Args:
            definition:
                Normalized portable definition to register.

        Returns:
            The active registry definition and whether this operation inserted
            it for the first time.

        Raises:
            TypeError:
                If definition is not a TraceTypeDefinition.
            TraceTypeConflictError:
                If the trace-type name is already associated with a different
                deterministic definition identity.
            TraceTypeDefinitionCollisionError:
                If matching deterministic identity information refers to
                different definition content.

        """
        cleanDefinition = requireInstance(
            definition,
            TraceTypeDefinition,
            "definition",
        )

        with self._lock:
            existingId = self._definitionIdByName.get(cleanDefinition.name)
            if existingId is not None:
                if existingId != cleanDefinition.traceTypeDefinitionId:
                    raise TraceTypeConflictError(
                        "Active trace-type name "
                        f"{cleanDefinition.name!r} is already registered as "
                        f"{existingId}, not "
                        f"{cleanDefinition.traceTypeDefinitionId}.",
                    )

                existingDefinition = self._definitionById[existingId]
                if existingDefinition != cleanDefinition:
                    raise TraceTypeDefinitionCollisionError(
                        "Registered trace-type definition content differs "
                        "despite matching name and deterministic ID; "
                        f"name={cleanDefinition.name!r}.",
                    )

                return TraceTypeRegistration(
                    definition=existingDefinition,
                    isNew=False,
                )

            existingDefinition = self._definitionById.get(
                cleanDefinition.traceTypeDefinitionId,
            )
            if existingDefinition is not None:
                if existingDefinition != cleanDefinition:
                    raise TraceTypeDefinitionCollisionError(
                        "Deterministic trace-type definition ID collision "
                        f"for {cleanDefinition.traceTypeDefinitionId}.",
                    )

                self._definitionIdByName[cleanDefinition.name] = (
                    cleanDefinition.traceTypeDefinitionId
                )

                return TraceTypeRegistration(
                    definition=existingDefinition,
                    isNew=False,
                )

            self._definitionIdByName[cleanDefinition.name] = (
                cleanDefinition.traceTypeDefinitionId
            )
            self._definitionById[
                cleanDefinition.traceTypeDefinitionId
            ] = cleanDefinition

            return TraceTypeRegistration(
                definition=cleanDefinition,
                isNew=True,
            )

    def getTraceTypeDefinitions(
        self,
    ) -> Mapping[TraceTypeDefinitionId, TraceTypeDefinition]:
        """
        Returns an immutable snapshot of active definitions.

        Returns:
            An immutable mapping from deterministic definition identities to
            the definitions registered at the time of the call.

        """
        with self._lock:
            return immutableMapping(self._definitionById)

    def getTraceTypeDefinition(
        self,
        traceTypeDefinitionId: TraceTypeDefinitionId,
    ) -> TraceTypeDefinition:
        """
        Returns one active definition by deterministic identity.

        Args:
            traceTypeDefinitionId:
                Content-derived definition identity to resolve.

        Returns:
            The active registered definition with the requested identity.

        Raises:
            TypeError:
                If traceTypeDefinitionId is not a TraceTypeDefinitionId.
            TraceTypeDefinitionNotFoundError:
                If no active definition has the requested identity.

        """
        cleanId = requireInstance(
            traceTypeDefinitionId,
            TraceTypeDefinitionId,
            "traceTypeDefinitionId",
        )

        with self._lock:
            definition = self._definitionById.get(cleanId)
            if definition is None:
                raise TraceTypeDefinitionNotFoundError(str(cleanId))

            return definition
