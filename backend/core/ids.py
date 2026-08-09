# file: backend/core/ids.py ; version: 2
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import ClassVar, Self

from backend.core.validation import requireExactNonBlankString, typeName

__all__: list[str] = [
    "Uuid7Id",
    "requireOptionalUuid7Id",
    "requireUuid7Id",
]


@dataclass(frozen=True, slots=True)
class Uuid7Id:
    """
    Base value object for typed Actant runtime identifiers.

    Concrete domains define identity types by deriving from this class.
    Instances of different concrete identity types are never equal, even
    when they contain the same UUID value.
    """

    UUID_VERSION: ClassVar[int] = 7

    value: uuid.UUID

    def __post_init__(self) -> None:
        """
        Validate the UUID value.

        Raises:
            TypeError:
                If value is not a UUID.
            ValueError:
                If value is not a UUIDv7.

        """
        if not isinstance(self.value, uuid.UUID):
            raise TypeError(
                f"value must be a uuid.UUID, not {typeName(self.value)}.",
            )

        if self.value.version != self.UUID_VERSION:
            raise ValueError("value must be a UUIDv7.")

    def __str__(self) -> str:
        """Return the canonical UUID string."""
        return str(self.value)

    @classmethod
    def new(cls) -> Self:
        """Create a new identifier containing a UUIDv7 value."""
        return cls(uuid.uuid7())

    @classmethod
    def parse(cls, value: str, name: str = "value") -> Self:
        """
        Parse a UUIDv7 identity from its canonical string representation.

        Raises:
            TypeError:
                If value is not a string.
            ValueError:
                If value is not a canonical UUIDv7 string.

        """
        text = requireExactNonBlankString(value, name)

        try:
            parsed = uuid.UUID(text)
        except ValueError as err:
            raise ValueError(f"{name} must be a UUIDv7 string.") from err

        if str(parsed) != text:
            raise ValueError(
                f"{name} must use canonical lowercase UUIDv7 syntax.",
            )

        if parsed.version != cls.UUID_VERSION:
            raise ValueError(f"{name} must be a UUIDv7.")

        return cls(parsed)


def requireUuid7Id[T: Uuid7Id](value: T, name: str) -> T:
    """
    Validates that value is a UUIDv7 identifier.

    Subclasses of Uuid7Id are accepted so concrete typed ID domains such as
    TraceEventId, TraceSpanId, and TraceProducerId preserve their own type.

    Raises:
        TypeError:
            If value is not a UUIDv7 identifier,
            or name is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    if not isinstance(value, Uuid7Id):
        raise TypeError(
            f"{cleanName} must be a Uuid7Id identifier; "
            f"received {typeName(value)}.",
        )

    return value


def requireOptionalUuid7Id[T: Uuid7Id](value: T | None, name: str) -> T | None:
    """
    Validates that value is None or a UUIDv7 identifier.

    Raises:
        TypeError:
            If value is neither None nor a UUIDv7 identifier,
            or name is not an exact built-in string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    if value is None:
        return None

    return requireUuid7Id(value, cleanName)
