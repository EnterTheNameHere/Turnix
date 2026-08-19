# file: backend/tracing/publisher.py ; version: 4
from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from backend.core.validation import requireInstance, typeName
from backend.tracing.destinations import TraceDestination
from backend.tracing.emergency import TraceEmergencyReporter
from backend.tracing.errors import TraceDestinationContractError, TraceInvariantError
from backend.tracing.ids import TraceDestinationRegistrationId
from backend.tracing.records import TraceRecord
from backend.tracing.typeDefinitions import TraceTypeDefinition

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from backend.tracing.ids import TraceEventId, TraceTypeDefinitionId

__all__: list[str] = []  # No public API


@dataclass(slots=True)
class _TracePublicationState:
    """
    Shares active publication state across copied execution contexts.

    The mutable active flag is intentionally stored inside the ContextVar
    value. Execution contexts copied while publication is active therefore
    share this state object and observe publication becoming inactive when the
    owning publication scope exits.
    """

    active: bool = True


@dataclass(frozen=True, slots=True)
class TraceDestinationRegistrationInfo:
    """Describes one destination registration without exposing delivery state."""

    registrationId: TraceDestinationRegistrationId
    destination: TraceDestination
    destinationType: str


@dataclass(frozen=True, slots=True)
class TraceDestinationHealthTransition:
    """Describes one edge-triggered destination health transition."""

    registration: TraceDestinationRegistrationInfo
    state: str
    operation: str | None = None
    errorType: str | None = None
    errorMessage: str | None = None


@dataclass(frozen=True, slots=True)
class TraceDestinationRemovalResult:
    """Describes one removed registration and its final delivery-health state."""

    registration: TraceDestinationRegistrationInfo
    wasFailed: bool
    failureOperation: str | None = None
    failureErrorType: str | None = None
    failureErrorMessage: str | None = None


@dataclass(frozen=True, slots=True)
class _TraceDestinationAddResult:
    """Returns registration and health effects from one add operation."""

    registration: TraceDestinationRegistrationInfo
    isNew: bool
    transitions: tuple[TraceDestinationHealthTransition, ...] = ()


@dataclass(slots=True)
class _TraceDestinationEntry:
    """Owns delivery and health state for one Tracer-local registration."""

    registration: TraceDestinationRegistrationInfo
    deliveredDefinitionIds: set[TraceTypeDefinitionId] = field(
        default_factory=set,
    )
    failedDefinitionIds: set[TraceTypeDefinitionId] = field(
        default_factory=set,
    )
    recordFailureActive: bool = False
    failureEpisodeOperation: str | None = None
    failureEpisodeErrorType: str | None = None
    failureEpisodeErrorMessage: str | None = None

    def isFailed(self) -> bool:
        """Returns whether any known delivery failure remains unresolved."""
        return bool(self.failedDefinitionIds) or self.recordFailureActive


class TracePublisher:
    """
    Coordinates destination delivery for exactly one Tracer lifecycle.

    The publisher is internal Tracer-owned machinery. It owns registration
    identity, per-registration definition delivery, health episode state,
    recursive-publication protection, and destination failure isolation.

    Destination health is edge-triggered. The first unresolved delivery
    failure transitions a registration from healthy to failed. Further
    failures in the same episode are suppressed. Recovery is emitted only
    after every failed definition has later been delivered successfully and a
    failed record-delivery state, if any, has also been cleared by success.
    """

    def __init__(
        self,
        *,
        getTraceTypeDefinitions: Callable[[], Mapping[
            TraceTypeDefinitionId,
            TraceTypeDefinition,
        ]],
        destinations: Iterable[TraceDestination],
        emergencyReporter: TraceEmergencyReporter,
    ) -> None:
        """
        Initializes one internal publisher and its initial registrations.

        Initial destination failures are retained as pending health
        transitions so the owning Tracer can publish ordinary infrastructure
        evidence after construction has established its complete tracing
        machinery.

        Args:
            getTraceTypeDefinitions:
                Callable returning a snapshot of currently registered
                trace-type definitions keyed by deterministic identity.
            destinations:
                Initial destinations to register.
            emergencyReporter:
                Reporter used for destination-health and recursive-publication
                diagnostics.

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
        self._emergencyReporter = requireInstance(
            emergencyReporter,
            TraceEmergencyReporter,
            "emergencyReporter",
        )
        self._lock = threading.RLock()
        self._destinationEntries: list[_TraceDestinationEntry] = []
        self._initialAddResults: list[_TraceDestinationAddResult] = []
        self._publicationState: contextvars.ContextVar[
            _TracePublicationState | None
        ] = contextvars.ContextVar(
            f"actantTracePublicationState_{id(self)}",
            default=None,
        )

        for destination in destinations:
            result = self.addDestination(destination)
            if result.isNew:
                self._initialAddResults.append(result)

    def isPublishing(self) -> bool:
        """
        Returns whether destination publication is active in this context.

        Returns:
            True when the current execution context is inside an active
            destination-publication scope, otherwise False.

        """
        state = self._publicationState.get()
        return state is not None and state.active

    def reportRecursiveTracingAttempt(self) -> None:
        """
        Reports one recursive ordinary tracing attempt out-of-band.

        This method reports only the recursion condition. Callers remain
        responsible for raising the appropriate tracing API error.
        """
        self._emergencyReporter.reportRecursivePublication(eventId=None)

    def getDestinationCount(self) -> int:
        """
        Returns the number of active destination registrations.

        Returns:
            Number of currently active destination registrations.

        """
        with self._lock:
            return len(self._destinationEntries)

    def getRegistrations(
        self,
    ) -> tuple[TraceDestinationRegistrationInfo, ...]:
        """
        Returns a stable snapshot of current destination registrations.

        Returns:
            Registration information in current publisher order.

        """
        with self._lock:
            return tuple(
                entry.registration
                for entry in self._destinationEntries
            )

    def takeInitialAddResults(self) -> tuple[_TraceDestinationAddResult, ...]:
        """
        Returns and clears ordered initial destination-add results.

        Each result describes one unique registration created during publisher
        initialization together with any destination-health transitions caused
        while initial trace-type definitions were offered.

        Returns:
            Initial add results in registration order.

        """
        with self._lock:
            results = tuple(self._initialAddResults)
            self._initialAddResults.clear()
            return results

    def addDestination(
        self,
        destination: TraceDestination,
    ) -> _TraceDestinationAddResult:
        """
        Adds one destination and initializes definition-delivery state.

        Addition is idempotent by destination object identity. Removing and
        later re-adding the same object creates a new registration identity.

        Before the destination becomes active, every currently registered
        trace-type definition is offered to it. Failed definition writes are
        reported and remain undelivered so future publication can retry them.

        Args:
            destination:
                Destination to add.

        Returns:
            Registration result describing whether a new registration was
            created and any health transitions caused by initial definition
            delivery.

        Raises:
            TraceDestinationContractError:
                If destination does not expose callable
                writeTraceTypeDefinition() and write() operations.
            TraceInvariantError:
                If this internal operation unexpectedly enters recursive
                destination publication.

        """
        cleanDestination = _requireDestination(destination)

        with self._lock:
            for entry in self._destinationEntries:
                if entry.registration.destination is cleanDestination:
                    return _TraceDestinationAddResult(
                        registration=entry.registration,
                        isNew=False,
                    )

            registration = TraceDestinationRegistrationInfo(
                registrationId=TraceDestinationRegistrationId.new(),
                destination=cleanDestination,
                destinationType=typeName(cleanDestination),
            )
            entry = _TraceDestinationEntry(registration=registration)
            transitions: list[TraceDestinationHealthTransition] = []
            definitions = self._getTraceTypeDefinitions()

            with self._publicationGuard(eventId=None) as allowed:
                if not allowed:
                    raise TraceInvariantError(
                        "Private TracePublisher addDestination() entered "
                        "recursive publication.",
                    )

                for definition in definitions.values():
                    self._writeDefinition(entry, definition, transitions)

            self._destinationEntries.append(entry)
            return _TraceDestinationAddResult(
                registration=registration,
                isNew=True,
                transitions=tuple(transitions),
            )

    def removeDestination(
        self,
        destination: TraceDestination,
    ) -> TraceDestinationRemovalResult | None:
        """
        Removes one active destination registration by object identity.

        Publisher-level removal only mutates registration state. Enforcement
        of the Tracer invariant requiring at least one active destination
        belongs to the owning Tracer.

        Args:
            destination:
                Destination object to remove.

        Returns:
            Removal information when the exact destination object was active,
            including its final failure-episode state; otherwise None.

        Raises:
            TraceDestinationContractError:
                If destination does not expose callable
                writeTraceTypeDefinition() and write() operations.

        """
        cleanDestination = _requireDestination(destination)

        with self._lock:
            for index, entry in enumerate(self._destinationEntries):
                if entry.registration.destination is cleanDestination:
                    result = TraceDestinationRemovalResult(
                        registration=entry.registration,
                        wasFailed=entry.isFailed(),
                        failureOperation=entry.failureEpisodeOperation,
                        failureErrorType=entry.failureEpisodeErrorType,
                        failureErrorMessage=entry.failureEpisodeErrorMessage,
                    )
                    del self._destinationEntries[index]
                    return result

            return None

    def publishTraceTypeDefinition(
        self,
        definition: TraceTypeDefinition,
        *,
        excludeRegistrationIds: frozenset[
            TraceDestinationRegistrationId
        ] = frozenset(),
    ) -> tuple[TraceDestinationHealthTransition, ...]:
        """
        Publishes one registered definition and returns health transitions.

        Successful delivery is tracked independently for each destination and
        only after writeTraceTypeDefinition() returns successfully. A
        destination that raises is reported through the emergency channel
        and remains eligible for later retry.

        A pending definition may be retried by a later explicit definition
        publication or when publication of a record referencing that
        definition requires successful delivery. Definitions already
        delivered successfully to an active destination are not redelivered.

        Args:
            definition:
                Registered trace-type definition to publish.
            excludeRegistrationIds:
                Destination registrations that must not receive this
                definition during this publication.

        Returns:
            Edge-triggered destination-health transitions caused by the
            publication, in destination processing order.

        Raises:
            TypeError:
                If definition is not a TraceTypeDefinition.
            TraceInvariantError:
                If this internal operation unexpectedly becomes recursive.

        """
        cleanDefinition = requireInstance(
            definition,
            TraceTypeDefinition,
            "definition",
        )
        transitions: list[TraceDestinationHealthTransition] = []

        with self._lock, self._publicationGuard(eventId=None) as allowed:
            if not allowed:
                raise TraceInvariantError(
                    "Private TracePublisher definition publication became "
                    "recursive.",
                )

            for entry in tuple(self._destinationEntries):
                if entry.registration.registrationId in excludeRegistrationIds:
                    continue
                self._writeDefinition(entry, cleanDefinition, transitions)

        return tuple(transitions)

    def publish(
        self,
        record: TraceRecord,
        *,
        excludeRegistrationIds: frozenset[
            TraceDestinationRegistrationId
        ] = frozenset(),
    ) -> tuple[TraceDestinationHealthTransition, ...]:
        """
        Publishes one record and returns edge-triggered health transitions.

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
            excludeRegistrationIds:
                Destination registrations that must not receive this record or
                definition retries during this publication.

        Returns:
            Edge-triggered destination-health transitions caused by the
            publication, in destination processing order.

        Raises:
            TypeError:
                If record is not a TraceRecord.
            TraceInvariantError:
                If the record references a trace-type definition that is not
                present in the active registry snapshot, or this internal
                operation unexpectedly becomes recursive.

        """
        cleanRecord = requireInstance(record, TraceRecord, "record")
        transitions: list[TraceDestinationHealthTransition] = []

        with (
            self._lock,
            self._publicationGuard(eventId=cleanRecord.eventId) as allowed,
        ):
            if not allowed:
                raise TraceInvariantError(
                    "Private TracePublisher record publication became recursive.",
                )

            definitions = self._getTraceTypeDefinitions()
            definition = definitions.get(cleanRecord.traceTypeDefinitionId)
            if definition is None:
                raise TraceInvariantError(
                    "Trace record references an unregistered trace-type "
                    "definition; "
                    f"eventId={cleanRecord.eventId}, "
                    "traceTypeDefinitionId="
                    f"{cleanRecord.traceTypeDefinitionId}.",
                )

            for entry in tuple(self._destinationEntries):
                if entry.registration.registrationId in excludeRegistrationIds:
                    continue

                self._retryFailedDefinitions(entry, definitions, transitions)
                if not self._writeDefinition(entry, definition, transitions):
                    continue

                self._writeRecord(entry, cleanRecord, transitions)

        return tuple(transitions)

    def _retryFailedDefinitions(
        self,
        entry: _TraceDestinationEntry,
        definitions: Mapping[TraceTypeDefinitionId, TraceTypeDefinition],
        transitions: list[TraceDestinationHealthTransition],
    ) -> None:
        """
        Retries every failed definition still registered for one destination.

        Args:
            entry:
                Destination entry whose failed definition deliveries are
                retried.
            definitions:
                Current active trace-type definitions keyed by deterministic
                identity.
            transitions:
                Mutable transition collector receiving any failure or recovery
                edges caused by retry attempts.

        Raises:
            TraceInvariantError:
                If the destination's failed-definition state references an
                identity no longer present in the active registry.

        """
        remaining = set(entry.failedDefinitionIds)
        for definitionId, definition in definitions.items():
            if definitionId not in remaining:
                continue
            remaining.remove(definitionId)
            self._writeDefinition(entry, definition, transitions)

        if remaining:
            missing = ", ".join(
                str(definitionId)
                for definitionId in remaining
            )
            raise TraceInvariantError(
                "Destination health state references unregistered trace-type "
                f"definitions: {missing}.",
            )

    def _writeDefinition(
        self,
        entry: _TraceDestinationEntry,
        definition: TraceTypeDefinition,
        transitions: list[TraceDestinationHealthTransition],
    ) -> bool:
        """
        Ensures one definition has been delivered to one registration.

        Successful delivery is remembered by deterministic definition
        identity. Failed delivery is reported but not marked complete so a
        later publication may retry it.

        Args:
            entry:
                Destination entry whose delivery state is updated.
            definition:
                Definition to deliver.
            transitions:
                Mutable transition collector receiving any failure or recovery
                edge caused by this delivery attempt.

        Returns:
            True when the definition was already delivered or this attempt
            completed successfully. False when the destination raised.

        """
        definitionId = definition.traceTypeDefinitionId
        if definitionId in entry.deliveredDefinitionIds:
            return True

        wasFailed = entry.isFailed()

        try:
            entry.registration.destination.writeTraceTypeDefinition(definition)
        except Exception as err:  # noqa: BLE001
            entry.failedDefinitionIds.add(definitionId)
            self._recordFailureTransition(
                entry,
                wasFailed=wasFailed,
                operation="writeTraceTypeDefinition",
                err=err,
                transitions=transitions,
            )
            return False

        entry.deliveredDefinitionIds.add(definitionId)
        entry.failedDefinitionIds.discard(definitionId)
        self._recordRecoveryTransition(
            entry,
            wasFailed=wasFailed,
            transitions=transitions,
        )
        return True

    def _writeRecord(
        self,
        entry: _TraceDestinationEntry,
        record: TraceRecord,
        transitions: list[TraceDestinationHealthTransition],
    ) -> None:
        """
        Writes one record while isolating ordinary destination failures.

        Args:
            entry:
                Destination entry whose record-delivery state is updated.
            record:
                Immutable record to deliver.
            transitions:
                Mutable transition collector receiving any failure or recovery
                edge caused by the write attempt.

        """
        wasFailed = entry.isFailed()

        try:
            entry.registration.destination.write(record)
        except Exception as err:  # noqa: BLE001
            entry.recordFailureActive = True
            self._recordFailureTransition(
                entry,
                wasFailed=wasFailed,
                operation="write",
                err=err,
                transitions=transitions,
            )
            return

        entry.recordFailureActive = False
        self._recordRecoveryTransition(
            entry,
            wasFailed=wasFailed,
            transitions=transitions,
        )

    def _recordFailureTransition(
        self,
        entry: _TraceDestinationEntry,
        *,
        wasFailed: bool,
        operation: str,
        err: Exception,
        transitions: list[TraceDestinationHealthTransition],
    ) -> None:
        """
        Records only the first transition into one unresolved failure episode.

        Args:
            entry:
                Destination entry whose health state changed.
            wasFailed:
                Whether the entry was already failed before the operation.
            operation:
                Destination operation that raised.
            err:
                Exception raised by the destination.
            transitions:
                Mutable transition collector receiving the failed edge.

        """
        if wasFailed or not entry.isFailed():
            return

        entry.failureEpisodeOperation = operation
        entry.failureEpisodeErrorType = typeName(err)
        entry.failureEpisodeErrorMessage = _safeString(err)
        transition = TraceDestinationHealthTransition(
            registration=entry.registration,
            state="failed",
            operation=entry.failureEpisodeOperation,
            errorType=entry.failureEpisodeErrorType,
            errorMessage=entry.failureEpisodeErrorMessage,
        )
        transitions.append(transition)
        self._emergencyReporter.reportDestinationFailure(
            operation=operation,
            registrationId=entry.registration.registrationId,
            destination=entry.registration.destination,
            err=err,
        )

    def _recordRecoveryTransition(
        self,
        entry: _TraceDestinationEntry,
        *,
        wasFailed: bool,
        transitions: list[TraceDestinationHealthTransition],
    ) -> None:
        """
        Records recovery when every unresolved delivery failure has cleared.

        The returned transition retains the first failure details from the
        completed episode before those details are cleared from live state.

        Args:
            entry:
                Destination entry whose health may have recovered.
            wasFailed:
                Whether the entry was failed before the successful operation.
            transitions:
                Mutable transition collector receiving the recovered edge.

        """
        if not wasFailed or entry.isFailed():
            return

        transitions.append(
            TraceDestinationHealthTransition(
                registration=entry.registration,
                state="recovered",
                operation=entry.failureEpisodeOperation,
                errorType=entry.failureEpisodeErrorType,
                errorMessage=entry.failureEpisodeErrorMessage,
            ),
        )
        entry.failureEpisodeOperation = None
        entry.failureEpisodeErrorType = None
        entry.failureEpisodeErrorMessage = None
        self._emergencyReporter.reportDestinationRecovered(
            registrationId=entry.registration.registrationId,
            destination=entry.registration.destination,
        )

    class _PublicationGuard:
        """
        Guards one destination-publication scope against recursion.

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
            Ends publication and restores prior context state.

            The shared active state is cleared before ContextVar restoration so
            execution contexts copied during publication cannot remain
            permanently marked as publishing.

            Args:
                *args:
                    Standard context-manager exception information, which is
                    not inspected by the guard.

            Returns:
                False so exceptions from the guarded operation are never
                suppressed.

            """
            if self._state is not None:
                self._state.active = False

            if self._token is not None:
                self._publisher._publicationState.reset(self._token)

            return False

    def _publicationGuard(
        self,
        *,
        eventId: TraceEventId | None,
    ) -> _PublicationGuard:
        """
        Creates one publication guard bound to this publisher.

        Args:
            eventId:
                Record identifier associated with publication, when available.

        Returns:
            A guard bound to this publisher.

        """
        return self._PublicationGuard(publisher=self, eventId=eventId)


def _requireDestination(destination: object) -> TraceDestination:
    """
    Validates the runtime trace-destination structural contract.

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


def _safeString(value: object) -> str:
    """
    Returns best-effort text without propagating conversion failures.

    Args:
        value:
            Object whose string representation is requested.

    Returns:
        String representation of value, or a fixed fallback if conversion
        raises an ordinary exception.

    """
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return "<message unavailable>"
