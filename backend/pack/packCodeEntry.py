# file: backend/pack/packCodeEntry.py ; version: 1
from __future__ import annotations

from backend.core.ids import Uuid7Id

__all__: list[str] = [
    "PackCodeEntryInstanceId",
]


class PackCodeEntryInstanceId(Uuid7Id):
    """Identifies one loaded Pack code entry."""

    __slots__ = ()

