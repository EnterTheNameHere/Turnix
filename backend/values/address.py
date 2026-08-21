# file: backend/values/address.py ; version: 1
from __future__ import annotations

from dataclasses import dataclass

from backend.values.validation import requireValueAddress

__all__: list[str] = [
    "ValueAddress",
]


@dataclass(frozen=True, slots=True)
class ValueAddress:
    """
    Represents one canonical address in the Actant Value System.

    A ValueAddress identifies one logical value independently of how that
    value is represented, materialized, stored, transported, or accessed by a
    particular programming language.

    ValueAddress is machine-facing identity. It is deliberately less
    expressive than ordinary text so that identity remains simple,
    predictable, and portable across Actant implementations.

    Canonical addresses:
        - contain lowercase ASCII characters only;
        - contain one or more non-empty segments;
        - use "/" as the only hierarchy separator;
        - use no escaping;
        - are never normalized by ValueAddress;
        - preserve their supplied canonical text exactly.

    Each segment starts with a lowercase ASCII letter or digit. Remaining
    segment characters may be lowercase ASCII letters, digits, ".", "_", or
    "-".

    Human-facing names and content are not constrained by ValueAddress syntax.
    Values and their metadata may use Unicode or any richer representation
    supported by the relevant domain and programming language.

    ValueAddress also does not define query or selection syntax. A future
    query mechanism may use patterns, predicates, metadata, aliases, or other
    means to locate values, but the resulting canonical identities remain
    ValueAddress instances.

    The address grammar is intended to have the same semantic meaning in every
    Actant-supported language. Python-specific object behaviour is not part of
    ValueAddress identity.

    Attributes:
        value:
            Canonical address text.

    Raises:
        TypeError:
            If value is not an exact built-in string.
        ValueError:
            If value is not a canonical ValueAddress.

    """

    value: str

    def __post_init__(self) -> None:
        """Validates that value is an already-canonical ValueAddress string."""
        requireValueAddress(self.value, "value")

    def __str__(self) -> str:
        """Returns the canonical address text."""
        return self.value
