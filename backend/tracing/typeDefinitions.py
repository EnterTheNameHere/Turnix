# file: backend/tracing/typeDefinitions.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from backend.core.collections import immutableMapping
from backend.core.validation import (
    requireExactNonBlankString,
    requireInstance,
    requireInteger,
    requireMapping,
    requireString,
)
from backend.tracing.canonical import canonicalJson
from backend.tracing.ids import TraceTypeDefinitionId
from backend.tracing.validation import (
    TraceLevel,
    requireDisplayName,
    requireName,
    requireOutcomeName,
    requireTraceLevel,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__: list[str] = [
    "DEFAULT_CANCELLED",
    "DEFAULT_COMPLETED",
    "DEFAULT_ERRORED",
    "DEFAULT_EVENT",
    "DEFAULT_FAILED",
    "DEFAULT_STARTED",
    "STANDARD_TRACE_SPAN_OUTCOMES",
    "TRACE_EVENT",
    "TRACE_SPAN",
    "TRACE_TYPE_DEFINITION_SCHEMA_VERSION",
    "TraceEventType",
    "TraceGeneratedType",
    "TraceSpanType",
    "TraceTypeDefinition",
    "TraceTypeDefinitionKind",
]


type TraceTypeDefinitionKind = Literal["event", "span"]

TRACE_TYPE_DEFINITION_SCHEMA_VERSION: Final[int] = 1

STANDARD_TRACE_SPAN_OUTCOMES: Final[tuple[str, ...]] = (
    "completed",
    "failed",
    "errored",
    "cancelled",
)


@dataclass(frozen=True, slots=True)
class TraceGeneratedType:
    """
    Defines metadata for one generated trace-record type.

    A generated type supplies the record label appended to its trace-type
    family where applicable, the default presentation level, and optional
    human-readable display text.

    Generic generated-type labels use normal tracing-name syntax. Span
    definitions impose the stronger single-segment outcome-label constraint
    when these values are used for span starts or outcomes.
    """

    label: str
    level: TraceLevel
    displayName: str = ""

    def __post_init__(self) -> None:
        """
        Validates generated trace-record metadata.

        Raises:
            TypeError:
                If label, level, or displayName violates its exact built-in
                string contract.
            ValueError:
                If label is not a valid tracing name, level is not a canonical
                trace level, or any validated string violates its whitespace
                contract.

        """
        requireName(self.label, "label")
        requireTraceLevel(self.level)
        requireDisplayName(self.displayName)

    def toCanonicalData(self) -> dict[str, str]:
        """
        Returns the portable canonical-data representation.

        Returns:
            A newly allocated mapping containing exactly the generated type's
            display name, label, and default trace level.

        """
        return {
            "displayName": self.displayName,
            "label": self.label,
            "level": self.level,
        }


DEFAULT_EVENT: Final[TraceGeneratedType] = TraceGeneratedType(
    label="trace.event",
    level="info",
    displayName="Event",
)

DEFAULT_STARTED: Final[TraceGeneratedType] = TraceGeneratedType(
    label="started",
    level="debug",
    displayName="Started",
)

DEFAULT_COMPLETED: Final[TraceGeneratedType] = TraceGeneratedType(
    label="completed",
    level="info",
    displayName="Completed",
)

DEFAULT_FAILED: Final[TraceGeneratedType] = TraceGeneratedType(
    label="failed",
    level="warning",
    displayName="Failed",
)

DEFAULT_ERRORED: Final[TraceGeneratedType] = TraceGeneratedType(
    label="errored",
    level="error",
    displayName="Errored",
)

DEFAULT_CANCELLED: Final[TraceGeneratedType] = TraceGeneratedType(
    label="cancelled",
    level="info",
    displayName="Cancelled",
)


@dataclass(frozen=True, slots=True)
class TraceTypeDefinition:
    """
    Represents one normalized portable trace-type definition.

    Definitions are immutable and content-addressed. Their
    TraceTypeDefinitionId must equal the SHA-256 identity derived from the
    definition's complete canonical JSON representation.

    Event definitions contain exactly one event generated type whose label
    equals the trace-type name and contain no span metadata.

    Span definitions contain start metadata and at least all standard span
    outcomes. Span start and outcome labels are single undotted name segments
    and must be unique across the complete span family.
    """

    traceTypeDefinitionId: TraceTypeDefinitionId
    definitionKind: TraceTypeDefinitionKind
    name: str
    domain: str
    event: TraceGeneratedType | None
    started: TraceGeneratedType | None
    outcomes: Mapping[str, TraceGeneratedType]
    schemaVersion: int = TRACE_TYPE_DEFINITION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """
        Validates and normalizes the complete portable definition.

        Outcome mappings are copied into immutable snapshot storage before the
        canonical identity is verified.

        Raises:
            TypeError:
                If any field has an unsupported runtime type or violates an
                exact built-in string contract.
            ValueError:
                If the definition kind, name, domain, schema version, event or
                span structure, generated labels, required span outcomes, or
                supplied content-derived identity violates the definition
                contract.

        """
        requireInstance(
            self.traceTypeDefinitionId,
            TraceTypeDefinitionId,
            "traceTypeDefinitionId",
        )

        cleanDefinitionKind = requireExactNonBlankString(
            self.definitionKind,
            "definitionKind",
        )
        if cleanDefinitionKind not in ("event", "span"):
            raise ValueError(
                "definitionKind must be 'event' or 'span'.",
            )

        requireName(self.name, "name")
        _requireDomain(self.domain)

        requireInteger(self.schemaVersion, "schemaVersion")
        if self.schemaVersion != TRACE_TYPE_DEFINITION_SCHEMA_VERSION:
            raise ValueError(
                "schemaVersion is not supported by this implementation.",
            )

        requireMapping(self.outcomes, "outcomes")

        normalizedOutcomes: dict[str, TraceGeneratedType] = {}
        for rawOutcome, rawGenerated in self.outcomes.items():
            outcome = requireOutcomeName(rawOutcome, "outcomes key")
            generated = requireInstance(
                rawGenerated,
                TraceGeneratedType,
                f"outcomes[{outcome!r}]",
            )
            normalizedOutcomes[outcome] = generated

        object.__setattr__(
            self,
            "outcomes",
            immutableMapping(normalizedOutcomes),
        )

        if cleanDefinitionKind == "event":
            if self.event is None:
                raise ValueError(
                    "Event definition must contain event metadata.",
                )

            event = requireInstance(self.event, TraceGeneratedType, "event")
            if event.label != self.name:
                raise ValueError(
                    "Event definition label must equal its trace-type name.",
                )

            if self.started is not None:
                raise ValueError(
                    "Event definition must not contain started metadata.",
                )

            if self.outcomes:
                raise ValueError("Event definition must not contain outcomes.")

        else:
            if self.event is not None:
                raise ValueError(
                    "Span definition must not contain event metadata.",
                )

            if self.started is None:
                raise ValueError(
                    "Span definition must contain started metadata.",
                )

            started = requireInstance(
                self.started,
                TraceGeneratedType,
                "started",
            )
            requireOutcomeName(started.label, "started label")

            for outcome, generated in self.outcomes.items():
                requireOutcomeName(
                    generated.label,
                    f"outcomes[{outcome!r}] label",
                )

            missingOutcomes = tuple(
                outcome
                for outcome in STANDARD_TRACE_SPAN_OUTCOMES
                if outcome not in self.outcomes
            )
            if missingOutcomes:
                raise ValueError(
                    "Span definition is missing standard outcomes: "
                    + ", ".join(missingOutcomes)
                    + ".",
                )

            generatedLabels = [started.label]
            generatedLabels.extend(
                generated.label for generated in self.outcomes.values()
            )
            if len(generatedLabels) != len(set(generatedLabels)):
                raise ValueError(
                    "Span definition generated labels must be unique across "
                    "start and all outcomes.",
                )

        expectedId = TraceTypeDefinitionId.fromCanonicalJson(
            canonicalJson(self.toCanonicalData()),
        )
        if self.traceTypeDefinitionId != expectedId:
            raise ValueError(
                "TraceTypeDefinitionId does not "
                "match the canonical definition.",
            )

    def toCanonicalData(self) -> dict[str, object]:
        """
        Returns the complete portable definition used for identity.

        Returns:
            A newly allocated canonical-data tree containing every field that
            participates in the trace-type definition identity.

        """
        return {
            "definitionKind": self.definitionKind,
            "domain": self.domain,
            "event": (
                None if self.event is None else self.event.toCanonicalData()
            ),
            "name": self.name,
            "outcomes": {
                outcome: generated.toCanonicalData()
                for outcome, generated in self.outcomes.items()
            },
            "schemaVersion": self.schemaVersion,
            "started": (
                None
                if self.started is None
                else self.started.toCanonicalData()
            ),
        }

    def toCanonicalJson(self) -> str:
        """
        Returns the canonical JSON representation used for identity.

        Returns:
            Deterministic canonical JSON text representing this complete
            trace-type definition.

        """
        return canonicalJson(self.toCanonicalData())

    def getGeneratedTypeForRecord(
        self,
        *,
        recordType: str,
        outcome: str | None = None,
    ) -> TraceGeneratedType | None:
        """
        Resolves generated metadata for one emitted record.

        For ordinary declared event and span definitions, recordType must match
        the concrete generated record type.

        The built-in ``trace.event`` and ``trace.span`` definitions also act
        as presentation defaults for record-local labels and outcomes that do
        not create additional portable type definitions.

        Args:
            recordType:
                Concrete emitted tracing record type.
            outcome:
                Span outcome associated with a terminal record when known.

        Returns:
            Matching generated metadata, or ``None`` when this definition does
            not describe the supplied record.

        Raises:
            TypeError:
                If recordType or a supplied outcome is not an exact built-in
                string.
            ValueError:
                If recordType is not a valid tracing name or a supplied
                outcome is not a valid single-segment outcome name.

        """
        cleanRecordType = requireName(recordType, "recordType")

        if self.definitionKind == "event":
            if self.event is None:
                return None

            if (
                self.event.label == cleanRecordType
                or self.name == "trace.event"
            ):
                return self.event

            return None

        if cleanRecordType == self.getStartedRecordType():
            return self.started

        if outcome is not None:
            cleanOutcome = requireOutcomeName(outcome)
            generated = self.outcomes.get(cleanOutcome)
            if generated is None:
                return None

            if (
                cleanRecordType == self.getOutcomeRecordType(cleanOutcome)
                or self.name == "trace.span"
            ):
                return generated

            return None

        for generated in self.outcomes.values():
            if cleanRecordType == f"{self.name}.{generated.label}":
                return generated

        return None

    def getStartedRecordType(self) -> str:
        """
        Returns the concrete record type generated for span start.

        Returns:
            The trace-type name joined with the configured start label.

        Raises:
            ValueError:
                If this is an event definition.

        """
        if self.started is None:
            raise ValueError("Event definitions do not generate span starts.")

        return f"{self.name}.{self.started.label}"

    def getOutcomeRecordType(self, outcome: str) -> str:
        """
        Returns the concrete record type generated for one span outcome.

        Args:
            outcome:
                Declared span outcome whose generated record type is requested.

        Returns:
            The trace-type name joined with the generated label assigned to the
            requested outcome.

        Raises:
            TypeError:
                If outcome is not an exact built-in string.
            ValueError:
                If this is an event definition or outcome does not satisfy the
                tracing-outcome syntax.
            KeyError:
                If the span definition does not declare outcome.

        """
        if self.definitionKind == "event":
            raise ValueError(
                "Event definitions do not generate span outcomes.",
            )

        cleanOutcome = requireOutcomeName(outcome)
        generated = self.outcomes.get(cleanOutcome)
        if generated is None:
            raise KeyError(cleanOutcome)

        return f"{self.name}.{generated.label}"


def _buildDefinition(
    *,
    definitionKind: TraceTypeDefinitionKind,
    name: str,
    domain: str,
    event: TraceGeneratedType | None,
    started: TraceGeneratedType | None,
    outcomes: Mapping[str, TraceGeneratedType],
) -> TraceTypeDefinition:
    """
    Builds and verifies one content-addressed trace-type definition.

    The candidate canonical data is hashed before construction. The resulting
    TraceTypeDefinition independently canonicalizes its normalized state and
    verifies the supplied identity, detecting drift between builder and
    definition serialization semantics.
    """
    canonicalData: dict[str, object] = {
        "definitionKind": definitionKind,
        "domain": domain,
        "event": None if event is None else event.toCanonicalData(),
        "name": name,
        "outcomes": {
            outcome: generated.toCanonicalData()
            for outcome, generated in outcomes.items()
        },
        "started": None if started is None else started.toCanonicalData(),
        "schemaVersion": TRACE_TYPE_DEFINITION_SCHEMA_VERSION,
    }

    definitionId = TraceTypeDefinitionId.fromCanonicalJson(
        canonicalJson(canonicalData),
    )

    return TraceTypeDefinition(
        traceTypeDefinitionId=definitionId,
        definitionKind=definitionKind,
        name=name,
        domain=domain,
        event=event,
        started=started,
        outcomes=outcomes,
    )


@dataclass(frozen=True, slots=True)
class TraceEventType:
    """
    Defines one reusable ordinary trace-event vocabulary entry.

    Event types produce exactly one generated record type. Unless explicit
    generated metadata is supplied, the event name itself becomes the record
    label and the default level is ``info``.

    The empty domain selects the default tracing domain and is part of the
    portable definition identity.
    """

    name: str
    domain: str = ""
    event: TraceGeneratedType | None = None
    _definition: TraceTypeDefinition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """
        Validates the event type and materializes its portable definition.

        When event metadata is omitted, equivalent metadata is generated using
        the trace-type name as its label and ``info`` as its level.

        Raises:
            TypeError:
                If name, domain, or event has an unsupported runtime type.
            ValueError:
                If name or domain violates tracing-name syntax, or explicit
                event metadata uses a label different from name.

        """
        cleanName = requireName(self.name, "name")
        cleanDomain = _requireDomain(self.domain)

        generated = self.event
        if generated is None:
            generated = TraceGeneratedType(label=cleanName, level="info")
        else:
            requireInstance(generated, TraceGeneratedType, "event")
            if generated.label != cleanName:
                raise ValueError(
                    "TraceEventType event label must equal name.",
                )

        object.__setattr__(self, "event", generated)
        object.__setattr__(
            self,
            "_definition",
            _buildDefinition(
                definitionKind="event",
                name=cleanName,
                domain=cleanDomain,
                event=generated,
                started=None,
                outcomes=immutableMapping({}),
            ),
        )

    def getDefinition(self) -> TraceTypeDefinition:
        """
        Returns the normalized portable trace-type definition.

        Returns:
            The immutable content-addressed definition materialized when this
            event type was constructed.

        """
        return self._definition


@dataclass(frozen=True, slots=True)
class TraceSpanType:
    """
    Defines one reusable span family and terminal-outcome vocabulary.

    Every span family contains one start generated type and the four standard
    terminal outcomes: completed, failed, errored, and cancelled. Additional
    custom outcomes may be declared without replacing standard outcomes.

    Span-generated labels are single undotted tracing-name segments and must
    be unique across the start record and every terminal outcome.

    The empty domain selects the default tracing domain and is part of the
    portable definition identity.
    """

    name: str
    domain: str = ""
    started: TraceGeneratedType = DEFAULT_STARTED
    completed: TraceGeneratedType = DEFAULT_COMPLETED
    failed: TraceGeneratedType = DEFAULT_FAILED
    errored: TraceGeneratedType = DEFAULT_ERRORED
    cancelled: TraceGeneratedType = DEFAULT_CANCELLED
    customOutcomes: Mapping[str, TraceGeneratedType] = field(
        default_factory=dict,
    )
    _definition: TraceTypeDefinition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """
        Validates the span family and materializes its portable definition.

        The supplied custom-outcome mapping is copied into immutable snapshot
        storage. Standard outcomes cannot be replaced through customOutcomes;
        callers customize them through their dedicated fields.

        Raises:
            TypeError:
                If any supplied field has an unsupported runtime type.
            ValueError:
                If name or domain violates tracing-name syntax, generated span
                labels are invalid, a custom outcome attempts to replace a
                standard outcome, generated labels are not unique, or another
                span-definition invariant is violated.

        """
        cleanName = requireName(self.name, "name")
        cleanDomain = _requireDomain(self.domain)

        started = _requireSpanGeneratedType(self.started, "started")
        completed = _requireSpanGeneratedType(self.completed, "completed")
        failed = _requireSpanGeneratedType(self.failed, "failed")
        errored = _requireSpanGeneratedType(self.errored, "errored")
        cancelled = _requireSpanGeneratedType(self.cancelled, "cancelled")

        requireMapping(self.customOutcomes, "customOutcomes")

        customOutcomes: dict[str, TraceGeneratedType] = {}
        for rawOutcome, rawGenerated in self.customOutcomes.items():
            outcome = requireOutcomeName(rawOutcome, "customOutcomes key")
            if outcome in STANDARD_TRACE_SPAN_OUTCOMES:
                raise ValueError(
                    "customOutcomes must not replace standard outcome "
                    f"{outcome!r}.",
                )

            customOutcomes[outcome] = _requireSpanGeneratedType(
                rawGenerated,
                f"customOutcomes[{outcome!r}]",
            )

        normalizedCustomOutcomes = immutableMapping(customOutcomes)
        object.__setattr__(self, "customOutcomes", normalizedCustomOutcomes)

        outcomes: dict[str, TraceGeneratedType] = {
            "cancelled": cancelled,
            "completed": completed,
            "errored": errored,
            "failed": failed,
        }
        outcomes.update(customOutcomes)

        object.__setattr__(
            self,
            "_definition",
            _buildDefinition(
                definitionKind="span",
                name=cleanName,
                domain=cleanDomain,
                event=None,
                started=started,
                outcomes=immutableMapping(outcomes),
            ),
        )

    def getDefinition(self) -> TraceTypeDefinition:
        """
        Returns the normalized portable trace-type definition.

        Returns:
            The immutable content-addressed definition materialized when this
            span type was constructed.

        """
        return self._definition

    def getOutcome(self, outcome: str) -> TraceGeneratedType | None:
        """
        Returns generated metadata for a declared span outcome.

        Args:
            outcome:
                Standard or custom outcome name to resolve.

        Returns:
            The generated metadata for the declared outcome, or ``None`` when
            this span family does not declare it.

        Raises:
            TypeError:
                If outcome is not an exact built-in string.
            ValueError:
                If outcome is not a valid single-segment outcome name.

        """
        cleanOutcome = requireOutcomeName(outcome)
        return self._definition.outcomes.get(cleanOutcome)


def _requireDomain(
    value: str,
    name: str = "domain",
) -> str:
    """
    Validates an optional tracing domain name.

    The empty exact built-in string represents the default tracing domain.
    Non-empty domains use ordinary lowercase dotted tracing-name syntax.
    """
    cleanValue = requireString(value, name)

    if cleanValue:
        return requireName(cleanValue, name)

    return cleanValue


def _requireSpanGeneratedType(
    value: TraceGeneratedType,
    name: str,
) -> TraceGeneratedType:
    """
    Validates generated metadata used by a span family.

    Span-generated labels are restricted to one undotted outcome-name segment
    because the enclosing span type supplies the dotted record-type prefix.
    """
    generated = requireInstance(value, TraceGeneratedType, name)
    requireOutcomeName(generated.label, f"{name} label")
    return generated


TRACE_EVENT: Final[TraceEventType] = TraceEventType(
    name="trace.event",
    domain="",
    event=DEFAULT_EVENT,
)
TRACE_SPAN: Final[TraceSpanType] = TraceSpanType(
    name="trace.span",
    domain="",
)
