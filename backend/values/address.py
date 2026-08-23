# file: backend/values/address.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass

from backend.core.validation import typeName
from backend.values.validation import (
    requireRelativeValueAddress,
    requireValueAddress,
)

__all__: list[str] = [
    "RelativeValueAddress",
    "ValueAddress",
]


@dataclass(frozen=True, slots=True)
class RelativeValueAddress:
    """
    Represents one canonical address relative to a Value System base address.

    A RelativeValueAddress contains reusable address structure but does not
    identify a complete logical Value by itself. It acquires complete identity
    only when resolved against a ValueAddress.

    RelativeValueAddress uses the same canonical textual grammar as
    ValueAddress:
        - lowercase ASCII characters only;
        - one or more non-empty segments;
        - "/" as the only hierarchy separator;
        - no escaping;
        - no normalization.

    The term "relative" does not imply filesystem semantics. Relative value
    addresses do not support parent traversal, current-directory components,
    separator normalization, or other filesystem-style resolution rules. "."
    and ".." are not traversal components and are invalid as segments under the
    canonical address grammar.

    For example:

        base:
            npcs/alice

        relative:
            inventory/armor

        resolved:
            npcs/alice/inventory/armor

    The same RelativeValueAddress may be resolved against different base
    addresses:

        npcs/alice + inventory/armor = npcs/alice/inventory/armor
        npcs/ben   + inventory/armor = npcs/ben/inventory/armor

    producing two distinct complete ValueAddress identities.

    RelativeValueAddress describes Value System address hierarchy only. It
    does not describe structure inside a materialized value. For example, a
    relative address "inventory/armor" does not mean dictionary access into a
    value stored at "inventory".

    Attributes:
        value:
            Canonical relative address text.

    Raises:
        TypeError:
            If value is not an exact built-in string.
        ValueError:
            If value is not canonical relative ValueAddress syntax.

    """

    value: str

    def __post_init__(self) -> None:
        """Validates that value is an already-canonical relative address."""
        requireRelativeValueAddress(self.value, "value")

    def __str__(self) -> str:
        """Returns the canonical relative address text."""
        return self.value


@dataclass(frozen=True, slots=True)
class ValueAddress:
    """
    Represents one canonical absolute address in the Actant Value System.

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

    RelativeValueAddress represents reusable address structure requiring a
    base ValueAddress. Resolving a relative address against this address
    appends its segments and produces another complete ValueAddress.

    Address hierarchy does not imply stored-container hierarchy. A Value may
    exist at "npcs/alice/inventory" even when no Value exists at "npcs" or
    "npcs/alice". Likewise, structure inside the materialized inventory Value
    does not automatically create further ValueAddress identities.

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

    def resolve(self, relative: str | RelativeValueAddress) -> ValueAddress:
        """
        Resolves a relative address against this complete address.

        Resolution appends the relative address segments to this address using
        exactly one "/" separator. No normalization, traversal, or lookup is
        performed.

        Supplying raw string text constructs and validates a
        RelativeValueAddress before composition.

        Args:
            relative:
                Canonical relative address text or an existing
                RelativeValueAddress.

        Returns:
            A complete ValueAddress containing this address followed by the
            relative address.

        Raises:
            TypeError:
                If relative is neither an exact built-in string nor a
                RelativeValueAddress.
            ValueError:
                If raw relative address text is not canonical.

        """
        if isinstance(relative, RelativeValueAddress):
            relativeAddress = relative
        elif type(relative) is str:
            relativeAddress = RelativeValueAddress(relative)
        else:
            raise TypeError(
                "relative must be a RelativeValueAddress or exact built-in "
                f"string; received {typeName(relative)}.",
            )

        return ValueAddress(f"{self.value}/{relativeAddress.value}")
