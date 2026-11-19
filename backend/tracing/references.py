# file: backend/tracing/references.py ; version: 3
from __future__ import annotations

from dataclasses import dataclass

from backend.core.ids import Uuid7Id
from backend.core.validation import requireExactNonBlankString, typeName
from backend.tracing.validation import requireName

__all__: list[str] = [
    "TraceReference",
    "TraceReferenceInput",
    "normalizeTraceReferences",
]


type TraceReferenceInput = (
    TraceReference | tuple[str, str | Uuid7Id]
)


@dataclass(frozen=True, slots=True)
class TraceReference:
    """
    Identifies one creator-asserted causal or logical relationship target.

    A reference consists of a tracing-name kind that identifies the target
    identity domain and an exact nonblank string containing the target
    identity. The identity is intentionally not restricted to UUID syntax so
    tracing can refer to other stable identity schemes.
    """

    kind: str
    id: str

    def __post_init__(self) -> None:
        """
        Validates the normalized reference fields.

        Raises:
            TypeError:
                If kind or id is not an exact built-in string.
            ValueError:
                If kind is not a valid tracing name, or id is blank or contains
                surrounding whitespace.

        """
        requireName(self.kind, "kind")
        requireExactNonBlankString(self.id, "id")

    @classmethod
    def fromIdentity(
        cls,
        kind: str,
        identity: str | Uuid7Id,
    ) -> TraceReference:
        """
        Creates a reference from a string or typed UUIDv7 identity.

        UUIDv7 identities, including domain-specific Uuid7Id subclasses, are
        converted to their canonical string representation. String identities
        are preserved exactly after validation.

        Args:
            kind:
                Tracing-name kind identifying the target identity domain.
            identity:
                Target identity as an exact nonblank string or Uuid7Id.

        Returns:
            A normalized immutable trace reference.

        Raises:
            TypeError:
                If kind or a string identity is not an exact built-in string,
                or identity is neither a string nor Uuid7Id.
            ValueError:
                If kind is not a valid tracing name or a string identity is
                blank or contains surrounding whitespace.

        """
        value = (
            str(identity)
            if isinstance(identity, Uuid7Id)
            else requireExactNonBlankString(identity, "identity")
        )
        return cls(kind=kind, id=value)


def normalizeTraceReferences(
    values: tuple[TraceReferenceInput, ...],
) -> tuple[TraceReference, ...]:
    """
    Normalizes exact-tuple reference input and removes later duplicates.

    The outer collection is intentionally strict. Higher-level tracing APIs
    promise an immutable tuple boundary and do not silently coerce lists or
    arbitrary iterables into valid evidence input.

    Args:
        values:
            Reference inputs to normalize.

    Returns:
        An immutable tuple containing unique normalized references in
        first-occurrence order.

    Raises:
        TypeError:
            If values is not an exact built-in tuple, an input is neither a
            TraceReference nor an exact two-item tuple, or tuple contents have
            unsupported types.
        ValueError:
            If a reference kind or identity violates its validation contract.

    """
    if type(values) is not tuple:
        raise TypeError(
            "values must be an exact built-in tuple; "
            f"received {typeName(values)}.",
        )

    normalized: list[TraceReference] = []
    seen: set[TraceReference] = set()

    for index, value in enumerate(values):
        if isinstance(value, TraceReference):
            reference = value
        elif type(value) is tuple and len(value) == 2:  # noqa: PLR2004
            rawId = value[1]
            if not isinstance(rawId, str | Uuid7Id):
                raise TypeError(
                    f"causedBy[{index}][1] must be a string or Uuid7Id; "
                    f"received {typeName(rawId)}.",
                )
            reference = TraceReference.fromIdentity(value[0], rawId)
        else:
            raise TypeError(
                f"causedBy[{index}] must be a TraceReference or "
                "a (kind, string-or-Uuid7Id) tuple; "
                f"received {typeName(value)}.",
            )

        if reference in seen:
            continue

        seen.add(reference)
        normalized.append(reference)

    return tuple(normalized)
