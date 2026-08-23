# file: tests/backend/values/test_valueHandle.py ; version: 1
from __future__ import annotations

from backend.values import (
    MISSING,
    InMemoryValueLayer,
    RelativeValueAddress,
    ValueAddress,
)


def testHandleDerivesRelativeAddress() -> None:
    layer = InMemoryValueLayer()

    alice = layer.value("npcs/alice")
    inventory = alice.value("inventory")

    assert inventory.address == ValueAddress("npcs/alice/inventory")


def testHandleDerivesReusableRelativeAddress() -> None:
    layer = InMemoryValueLayer(
        values={
            "npcs/alice/inventory": {"bread": 1},
            "npcs/ben/inventory": {"bread": 2},
        },
    )
    inventoryAddress = RelativeValueAddress("inventory")

    alice = layer.value("npcs/alice")
    ben = layer.value("npcs/ben")

    aliceInventory = alice.value(inventoryAddress)
    benInventory = ben.value(inventoryAddress)

    assert aliceInventory.address == ValueAddress("npcs/alice/inventory")
    assert benInventory.address == ValueAddress("npcs/ben/inventory")

    aliceValue = aliceInventory.load()
    benValue = benInventory.load()

    assert isinstance(aliceValue, dict)
    assert isinstance(benValue, dict)
    assert aliceValue["bread"] == 1
    assert benValue["bread"] == 2


def testDerivedHandleRemainsBoundToSameLayerView() -> None:
    parent = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    child = InMemoryValueLayer(
        values={"npcs/alice/money": 15},
        parent=parent,
    )

    alice = child.value("npcs/alice")
    money = alice.value("money")

    assert money.load() == 15


def testBaseHandleNeedNotContainValueForChildToExist() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/inventory": {"bread": 1}},
    )

    npcs = layer.value("npcs")
    alice = npcs.value("alice")
    inventory = alice.value("inventory")

    assert npcs.load() is MISSING
    assert alice.load() is MISSING

    loaded = inventory.load()

    assert isinstance(loaded, dict)
    assert loaded["bread"] == 1


def testHandleDerivationDoesNotInterpretValueStructure() -> None:
    layer = InMemoryValueLayer(
        values={
            "npcs/alice/inventory": {
                "armor": "iron-armor",
            },
        },
    )

    inventory = layer.value("npcs/alice/inventory")

    loaded = inventory.load()

    assert isinstance(loaded, dict)
    assert loaded["armor"] == "iron-armor"
    assert inventory.value("armor").load() is MISSING


def testSameAddressThroughDifferentLayerViewsCanObserveDifferentValues(
) -> None:
    parent = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    child = InMemoryValueLayer(
        values={"npcs/alice/money": 15},
        parent=parent,
    )

    parentMoney = parent.value("npcs/alice/money")
    childMoney = child.value("npcs/alice/money")

    assert parentMoney.address == childMoney.address
    assert parentMoney.load() == 20
    assert childMoney.load() == 15
