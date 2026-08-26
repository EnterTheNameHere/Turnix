# file: backend/llm/llmOwner.py ; version: 2
from __future__ import annotations

from dataclasses import dataclass

from backend.core.validation import requireExactNonBlankString, typeName
from backend.pack.packCodeEntry import PackCodeEntryInstanceId

__all__: list[str] = [
    "LlmBackendOwner",
    "LlmOwner",
    "requireLlmOwner",
]


_ALLOWED_FIRST_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789",
)
_ALLOWED_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789._-",
)


@dataclass(frozen=True, slots=True)
class LlmBackendOwner:
    """
    Identifies one built-in backend owner within the LLM domain.

    Backend-owner identifiers provide stable machine-facing attribution for
    LLM-domain behaviour that does not originate from loaded Pack code. They
    may identify built-in components, policies, producers, or other backend
    participants whose ownership must remain distinguishable in run state,
    diagnostics, tracing, or namespaced data.

    ownerId is canonical identity rather than human-facing text. It:

        - is an exact built-in string;
        - contains ASCII characters only;
        - starts with a lowercase ASCII letter or digit;
        - otherwise contains only lowercase ASCII letters, digits, ".", "_",
          and "-";
        - is never normalized or case-folded.

    Human-readable names and descriptions belong in metadata or diagnostics
    rather than in this identifier.

    Equality and hashing are value-based through the frozen dataclass contract.
    A backend owner and a PackCodeEntryInstanceId remain distinct owner kinds
    even if their printable forms happen to contain the same text.
    """

    ownerId: str

    def __post_init__(self) -> None:
        """Validates the canonical backend-owner identifier."""
        cleanOwnerId = requireExactNonBlankString(self.ownerId, "ownerId")

        if not cleanOwnerId.isascii():
            invalidCharacter = next(
                character
                for character in cleanOwnerId
                if not character.isascii()
            )
            raise ValueError(
                "ownerId must contain ASCII characters only.\n"
                f"received: {cleanOwnerId!r}\n"
                f"invalid character: {invalidCharacter!r}",
            )

        firstCharacter = cleanOwnerId[0]
        if firstCharacter not in _ALLOWED_FIRST_CHARACTERS:
            raise ValueError(
                "ownerId must start with a lowercase ASCII letter or digit.\n"
                f"received: {cleanOwnerId!r}\n"
                f"invalid first character: {firstCharacter!r}",
            )

        for character in cleanOwnerId[1:]:
            if character not in _ALLOWED_CHARACTERS:
                raise ValueError(
                    f"ownerId contains invalid character {character!r}.\n"
                    f"received: {cleanOwnerId!r}\n"
                    "Backend-owner identifiers allow only lowercase ASCII "
                    'letters, digits, ".", "_", and "-".',
                )

    def __str__(self) -> str:
        """Returns the canonical backend-owner identifier."""
        return self.ownerId


type LlmOwner = LlmBackendOwner | PackCodeEntryInstanceId


def requireLlmOwner(owner: LlmOwner, name: str) -> LlmOwner:
    """
    Validates and returns one LLM-domain owner identity.

    Built-in backend behaviour is attributed through LlmBackendOwner.
    Behaviour contributed by loaded Pack code is attributed through
    PackCodeEntryInstanceId. Keeping these owner kinds explicit preserves
    producer attribution without pretending that every LLM-domain contribution
    originates from Pack code.

    Args:
        owner:
            Owner identity to validate.
        name:
            Diagnostic name used when reporting invalid input.

    Returns:
        The original validated owner object unchanged.

    Raises:
        TypeError:
            If owner is neither an LlmBackendOwner nor a
            PackCodeEntryInstanceId, or if name is not an exact built-in
            string.
        ValueError:
            If name is blank or contains surrounding whitespace.

    """
    cleanName = requireExactNonBlankString(name, "name")

    if not isinstance(owner, LlmBackendOwner | PackCodeEntryInstanceId):
        raise TypeError(
            f"{cleanName} must be an LlmBackendOwner or "
            "PackCodeEntryInstanceId; "
            f"received {typeName(owner)}.",
        )

    return owner
