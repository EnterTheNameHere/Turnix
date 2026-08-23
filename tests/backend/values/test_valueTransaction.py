# file: tests/backend/values/test_valueTransaction.py ; version: 1
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.values import (
    MISSING,
    InMemoryValueLayer,
    ValueAddress,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class RejectingPromotionLayer(InMemoryValueLayer):
    def _acceptPromotion(self, values: Mapping[ValueAddress, object]) -> None:
        raise RuntimeError("Promotion rejected.")


def testTransactionStagesValueWithoutChangingParent() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    transaction = layer.openTransaction()

    money = transaction.value("npcs/alice/money")
    money.set(15)

    assert money.load() == 15
    assert layer.value("npcs/alice/money").load() == 20


def testTransactionReadsUnchangedValueFromParent() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    transaction = layer.openTransaction()

    assert transaction.value("npcs/alice/money").load() == 20


def testTransactionCanStageNoneOverParentValue() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/equipment/helmet": "iron-helmet"},
    )
    transaction = layer.openTransaction()

    helmet = transaction.value("npcs/alice/equipment/helmet")
    helmet.set(None)

    assert helmet.load() is None


def testTransactionCanStagePreviouslyMissingValue() -> None:
    layer = InMemoryValueLayer()
    transaction = layer.openTransaction()

    money = transaction.value("npcs/alice/money")

    assert money.load() is MISSING

    money.set(20)

    assert money.load() == 20
    assert layer.value("npcs/alice/money").load() is MISSING


def testTransactionAbortDiscardsStagedValue() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    transaction = layer.openTransaction()

    transaction.value("npcs/alice/money").set(15)

    transaction.abort()

    assert layer.value("npcs/alice/money").load() == 20


def testTransactionCommitPromotesStagedValueToParent() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    transaction = layer.openTransaction()

    transaction.value("npcs/alice/money").set(15)

    transaction.commit()

    assert layer.value("npcs/alice/money").load() == 15


def testTransactionRejectsMissingAsStagedValue() -> None:
    layer = InMemoryValueLayer()
    transaction = layer.openTransaction()

    with pytest.raises(TypeError) as err:
        transaction.value("npcs/alice/money").set(MISSING)

    message = str(err.value)

    assert "MISSING" in message
    assert "cannot be staged" in message


def testDirectLayerHandleCannotMutateCommittedValue() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )

    with pytest.raises(RuntimeError) as err:
        layer.value("npcs/alice/money").set(15)

    assert "does not support direct value mutation" in str(err.value)

    assert layer.value("npcs/alice/money").load() == 20


def testTransactionSnapshotsValueWhenStaged() -> None:
    layer = InMemoryValueLayer()
    transaction = layer.openTransaction()
    inventory = {"bread": 1}

    transaction.value("npcs/alice/inventory").set(inventory)

    inventory["bread"] = 0

    loaded = transaction.value("npcs/alice/inventory").load()

    assert isinstance(loaded, dict)
    assert loaded["bread"] == 1


def testLoadedTransactionValueCannotMutateStagedState() -> None:
    layer = InMemoryValueLayer()
    transaction = layer.openTransaction()

    inventory = transaction.value("npcs/alice/inventory")
    inventory.set({"bread": 1})

    loaded = inventory.load()

    assert isinstance(loaded, dict)
    loaded["bread"] = 0

    reloaded = inventory.load()

    assert isinstance(reloaded, dict)
    assert reloaded["bread"] == 1


def testCommittedMutableValueCannotBeChangedThroughFormerStagedObject(
) -> None:
    layer = InMemoryValueLayer()
    transaction = layer.openTransaction()
    inventory = {"bread": 1}

    transaction.value("npcs/alice/inventory").set(inventory)
    transaction.commit()

    inventory["bread"] = 0

    loaded = layer.value("npcs/alice/inventory").load()

    assert isinstance(loaded, dict)
    assert loaded["bread"] == 1


def testChildTransactionReadsThroughParentTransaction() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    parent = layer.openTransaction()
    parent.value("npcs/alice/money").set(15)

    child = parent.openTransaction()

    assert child.value("npcs/alice/money").load() == 15


def testChildValueShadowsParentTransactionValue() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    parent = layer.openTransaction()
    parent.value("npcs/alice/money").set(15)

    child = parent.openTransaction()
    child.value("npcs/alice/money").set(10)

    assert child.value("npcs/alice/money").load() == 10
    assert parent.value("npcs/alice/money").load() == 15
    assert layer.value("npcs/alice/money").load() == 20


def testParentAbortDiscardsMutationPromotedFromChild() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    parent = layer.openTransaction()
    child = parent.openTransaction()

    child.value("npcs/alice/money").set(15)
    child.commit()

    parent.abort()

    assert layer.value("npcs/alice/money").load() == 20


def testParentCommitMakesPromotedChildMutationAuthoritative() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    parent = layer.openTransaction()
    child = parent.openTransaction()

    child.value("npcs/alice/money").set(15)
    child.commit()

    assert layer.value("npcs/alice/money").load() == 20

    parent.commit()

    assert layer.value("npcs/alice/money").load() == 15


@pytest.mark.parametrize(
    "resolution",
    [
        "commit",
        "abort",
    ],
)
def testResolvedTransactionRejectsFurtherLoad(
    resolution: str,
) -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    transaction = layer.openTransaction()
    money = transaction.value("npcs/alice/money")

    if resolution == "commit":
        transaction.commit()
    else:
        transaction.abort()

    with pytest.raises(RuntimeError):
        money.load()


@pytest.mark.parametrize(
    "resolution",
    [
        "commit",
        "abort",
    ],
)
def testResolvedTransactionRejectsFurtherSet(
    resolution: str,
) -> None:
    layer = InMemoryValueLayer()
    transaction = layer.openTransaction()
    money = transaction.value("npcs/alice/money")

    if resolution == "commit":
        transaction.commit()
    else:
        transaction.abort()

    with pytest.raises(RuntimeError):
        money.set(20)


def testParentCannotCommitWithActiveChild() -> None:
    layer = InMemoryValueLayer()
    parent = layer.openTransaction()
    parent.openTransaction()

    with pytest.raises(RuntimeError) as err:
        parent.commit()

    assert "active child" in str(err.value)


def testParentCannotAbortWithActiveChild() -> None:
    layer = InMemoryValueLayer()
    parent = layer.openTransaction()
    parent.openTransaction()

    with pytest.raises(RuntimeError) as err:
        parent.abort()

    assert "active child" in str(err.value)


def testChildAbortAllowsParentToResolve() -> None:
    layer = InMemoryValueLayer()
    parent = layer.openTransaction()
    child = parent.openTransaction()

    child.abort()
    parent.commit()


def testChildCommitAllowsParentToResolve() -> None:
    layer = InMemoryValueLayer()
    parent = layer.openTransaction()
    child = parent.openTransaction()

    child.commit()
    parent.commit()


def testChildTransactionCommitChangesParentTransactionOnly() -> None:
    layer = InMemoryValueLayer(
        values={"npcs/alice/money": 20},
    )
    parent = layer.openTransaction()
    child = parent.openTransaction()

    child.value("npcs/alice/money").set(15)
    child.commit()

    assert parent.value("npcs/alice/money").load() == 15
    assert layer.value("npcs/alice/money").load() == 20


def testFailedCommitLeavesTransactionActiveWithStagedValues() -> None:
    layer = RejectingPromotionLayer(
        values={"npcs/alice/money": 20},
    )
    transaction = layer.openTransaction()
    money = transaction.value("npcs/alice/money")

    money.set(15)

    with pytest.raises(RuntimeError, match="Promotion rejected"):
        transaction.commit()

    assert money.load() == 15

    money.set(10)

    assert money.load() == 10

    transaction.abort()

    assert layer.value("npcs/alice/money").load() == 20


def testAliceBuysBreadAndBeerFromMaid() -> None:
    layer = InMemoryValueLayer(
        values={
            "npcs/alice/money": 20,
            "npcs/alice/inventory": {},
            "npcs/anna/till": 100,
            "locations/tavern/stock": {
                "bread": 8,
                "beer": 12,
            },
        },
    )
    transaction = layer.openTransaction()

    alice = transaction.value("npcs/alice")
    anna = transaction.value("npcs/anna")
    tavern = transaction.value("locations/tavern")

    moneyHandle = alice.value("money")
    inventoryHandle = alice.value("inventory")
    tillHandle = anna.value("till")
    stockHandle = tavern.value("stock")

    money = moneyHandle.load()
    inventory = inventoryHandle.load()
    till = tillHandle.load()
    stock = stockHandle.load()

    assert isinstance(money, int)
    assert isinstance(inventory, dict)
    assert isinstance(till, int)
    assert isinstance(stock, dict)

    cost = 5

    money -= cost
    till += cost
    stock["bread"] -= 1
    stock["beer"] -= 1
    inventory["bread"] = 1
    inventory["beer"] = 1

    moneyHandle.set(money)
    inventoryHandle.set(inventory)
    tillHandle.set(till)
    stockHandle.set(stock)

    transaction.commit()

    assert layer.value("npcs/alice/money").load() == 15
    assert layer.value("npcs/anna/till").load() == 105

    committedInventory = layer.value("npcs/alice/inventory").load()
    committedStock = layer.value("locations/tavern/stock").load()

    assert isinstance(committedInventory, dict)
    assert isinstance(committedStock, dict)

    assert committedInventory == {
        "bread": 1,
        "beer": 1,
    }

    assert committedStock == {
        "bread": 7,
        "beer": 11,
    }
