# file: tests/backend/core/test_collections.py ; version 2
from __future__ import annotations

import builtins
from collections import UserDict
from types import MappingProxyType

import pytest

from backend.core.collections import immutableMapping


def testImmutableMappingReturnsDifferentObject() -> None:
    source = {"value": 1}

    result = immutableMapping(source)

    assert result is not source


def testImmutableMappingIsIndependentSnapshot() -> None:
    source = {"value": 1}

    result = immutableMapping(source)
    source["value"] = 2

    assert result["value"] == 1


def testImmutableMappingRejectsMutation() -> None:
    result = immutableMapping({"value": 1})

    with pytest.raises(TypeError):
        result["value"] = 2  # ty:ignore[invalid-assignment]


def testImmutableMappingPreservesNestedValues() -> None:
    nested: list[int] = [1]
    result = immutableMapping({"nested": nested})

    nested.append(2)

    assert result["nested"] == [1, 2]
    assert result["nested"] is nested


def testImmutableMappingUsesBestAvailableImplementation() -> None:
    result = immutableMapping({"value": 1})

    if hasattr(builtins, "frozendict"):
        assert type(result) is builtins.frozendict
    else:
        assert type(result) is type(MappingProxyType({}))


def testImmutableMappingAcceptsMappingImplementation() -> None:
    source: UserDict[str, int] = UserDict({"value": 1})

    result = immutableMapping(source)

    assert result == {"value": 1}


def testImmutableMappingRejectsNonMapping() -> None:
    with pytest.raises(
        TypeError,
        match=r"value must be a mapping; received list\.",
    ):
        immutableMapping([])  # ty:ignore[invalid-argument-type]
