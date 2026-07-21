# file: tests/backend/core/collections.test.py ; version 1
from __future__ import annotations

import builtins

import pytest

from backend.core.collections import immutableMapping


def testImmutableMappingIsIndependentSnapshot() -> None:
    source = {"value": 1}

    result = immutableMapping(source)
    source["value"] = 2

    assert result["value"] == 1


def testImmutableMappingRejectsMutation() -> None:
    result = immutableMapping({"value": 1})

    with pytest.raises(TypeError):
        result["value"] = 2  # type: ignore[index] # ty:ignore[invalid-assignment]


def testImmutableMappingPreservesNestedValues() -> None:
    nested: list[int] = [1]
    result = immutableMapping({"nested": nested})

    nested.append(2)

    assert result["nested"] == [1, 2]


def testImmutableMappingUsesBestAvailableImplementation() -> None:
    result = immutableMapping({"value": 1})

    if hasattr(builtins, "frozendict"):
        assert type(result).__name__ == "frozendict"
    else:
        assert type(result).__name__ == "mappingproxy"
