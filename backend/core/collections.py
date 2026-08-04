# backend/core/collections.py ; version 3
from __future__ import annotations

import builtins
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from backend.core.validation import requireMapping

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__: list[str] = [
    "immutableMapping",
]


class _FrozenDictType(Protocol):
    def __call__[K, V](
        self,
        value: Mapping[K, V],
    ) -> Mapping[K, V]:
        ...


_FROZEN_DICT_CONSTRUCTOR = cast(
    _FrozenDictType | None,
    getattr(
        builtins,
        "frozendict",
        None,
    ),
)


def immutableMapping[K, V](
    value: Mapping[K, V],
) -> Mapping[K, V]:
    """
    Returns an independent shallow immutable snapshot.

    Python 3.15 uses built-in frozendict. Older supported runtimes use
    a privately owned dict wrapped in MappingProxyType.

    Nested values are preserved unchanged.

    Raises:
        TypeError:
            If value is not a mapping.

    """
    requireMapping(value, "value")

    if _FROZEN_DICT_CONSTRUCTOR is not None:
        return _FROZEN_DICT_CONSTRUCTOR(value)

    return MappingProxyType(dict(value))

