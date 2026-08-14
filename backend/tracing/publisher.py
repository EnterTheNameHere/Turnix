# file: backend/tracing/publisher.py ; version: 3
from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from backend.core.validation import requireInstance
from backend.tracing.destinations import TraceDestination, TraceSinkDestination
from backend.tracing.emergency import TraceEmergencyReporter
from backend.tracing.errors import TraceDestinationContractError, TraceInvariantError
from backend.tracing.records import TraceRecord
from backend.tracing.typeDefinitions import TraceTypeDefinition

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from backend.tracing.ids import TraceEventId, TraceTypeDefinitionId

__all__: list[str] = [
    "TracePublisher",
]


@dataclass(slots=True)
class _TracePublicationState:
    """
    Shares publication activity across copied execution contexts.

    The mutable active flag is intentionally stored inside the ContextVar
    value. Execution contexts copied while publication is active therefore
    share this state object and observe publication becoming inactive when the
    owning publication scope exits.
    """

    active: bool = True


@dataclass(slots=True)
class _TraceDestinationEntry:
    """
    Stores one destination and its successful definition-delivery state.

    A definition identity is added to deliveredDefinitionIds only after the
    destination returns successfully from writeTraceTypeDefinition().
    """

    destination: TraceDestination
    deliveredDefinitionIds: set[TraceTypeDefinitionId]


class TracePublisher:
    """
    Publishes trace definitions and records without retaining record history.

    Publication is serialized across threads so destinations observe one
    consistent definition-and-record delivery order. Destination failures are
    isolated and reported through TraceEmergencyReporter rather than being
    propagated through ordinary tracing.

    Definition-delivery state is tracked independently for each destination and
    only after successful delivery. Failed definition delivery remains pending
    and may be retried by later definition publication or when a record
    referencing that definition is published.

    Before a record is written to a destination, the publisher ensures that the
    record's registered trace-type definition has been delivered successfully
    to that destination. A record is withheld from a destination while its
    required definition remains undelivered.

    Recursive ordinary tracing from destination delivery is prohibited.
    Publication activity is tracked with context-local state that remains
    correct across copied asyncio execution contexts.
    """

    def __init__(
        self,
        *,
        getTraceTypeDefinitions: Callable[[], Mapping[
            TraceTypeDefinitionId,
            TraceTypeDefinition,
        ]],
        destinations: Iterable[TraceDestination] = (),
        emergencyReporter: TraceEmergencyReporter | None = None,
    ) -> None:
        """
        Initializes a trace publisher.

        Initial destinations are activated one at a time. Each is immediately
        offered every trace-type definition registered at the time it is added.
        Definition delivery is tracked per destination only after successful
        writeTraceTypeDefinition() completion; failed initial deliveries remain
        pending for later retry.

        Args:
            getTraceTypeDefinitions:
                Callable returning a snapshot of currently registered
                trace-type definitions keyed by deterministic identity.
            destinations:
                Initial destinations to activate.
            emergencyReporter:
                Reporter used for destination and recursive-publication
                failures. When omitted, a default reporter is created.

        Raises:
            TypeError:
                If getTraceTypeDefinitions is not callable or
                emergencyReporter has an unsupported runtime type.
            TraceDestinationContractError:
                If an initial destination does not expose callable
                writeTraceTypeDefinition() and write() operations.

        """
        if not callable(getTraceTypeDefinitions):
            raise TypeError("getTraceTypeDefinitions must be callable.")

        self._getTraceTypeDefinitions = getTraceTypeDefinitions
        self._emergencyReporter = (
            TraceEmergencyReporter()
            if emergencyReporter is None
            else requireInstance(
                emergencyReporter,
                TraceEmergencyReporter,
                "emergencyReporter",
            )
        )
        self._lock = threading.RLock()
        self._destinationEntries: list[_TraceDestinationEntry] = []
        self._publicationState: contextvars.ContextVar[
            _TracePublicationState | None
        ] = contextvars.ContextVar(
            f"actantTracePublicationState_{id(self)}",
            default=None,
        )

        for destination in destinations:
            self.addDestination(destination)

        if not self._destinationEntries:
            self._destinationEntries.append(
                _TraceDestinationEntry(
                    destination=TraceSinkDestination(),
                    deliveredDefinitionIds=set(),
                ),
            )

    def isPublishing(self) -> bool:
        """
        Returns whether destination delivery is active in this context.

        Returns:
            True when the current execution context is inside an active
            destination-publication scope, otherwise False.

        """
        state = self._publicationState.get()
        return state is not None and state.active

    def reportRecursiveTracingAttempt(self) -> None:
        """
        Reports a recursive tracing attempt through the emergency channel.

        This method reports only the recursion condition. Callers remain
        responsible for raising the appropriate tracing API error.
        """
        self._emergencyReporter.reportRecursivePublication(eventId=None)

    def addDestination(self, destination: TraceDestination) -> None:
        """
        Adds one destination after attempting current definition delivery.

        Addition is idempotent by destination object identity. Before the
        destination becomes active, every currently registered trace-type
        definition is offered to it. Failed definition writes are reported and
        remain undelivered so future record publication can retry them.

        The implicit sink destination is removed when the first real
        destination is added.

        Args:
            destination:
                Destination to add.

        Raises:
            TraceDestinationContractError:
                If destination does not expose callable
                writeTraceTypeDefinition() and write() operations.

        """
        cleanDestination = _requireDestination(destination)

        with self._lock:
            if any(
                entry.destination is cleanDestination
                for entry in self._destinationEntries
            ):
                return

            entry = _TraceDestinationEntry(
                destination=cleanDestination,
                deliveredDefinitionIds=set(),
            )
            definitions = self._getTraceTypeDefinitions()

            with self._publicationGuard(eventId=None) as allowed:
                if not allowed:
                    return

                for definition in definitions.values():
                    self._writeDefinition(entry, definition)

            if (
                len(self._destinationEntries) == 1
                and isinstance(
                    self._destinationEntries[0].destination,
                    TraceSinkDestination,
                )
            ):
                self._destinationEntries.clear()

            self._destinationEntries.append(entry)

    def removeDestination(self, destination: TraceDestination) -> bool:
        """
        Removes one active destination by object identity.

        When removal leaves no active destination, an implicit
        TraceSinkDestination is installed so the publisher always retains a
        valid delivery target.

        Args:
            destination:
                Destination object to remove.

        Returns:
            True when the exact destination object was active and removed,
            otherwise False.

        Raises:
            TraceDestinationContractError:
                If destination does not expose callable
                writeTraceTypeDefinition() and write() operations.

        """
        cleanDestination = _requireDestination(destination)

        with self._lock:
            for index, entry in enumerate(self._destinationEntries):
                if entry.destination is cleanDestination:
                    del self._destinationEntries[index]

                    if not self._destinationEntries:
                        self._destinationEntries.append(
                            _TraceDestinationEntry(
                                destination=TraceSinkDestination(),
                                deliveredDefinitionIds=set(),
                            ),
                        )

                    return True

            return False

    def getDestinations(self) -> tuple[TraceDestination, ...]:
        """
        Returns a stable snapshot of active destinations.

        Returns:
            A tuple containing destination objects in current publication
            order.

        """
        with self._lock:
            return tuple(
                entry.destination
                for entry in self._destinationEntries
            )

    def publishTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
    ) -> None:
        """
        Publishes one registered definition to every active destination.

        Successful delivery is tracked independently for each destination and
        only after writeTraceTypeDefinition() returns successfully. A
        destination that raises is reported through the emergency channel
        and remains eligible for later retry.

        A pending definition may be retried by a later explicit definition
        publication or when publication of a record referencing that definition
        requires successful delivery. Definitions already delivered
        successfully to an active destination are not redelivered.

        Args:
            definition:
                Registered trace-type definition to publish.

        Raises:
            TypeError:
                If definition is not a TraceTypeDefinition.

        """
        cleanDefinition = requireInstance(
            definition,
            TraceTypeDefinition,
            "definition",
        )

        with self._lock, self._publicationGuard(eventId=None) as allowed:
            if not allowed:
                return

            for entry in tuple(self._destinationEntries):
                self._writeDefinition(entry, cleanDefinition)

    def publish(self, record: TraceRecord) -> None:
        """
        Publishes one immutable record to every active destination.

        The record's trace-type definition must still be present in the active
        registry. For each destination, the publisher ensures that definition
        has been delivered successfully before writing the record. If an
        earlier definition-delivery attempt failed, delivery is retried as part
        of record publication.

        The record is written to a destination only after its referenced
        definition has been delivered successfully. If definition delivery
        still fails, the record is withheld from that destination while
        publication continues independently to other destinations.

        Args:
            record:
                Immutable trace record to publish.

        Raises:
            TypeError:
                If record is not a TraceRecord.
            TraceInvariantError:
                If the record references a trace-type definition that is not
                present in the active registry snapshot.

        """
        cleanRecord = requireInstance(record, TraceRecord, "record")

        with self._lock, self._publicationGuard(eventId=cleanRecord.eventId) as allowed:
            if not allowed:
                return

            definitions = self._getTraceTypeDefinitions()
            definition = definitions.get(
                cleanRecord.traceTypeDefinitionId,
            )
            if definition is None:
                raise TraceInvariantError(
                    "Trace record references an unregistered trace-type "
                    "definition; "
                    f"eventId={cleanRecord.eventId}, "
                    "traceTypeDefinitionId="
                    f"{cleanRecord.traceTypeDefinitionId}.",
                )

            for entry in tuple(self._destinationEntries):
                if not self._writeDefinition(entry, definition):
                    continue

                self._writeRecord(entry.destination, cleanRecord)

    def _writeDefinition(
        self,
        entry: _TraceDestinationEntry,
        definition: TraceTypeDefinition,
    ) -> bool:
        """
        Ensures one definition has been delivered to one destination.

        Successful delivery is remembered by deterministic definition
        identity. Failed delivery is reported but not marked complete so a
        later publication may retry it.

        Args:
            entry:
                Destination entry whose delivery state is updated.
            definition:
                Definition to deliver.

        Returns:
            True when the definition was already delivered or this attempt
            completed successfully. False when the destination raised.

        """
        definitionId = definition.traceTypeDefinitionId
        if definitionId in entry.deliveredDefinitionIds:
            return True

        try:
            entry.destination.writeTraceTypeDefinition(definition)
        except Exception as err:  # noqa: BLE001
            self._emergencyReporter.reportDestinationFailure(
                operation="writeTraceTypeDefinition",
                destination=entry.destination,
                err=err,
            )
            return False

        entry.deliveredDefinitionIds.add(definitionId)
        return True

    def _writeRecord(
        self,
        destination: TraceDestination,
        record: TraceRecord,
    ) -> None:
        """
        Writes one record while isolating destination failures.

        Args:
            destination:
                Destination receiving the record.
            record:
                Immutable record to deliver.

        """
        try:
            destination.write(record)
        except Exception as err:  # noqa: BLE001
            self._emergencyReporter.reportDestinationFailure(
                operation="write",
                destination=destination,
                err=err,
            )

    class _PublicationGuard:
        """
        Guards one destination-publication scope against recursive tracing.

        The guard stores a mutable publication-state object in a ContextVar.
        Copies of the current execution context therefore share the active
        state object and observe it becoming inactive when this scope exits.
        """

        def __init__(
            self,
            *,
            publisher: TracePublisher,
            eventId: TraceEventId | None,
        ) -> None:
            """
            Initializes a publication guard.

            Args:
                publisher:
                    Publisher whose publication state is guarded.
                eventId:
                    Record identifier associated with the attempted
                    publication, when one exists.

            """
            self._publisher = publisher
            self._eventId = eventId
            self._state: _TracePublicationState | None = None
            self._token: contextvars.Token[
                _TracePublicationState | None
            ] | None = None

        def __enter__(self) -> bool:
            """
            Attempts to enter destination publication.

            Returns:
                True when publication may proceed. False when recursive
                publication is detected and reported.

            """
            current = self._publisher._publicationState.get()
            if current is not None and current.active:
                self._publisher._emergencyReporter.reportRecursivePublication(
                    eventId=self._eventId,
                )
                return False

            state = _TracePublicationState()
            self._state = state
            self._token = self._publisher._publicationState.set(state)
            return True

        def __exit__(self, *args: object) -> bool:
            """
            Ends destination publication and restores prior context state.

            Args:
                *args:
                    Standard context-manager exception information, which is
                    not inspected by the guard.

            Returns:
                False so exceptions from the guarded operation are never
                suppressed.

            """
            state = self._state
            token = self._token

            if state is not None:
                state.active = False

            if token is not None:
                self._publisher._publicationState.reset(token)

            return False

    def _publicationGuard(
        self,
        *,
        eventId: TraceEventId | None,
    ) -> _PublicationGuard:
        """
        Creates one publication guard.

        Args:
            eventId:
                Record identifier associated with publication, when available.

        Returns:
            A guard bound to this publisher.

        """
        return self._PublicationGuard(publisher=self, eventId=eventId)


def _requireDestination(destination: object) -> TraceDestination:
    """
    Validates the runtime trace-destination contract.

    Runtime validation intentionally checks only that the two required
    operations exist and are callable. Signature introspection is avoided so
    wrapped, dynamically implemented, and extension-backed destinations remain
    compatible with the structural destination protocol.

    Args:
        destination:
            Object to validate as a trace destination.

    Returns:
        The validated object cast to TraceDestination.

    Raises:
        TraceDestinationContractError:
            If destination does not expose callable
            writeTraceTypeDefinition() and write() operations.

    """
    writeDefinition = getattr(destination, "writeTraceTypeDefinition", None)
    writeRecord = getattr(destination, "write", None)

    if not callable(writeDefinition) or not callable(writeRecord):
        raise TraceDestinationContractError(
            "Trace destination must expose callable "
            "writeTraceTypeDefinition(definition) and write(record).",
        )

    return cast(TraceDestination, destination)
