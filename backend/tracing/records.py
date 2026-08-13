# file: backend/tracing/records.py ; version: 2
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from backend.core.ids import requireOptionalUuid7Id
from backend.core.immutableValue import ImmutableValue, ImmutableValueFreezer
from backend.core.validation import (
    requireInstance,
    requireMapping,
    requireNonNegativeInteger,
    requirePositiveInteger,
    requireString,
    typeName,
)
from backend.tracing.exceptionSnapshot import ExceptionSnapshot
from backend.tracing.ids import (
    TraceEventId,
    TraceProducerId,
    TraceSpanId,
    TraceTypeDefinitionId,
)
from backend.tracing.references import TraceReference
from backend.tracing.validation import (
    TraceLevel,
    TraceRecordKind,
    requireName,
    requireOrigin,
    requireOutcomeName,
    requireTraceLevel,
    requireTraceRecordKind,
)

if TYPE_CHECKING:
    from backend.core.ids import Uuid7Id

__all__: list[str] = [
    "TraceRecord",
]


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """
    Represents one immutable normalized piece of trace evidence.

    A trace record belongs to one producer-local sequence and carries both
    wall-clock and monotonic timestamps. Its structural fields depend on its
    record kind:

    - events may be parentless or contained within a span;
    - span-start records establish span identity, family, and ancestry;
    - span-end records terminate an established span and carry its outcome.

    Attributes are deep-frozen during construction. Causal references must
    already be normalized TraceReference values contained in an exact tuple.
    """

    eventId: TraceEventId
    traceProducerId: TraceProducerId
    sequence: int
    timestampUnixNs: int
    timestampMonotonicNs: int
    kind: TraceRecordKind
    domain: str
    type: str
    traceTypeDefinitionId: TraceTypeDefinitionId
    level: TraceLevel
    message: str = ""
    attributes: Mapping[str, ImmutableValue] = field(default_factory=dict)
    exceptionSnapshot: ExceptionSnapshot | None = None
    spanId: TraceSpanId | None = None
    spanStartEventId: TraceEventId | None = None
    parentSpanId: TraceSpanId | None = None
    spanFamily: str | None = None
    origin: str | None = None
    outcome: str | None = None
    durationNs: int | None = None
    causedBy: tuple[TraceReference, ...] = field(default_factory=tuple)
    actantRunId: Uuid7Id | None = None
    applicationId: Uuid7Id | None = None
    applicationRunId: Uuid7Id | None = None

    def __post_init__(self) -> None:
        """
        Validates and normalizes the complete trace record.

        Attribute mappings are copied into immutable deep-frozen storage.
        Causal references are validated without coercing arbitrary iterables.
        Record-kind-specific structural invariants are then enforced.

        Raises:
            TypeError:
                If an identity, record kind, level, string, mapping, exception
                snapshot, causal reference, correlation identity, or other
                field has an unsupported runtime type.
            ValueError:
                If sequence or timestamps violate numeric constraints, tracing
                names are invalid, causal references contain duplicates, or
                the selected record kind violates its structural invariants.

        """
        requireInstance(self.eventId, TraceEventId, "eventId")
        requireInstance(
            self.traceProducerId,
            TraceProducerId,
            "traceProducerId",
        )
        requirePositiveInteger(self.sequence, "sequence")
        requireNonNegativeInteger(self.timestampUnixNs, "timestampUnixNs")
        requireNonNegativeInteger(
            self.timestampMonotonicNs,
            "timestampMonotonicNs",
        )
        requireTraceRecordKind(self.kind)

        requireString(self.domain, "domain")
        if self.domain:
            requireName(self.domain, "domain")

        requireName(self.type, "type")
        requireInstance(
            self.traceTypeDefinitionId,
            TraceTypeDefinitionId,
            "traceTypeDefinitionId",
        )
        requireTraceLevel(self.level)
        requireString(self.message, "message")

        requireMapping(self.attributes, "attributes")
        frozenAttributes = ImmutableValueFreezer().freezeMapping(
            cast(Mapping[object, object], self.attributes),
            "attributes",
        )
        object.__setattr__(self, "attributes", frozenAttributes)

        if self.exceptionSnapshot is not None:
            requireInstance(
                self.exceptionSnapshot,
                ExceptionSnapshot,
                "exceptionSnapshot",
            )

        if type(self.causedBy) is not tuple:
            raise TypeError(
                "causedBy must be an exact built-in tuple; "
                f"received {typeName(self.causedBy)}.",
            )

        seenReferences: set[TraceReference] = set()
        for index, reference in enumerate(self.causedBy):
            requireInstance(reference, TraceReference, f"causedBy[{index}]")
            if reference in seenReferences:
                raise ValueError("causedBy must not contain duplicates.")
            seenReferences.add(reference)

        requireOptionalUuid7Id(self.actantRunId, "actantRunId")
        requireOptionalUuid7Id(self.applicationId, "applicationId")
        requireOptionalUuid7Id(self.applicationRunId, "applicationRunId")

        if self.kind == "event":
            self._validateEvent()
        elif self.kind == "spanStart":
            self._validateSpanStart()
        elif self.kind == "spanEnd":
            self._validateSpanEnd()
        else:
            raise ValueError(f"Unknown record kind: {self.kind}")

    def _validateEvent(self) -> None:
        """
        Validates event-specific structural fields.

        Parentless events carry an origin and no span linkage. Contained
        events carry span identity and the corresponding span-start event
        identity, and must not carry an origin.

        Raises:
            TypeError:
                If a present span identity has an unsupported runtime type or
                origin violates its exact built-in string contract.
            ValueError:
                If event-only structural invariants are violated.

        """
        if self.parentSpanId is not None:
            raise ValueError("Event record must not contain parentSpanId.")

        if self.spanFamily is not None:
            raise ValueError("Event record must not contain spanFamily.")

        if self.outcome is not None:
            raise ValueError("Event record must not contain outcome.")

        if self.durationNs is not None:
            raise ValueError("Event record must not contain durationNs.")

        if self.spanId is None:
            if self.spanStartEventId is not None:
                raise ValueError(
                    "Parentless event must not contain spanStartEventId.",
                )

            if self.origin is None:
                raise ValueError("Parentless event must contain origin.")

            requireOrigin(self.origin)
            return

        requireInstance(self.spanId, TraceSpanId, "spanId")

        if self.spanStartEventId is None:
            raise ValueError("Contained event must contain spanStartEventId.")

        requireInstance(
            self.spanStartEventId,
            TraceEventId,
            "spanStartEventId",
        )

        if self.spanStartEventId == self.eventId:
            raise ValueError(
                "Contained event must not identify itself as span start.",
            )

        if self.origin is not None:
            raise ValueError("Contained event must not contain origin.")

    def _validateSpanStart(self) -> None:
        """
        Validates span-start-specific structural fields.

        Every span start carries a span identity, identifies itself as the
        span-start event, and declares the span family. Root starts carry an
        origin; child starts instead carry a parent span identity.

        Raises:
            TypeError:
                If span identities, span-start identity, span family, or
                origin have unsupported runtime types.
            ValueError:
                If span-start structural invariants are violated.

        """
        if self.spanId is None:
            raise ValueError("Span-start record must contain spanId.")

        requireInstance(self.spanId, TraceSpanId, "spanId")

        if self.spanStartEventId is None:
            raise ValueError(
                "Span-start record must contain spanStartEventId.",
            )

        requireInstance(
            self.spanStartEventId,
            TraceEventId,
            "spanStartEventId",
        )

        if self.spanStartEventId != self.eventId:
            raise ValueError(
                "Span-start spanStartEventId must equal eventId.",
            )

        if self.spanFamily is None:
            raise ValueError("Span-start record must contain spanFamily.")

        requireName(self.spanFamily, "spanFamily")

        if self.outcome is not None:
            raise ValueError("Span-start record must not contain outcome.")

        if self.durationNs is not None:
            raise ValueError("Span-start record must not contain durationNs.")

        if self.parentSpanId is None:
            if self.origin is None:
                raise ValueError("Root span-start record must contain origin.")

            requireOrigin(self.origin)
            return

        requireInstance(self.parentSpanId, TraceSpanId, "parentSpanId")

        if self.parentSpanId == self.spanId:
            raise ValueError(
                "Span-start record must not identify itself as parent span.",
            )

        if self.origin is not None:
            raise ValueError(
                "Child span-start record must not contain origin.",
            )

    def _validateSpanEnd(self) -> None:
        """
        Validates span-end-specific structural fields.

        A span end carries the span identity and original span-start event
        identity, repeats the span family, and declares one terminal outcome.
        It does not repeat parent-span ancestry or origin information.

        Duration is optional so imported or externally generated evidence may
        terminate a span without a locally measurable elapsed duration.

        Raises:
            TypeError:
                If span identities, span family, outcome, or duration have
                unsupported runtime types.
            ValueError:
                If span-end structural invariants are violated or a present
                duration is negative.

        """
        if self.spanId is None:
            raise ValueError("Span-end record must contain spanId.")

        requireInstance(self.spanId, TraceSpanId, "spanId")

        if self.spanStartEventId is None:
            raise ValueError("Span-end record must contain spanStartEventId.")

        requireInstance(
            self.spanStartEventId,
            TraceEventId,
            "spanStartEventId",
        )

        if self.spanStartEventId == self.eventId:
            raise ValueError(
                "Span-end record must not identify itself as span start.",
            )

        if self.parentSpanId is not None:
            raise ValueError("Span-end record must not contain parentSpanId.")

        if self.spanFamily is None:
            raise ValueError("Span-end record must contain spanFamily.")

        requireName(self.spanFamily, "spanFamily")

        if self.origin is not None:
            raise ValueError("Span-end record must not contain origin.")

        if self.outcome is None:
            raise ValueError("Span-end record must contain outcome.")

        requireOutcomeName(self.outcome)

        if self.durationNs is not None:
            requireNonNegativeInteger(self.durationNs, "durationNs")
