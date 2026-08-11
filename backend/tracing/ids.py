# file: backend/tracing/ids.py ; version: 2
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from backend.core.ids import Uuid7Id
from backend.core.validation import requireExactNonBlankString

__all__: list[str] = [
    "TraceEventId",
    "TraceProducerId",
    "TraceSpanId",
    "TraceTypeDefinitionId",
]


class TraceEventId(Uuid7Id):
    """Identifies one immutable trace record within Actant tracing."""

    __slots__ = ()


class TraceSpanId(Uuid7Id):
    """Identifies one logical trace span across its emitted records."""

    __slots__ = ()


class TraceProducerId(Uuid7Id):
    """
    Identifies one trace producer's sequence and monotonic-clock domain.

    Records carrying the same producer identity share the producer-local
    sequence and monotonic timestamp domain used for ordering evidence.
    """

    __slots__ = ()


_TRACE_TYPE_DEFINITION_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class TraceTypeDefinitionId:
    """
    Represents a portable content-derived trace-type definition identity.

    The canonical representation uses the ``sha256:`` prefix followed by
    exactly 64 lowercase hexadecimal characters containing the SHA-256 digest
    of the definition's canonical UTF-8 JSON representation.
    """

    value: str

    def __post_init__(self) -> None:
        """
        Validates the canonical trace-type definition identity.

        Raises:
            TypeError:
                If value is not an exact built-in string.
            ValueError:
                If value is blank, contains surrounding whitespace, or does
                not use the required lowercase ``sha256:<digest>`` syntax.

        """
        cleanValue = requireExactNonBlankString(self.value, "value")
        if _TRACE_TYPE_DEFINITION_ID_PATTERN.fullmatch(cleanValue) is None:
            raise ValueError(
                "value must use 'sha256:' followed by 64 lowercase "
                f"hexadecimal characters; received '{cleanValue}'.",
            )

    def __str__(self) -> str:
        """
        Returns the canonical deterministic identity string.

        Returns:
            The ``sha256:<digest>`` representation stored by this identity.

        """
        return self.value

    @classmethod
    def fromCanonicalJson(cls, canonicalJson: str) -> TraceTypeDefinitionId:
        """
        Creates an identity from canonical UTF-8 JSON text.

        The supplied text is encoded as UTF-8 and hashed with SHA-256. This
        method assumes the caller has already produced the required canonical
        JSON representation; it does not parse or canonicalize JSON itself.

        Args:
            canonicalJson:
                Canonical JSON text whose exact UTF-8 bytes define the
                identity.

        Returns:
            A trace-type definition identity containing the SHA-256 digest.

        Raises:
            TypeError:
                If canonicalJson is not an exact built-in string.
            ValueError:
                If canonicalJson is blank or contains surrounding whitespace.

        """
        text = requireExactNonBlankString(canonicalJson, "canonicalJson")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(f"sha256:{digest}")
