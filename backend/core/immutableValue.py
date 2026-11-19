# file: backend/core/immutableValue.py ; version: 5
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, cast

from backend.core.collections import immutableMapping
from backend.core.validation import (
    requireExactNonBlankString,
    requireMapping,
    requireOptionalNonNegativeInteger,
    typeName,
)

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


_EMPTY_MAPPING: Final[Mapping[str, ImmutableValue]] = immutableMapping({})


@dataclass(slots=True)
class _FreezeState:
    """
    Hold traversal state for one freeze operation.

    activeContainerPaths contains only containers on the current recursion
    branch. Removing a container after traversal allows repeated references
    without incorrectly classifying them as cycles.
    """

    activeContainerPaths: dict[int, str] = field(default_factory=dict)
    itemCount: int = 0


class ImmutableValueFreezer:
    """
    Convert supported values into recursively immutable values.

    Supported scalar values are:

    - None
    - bool
    - int
    - finite float values
    - str

    Scalar values and mapping keys must use exact built-in Python types.
    Subclasses of bool, int, float, and str are rejected. Float values must
    be finite; NaN and positive or negative infinity are rejected.

    Mappings must use string keys and are copied into immutable mappings.
    Non-string sequences are copied into tuples.

    Sets are rejected because their iteration order is not a deterministic
    value contract. Callables and all other unsupported objects are rejected.

    Cycles encountered during traversal are detected. Repeated references
    that do not form a cycle are allowed and are independently frozen at
    each location. Configured limits may terminate traversal before a deeper
    cycle is reached.

    Optional limits apply independently to each freeze operation:

    - maxDepth limits nested container depth. A root container has depth zero.
    - maxItems limits the total mapping entries and sequence elements visited.
    - maxStringLength limits every string value and mapping key.

    A limit of None means unlimited.
    """

    def __init__(
        self,
        *,
        maxDepth: int | None = None,
        maxItems: int | None = None,
        maxStringLength: int | None = None,
    ) -> None:
        """Initialize an ImmutableValueFreezer."""
        self._maxDepth = requireOptionalNonNegativeInteger(
            maxDepth,
            "maxDepth",
        )
        self._maxItems = requireOptionalNonNegativeInteger(
            maxItems,
            "maxItems",
        )
        self._maxStringLength = requireOptionalNonNegativeInteger(
            maxStringLength,
            "maxStringLength",
        )

    def freeze(
        self,
        value: object,
        name: str = "value",
    ) -> ImmutableValue:
        """
        Freeze one supported value.

        name identifies the root value in diagnostics.
        """
        rootPath = requireExactNonBlankString(name, "name")

        return self._freeze(
            value,
            path=rootPath,
            depth=0,
            state=_FreezeState(),
        )

    def freezeMapping(
        self,
        value: Mapping[object, object] | None,
        name: str = "value",
    ) -> Mapping[str, ImmutableValue]:
        """
        Freeze a mapping or return the shared empty mapping for None.

        None is treated as an absent optional mapping, not as an immutable
        scalar result.
        """
        rootPath = requireExactNonBlankString(name, "name")

        if value is None:
            return _EMPTY_MAPPING

        requireMapping(value, rootPath)

        frozen = self._freeze(
            value,
            path=rootPath,
            depth=0,
            state=_FreezeState(),
        )

        if not isinstance(frozen, Mapping):
            raise RuntimeError(  # noqa: TRY004
                f"Frozen {rootPath} unexpectedly stopped being a mapping.",
            )

        return cast(Mapping[str, ImmutableValue], frozen)

    def _freeze(
        self,
        value: object,
        *,
        path: str,
        depth: int,
        state: _FreezeState,
    ) -> ImmutableValue:
        if value is None:
            return None

        valueType = type(value)

        if valueType is str:
            cleanValue = cast(str, value)
            self._requireStringLength(cleanValue, path=path)
            return cleanValue

        if valueType is bool:
            return cast(ImmutableScalar, value)

        if valueType is int:
            return cast(ImmutableScalar, value)

        if valueType is float:
            cleanValue = cast(float, value)

            if not math.isfinite(cleanValue):
                raise ValueError(
                    f"Float value at {path} must be finite; "
                    f"received {cleanValue}.",
                )

            return cleanValue

        if isinstance(value, str | bool | int | float):
            raise TypeError(
                f"Scalar value at {path} must use an exact built-in type; "
                f"received {typeName(value)}.",
            )

        if callable(value):
            raise TypeError(
                f"Value at {path} must not be callable; "
                f"received {typeName(value)}.",
            )

        if isinstance(value, Mapping):
            return self._freezeMappingValue(
                cast(Mapping[object, object], value),
                path=path,
                depth=depth,
                state=state,
            )

        if isinstance(value, Sequence) and not isinstance(
            value,
            bytes | bytearray,
        ):
            return self._freezeSequenceValue(
                value,
                path=path,
                depth=depth,
                state=state,
            )

        if isinstance(value, set | frozenset):
            raise TypeError(
                f"Value at {path} must not be a set. "
                "Convert it to a deterministically ordered sequence.",
            )

        raise TypeError(
            f"Unsupported value at {path}: {typeName(value)}.",
        )

    def _freezeMappingValue(
        self,
        value: Mapping[object, object],
        *,
        path: str,
        depth: int,
        state: _FreezeState,
    ) -> Mapping[str, ImmutableValue]:
        self._requireDepth(depth, path=path)
        self._enterContainer(value, path=path, state=state)

        try:
            frozenMapping: dict[str, ImmutableValue] = {}

            for rawKey, rawValue in value.items():
                if type(rawKey) is not str:
                    raise TypeError(
                        f"Mapping key at {path} must be an exact built-in "
                        f"string; received {typeName(rawKey)}.",
                    )

                keyPath = f"{path}[{rawKey!r}]"

                self._consumeItem(path=keyPath, state=state)

                self._requireStringLength(
                    rawKey,
                    path=f"{keyPath} mapping key",
                )

                frozenMapping[rawKey] = self._freeze(
                    rawValue,
                    path=keyPath,
                    depth=depth + 1,
                    state=state,
                )

            return immutableMapping(frozenMapping)

        finally:
            self._leaveContainer(value, state=state)

    def _freezeSequenceValue(
        self,
        value: Sequence[object],
        *,
        path: str,
        depth: int,
        state: _FreezeState,
    ) -> tuple[ImmutableValue, ...]:
        self._requireDepth(depth, path=path)
        self._enterContainer(value, path=path, state=state)

        try:
            frozenItems: list[ImmutableValue] = []

            for index, rawItem in enumerate(value):
                itemPath = f"{path}[{index}]"

                self._consumeItem(path=itemPath, state=state)
                frozenItems.append(
                    self._freeze(
                        rawItem,
                        path=itemPath,
                        depth=depth + 1,
                        state=state,
                    ),
                )

            return tuple(frozenItems)

        finally:
            self._leaveContainer(value, state=state)

    def _enterContainer(
        self,
        value: object,
        *,
        path: str,
        state: _FreezeState,
    ) -> None:
        containerId = id(value)
        previousPath = state.activeContainerPaths.get(containerId)

        if previousPath is not None:
            raise ValueError(
                f"Cycle detected at {path}; "
                f"the same container is already active at {previousPath}.",
            )

        state.activeContainerPaths[containerId] = path

    def _leaveContainer(
        self,
        value: object,
        *,
        state: _FreezeState,
    ) -> None:
        containerId = id(value)

        if containerId not in state.activeContainerPaths:
            raise RuntimeError(
                "Immutable-value traversal lost an active container.",
            )

        del state.activeContainerPaths[containerId]

    def _consumeItem(
        self,
        *,
        path: str,
        state: _FreezeState,
    ) -> None:
        state.itemCount += 1

        if (
            self._maxItems is not None
            and state.itemCount > self._maxItems
        ):
            raise ValueError(
                f"Immutable value exceeds maxItems={self._maxItems} "
                f"while traversing {path}.",
            )

    def _requireDepth(
        self,
        depth: int,
        *,
        path: str,
    ) -> None:
        if (
            self._maxDepth is not None
            and depth > self._maxDepth
        ):
            raise ValueError(
                f"Container at {path} exceeds "
                f"maxDepth={self._maxDepth}; "
                f"got depth {depth}.",
            )

    def _requireStringLength(
        self,
        value: str,
        *,
        path: str,
    ) -> None:
        if (
            self._maxStringLength is not None
            and len(value) > self._maxStringLength
        ):
            raise ValueError(
                f"String at {path} exceeds "
                f"maxStringLength={self._maxStringLength}; "
                f"got length {len(value)}.",
            )
