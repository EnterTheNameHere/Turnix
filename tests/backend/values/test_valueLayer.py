# file: tests/backend/values/test_valueLayer.py ; version: 1
from __future__ import annotations

import pytest

from backend.values import MISSING, InMemoryValueLayer, ValueAddress


def testLayerReturnsStoredValue() -> None:
    layer = InMemoryValueLayer(
        values={"world/player/name": "Alice"},
    )

    assert layer.value("world/player/name").load() == "Alice"


def testLayerReturnsMissingForUnknownAddress() -> None:
    layer = InMemoryValueLayer()

    assert layer.value("world/player/name").load() is MISSING


def testLayerDistinguishesMissingValueFromNone() -> None:
    layer = InMemoryValueLayer(
        values={"world/player/name": None},
    )

    assert layer.value("world/player/name").load() is None
    assert layer.value("world/player/title").load() is MISSING


def testChildLoadsParentValueWhenLocallyMissing() -> None:
    parent = InMemoryValueLayer(
        values={"world/player/name": "Alice"},
    )
    child = InMemoryValueLayer(parent=parent)

    assert child.value("world/player/name").load() == "Alice"


def testChildValueOverridesParentValue() -> None:
    parent = InMemoryValueLayer(
        values={"world/player/name": "Alice"},
    )
    child = InMemoryValueLayer(
        values={"world/player/name": "Bob"},
        parent=parent,
    )

    assert child.value("world/player/name").load() == "Bob"


def testChildNoneShadowsParentValue() -> None:
    parent = InMemoryValueLayer(
        values={"world/player/name": "Alice"},
    )
    child = InMemoryValueLayer(
        values={"world/player/name": None},
        parent=parent,
    )

    assert child.value("world/player/name").load() is None


def testMissingValueRemainsMissingWhenNoLayerProvidesIt() -> None:
    root = InMemoryValueLayer()
    middle = InMemoryValueLayer(parent=root)
    child = InMemoryValueLayer(parent=middle)

    assert child.value("world/player/name").load() is MISSING


def testLayerAcceptsExistingValueAddress() -> None:
    address = ValueAddress("world/player/name")
    layer = InMemoryValueLayer(
        values={"world/player/name": "Alice"},
    )

    handle = layer.value(address)

    assert handle.address is address
    assert handle.load() == "Alice"


def testLayerCreatesAddressBoundHandle() -> None:
    layer = InMemoryValueLayer(
        values={"world/player/name": "Alice"},
    )

    handle = layer.value("world/player/name")

    assert handle.address == ValueAddress("world/player/name")


def testLayerSnapshotsInitialMutableValue() -> None:
    source = {"health": 100}
    layer = InMemoryValueLayer(
        values={"world/player": source},
    )

    source["health"] = 0

    loaded = layer.value("world/player").load()

    assert isinstance(loaded, dict)
    assert loaded["health"] == 100


def testLoadedMutableValueCannotMutateLayer() -> None:
    layer = InMemoryValueLayer(
        values={"world/player": {"health": 100}},
    )

    loaded = layer.value("world/player").load()

    assert isinstance(loaded, dict)
    loaded["health"] = 0

    reloaded = layer.value("world/player").load()

    assert isinstance(reloaded, dict)
    assert reloaded["health"] == 100


def testLayerRejectsMissingAsStoredValue() -> None:
    with pytest.raises(TypeError) as err:
        InMemoryValueLayer(
            values={"world/player": MISSING},
        )

    message = str(err.value)

    assert "MISSING" in message
    assert "cannot be stored" in message
