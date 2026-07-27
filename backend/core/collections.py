# backend/core/collections.py ; version 2
from __future__ import annotations

import builtins
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

from backend.core.validation import typeName

__all__: list[str] = [
    "immutableMapping",
]

_FROZEN_DICT_TYPE: Any | None = getattr(
    builtins,
    "frozendict",
    None,
)


def immutableMapping[K, V](
    value: Mapping[K, V],
) -> Mapping[K, V]:
    """
    Return an independent shallow immutable snapshot.

    Python 3.15 uses built-in frozendict. Older supported runtimes use
    a privately owned dict wrapped in MappingProxyType.

    Nested values are preserved unchanged.

    Raises:
        TypeError:
            If value is not a mapping.

    """
    if not isinstance(value, Mapping):
        raise TypeError(f"value must be a mapping, not {typeName(value)}.")

    if _FROZEN_DICT_TYPE is not None:
        return cast(Mapping[K, V], _FROZEN_DICT_TYPE(value))

    return MappingProxyType(dict(value))

