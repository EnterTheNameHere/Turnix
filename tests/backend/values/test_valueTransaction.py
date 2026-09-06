# file: tests/backend/values/test_valueTransaction.py ; version: 2
from __future__ import annotations

import pytest

from backend.values import CommittedValueLayer, MISSING, StateConflictError


def testTransactionHandleStagesWithoutChangingAuthoritativeValue() -> None:
    layer = CommittedValueLayer()
    seed = layer.openTransaction()
    seed.set("npcs/alice/money", 20)
    seed.commit()

    transaction = layer.openTransaction()
    money = transaction.value("npcs/alice/money")
    money.set(15)

    assert money.load() == 15
    assert layer.value("npcs/alice/money").load() == 20

    transaction.commit()

    assert layer.value("npcs/alice/money").load() == 15
    assert layer.revisionId("npcs/alice/money") == 2


def testTransactionHandleCanCreatePreviouslyMissingValue() -> None:
    layer = CommittedValueLayer()
    transaction = layer.openTransaction()
    money = transaction.value("npcs/alice/money")

    assert money.load() is MISSING

    money.set(20)

    assert money.load() == 20
    assert layer.value("npcs/alice/money").load() is MISSING

    transaction.commit()

    assert layer.value("npcs/alice/money").load() == 20


def testTransactionRejectsMissingAsStagedValue() -> None:
    layer = CommittedValueLayer()
    transaction = layer.openTransaction()

    with pytest.raises(TypeError, match="MISSING"):
        transaction.value("npcs/alice/money").set(MISSING)


def testDirectCommittedHandleCannotBypassTransactionBoundary() -> None:
    layer = CommittedValueLayer()

    with pytest.raises(RuntimeError, match="does not support direct value mutation"):
        layer.value("npcs/alice/money").set(20)


def testTransactionSnapshotsStagedAndLoadedMutableValues() -> None:
    layer = CommittedValueLayer()
    transaction = layer.openTransaction()
    inventory = {"bread": 1}
    handle = transaction.value("npcs/alice/inventory")

    handle.set(inventory)
    inventory["bread"] = 0

    loaded = handle.load()
    assert isinstance(loaded, dict)
    assert loaded["bread"] == 1

    loaded["bread"] = 2
    assert handle.load() == {"bread": 1}

    transaction.commit()
    assert layer.value("npcs/alice/inventory").load() == {"bread": 1}


def testNestedTransactionPromotesOnlyToParentUntilOuterCommit() -> None:
    layer = CommittedValueLayer()
    outer = layer.openTransaction()
    child = outer.openTransaction()

    child.value("world/state").set({"phase": 1})
    child.commit()

    assert layer.value("world/state").load() is MISSING
    assert outer.value("world/state").load() == {"phase": 1}

    outer.commit()

    assert layer.value("world/state").load() == {"phase": 1}


def testParentIsSuspendedWhileChildIsActive() -> None:
    layer = CommittedValueLayer()
    outer = layer.openTransaction()
    outer.value("world/state").set("parent")
    child = outer.openTransaction()

    with pytest.raises(RuntimeError, match="unresolved active child"):
        outer.value("world/state").load()
    with pytest.raises(RuntimeError, match="unresolved active child"):
        outer.value("world/state").set("ambiguous")
    with pytest.raises(RuntimeError, match="unresolved active child"):
        outer.openTransaction()

    child.abort()

    assert outer.value("world/state").load() == "parent"


def testConflictUsesFirstTouchedAuthoritativeRevision() -> None:
    layer = CommittedValueLayer()
    first = layer.openTransaction()
    second = layer.openTransaction()

    assert first.value("counter").load() is MISSING

    second.value("counter").set(1)
    second.commit()

    first.value("counter").set(2)

    with pytest.raises(StateConflictError):
        first.commit()

    # Failed authoritative commit leaves the transaction active so the owner
    # can inspect staged work or abort it explicitly.
    assert first.value("counter").load() == 2
    first.abort()

    assert layer.value("counter").load() == 1


@pytest.mark.parametrize("resolution", ["commit", "abort"])
def testResolvedTransactionRejectsFurtherHandleAccess(resolution: str) -> None:
    layer = CommittedValueLayer()
    transaction = layer.openTransaction()
    handle = transaction.value("world/state")
    handle.set("staged")

    if resolution == "commit":
        transaction.commit()
    else:
        transaction.abort()

    with pytest.raises(RuntimeError):
        handle.load()
    with pytest.raises(RuntimeError):
        handle.set("later")
