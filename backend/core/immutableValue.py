# file: backend/core/immutableValue.py ; version: 1
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from backend.core.collections import immutableMapping
from backend.core.validation import typeName

__all__: list[str] = [
    "ImmutableScalar",
    "ImmutableValue",
    "ImmutableValueFreezer",
]


type ImmutableScalar = bool | int | float | str | None
type ImmutableValue = (
    ImmutableScalar
    | tuple[ImmutableValue, ...]
    | Mapping[str, ImmutableValue]
)


_EMPTY_MAPPING: Mapping[str, ImmutableValue] = immutableMapping({})


class ImmutableValueFreezer:
    """
    Convert supported values into recursively immutable values.

    Cycle detection and input-size limits are intentionally centralized here so
    they can be added later without changing consumer-facing values types.
    """

    def freeze(
        self,
        value: object,
        name: str = "value",
    ) -> ImmutableValue:
        return self._freeze(value, path=name)

    def freezeMapping(
        self,
        value: Mapping[str, object] | None,
        name: str = "value",
    ) -> Mapping[str, ImmutableValue]:
        if value is None:
            return _EMPTY_MAPPING

        if not isinstance(value, Mapping):
            raise TypeError(
                f"{name} must be a mapping, not {typeName(value)}.",
            )

        frozen = self._freeze(value, path=name)

        if not isinstance(frozen, Mapping):
            raise TypeError(
                f"Frozen {name} unexpectedly stopped being a mapping.",
            )

        return cast(Mapping[str, ImmutableValue], frozen)


    def _freeze(
        self,
        value: object,
        *,
        path: str,
    ) -> ImmutableValue:
        if (
            value is None
            or isinstance(value, bool | int | float | str)
        ):
            return value

        if isinstance(value, Mapping):
            frozenMapping: dict[str, ImmutableValue] = {}

            for rawKey, rawValue in value.items():
                if not isinstance(rawKey, str):
                    raise TypeError(
                        f"Mapping key at {path} must be a string; "
                        f"received {typeName(rawKey)}.",
                    )

                frozenMapping[rawKey] = self._freeze(
                    rawValue,
                    path=f"{path}.{rawKey}",
                )

            return immutableMapping(frozenMapping)

        if isinstance(value, Sequence) and not isinstance(
            value,
            str | bytes | bytearray,
        ):
            return tuple(
                self._freeze(item, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            )

        if isinstance(value, set | frozenset):
            raise TypeError(
                f"Value at {path} must not be a set. "
                "Convert it to a deterministically ordered sequence.",
            )

        if callable(value):
            raise TypeError(
                f"Value at {path} must not be callable; "
                f"received {typeName(value)}.",
            )

        raise TypeError(f"Unsupported value at {path}: {typeName(value)}.")
