import pytest

from backend.values.committed import CommittedValueLayer, StateConflictError
from backend.values.sentinels import MISSING


def test_commit_creates_revision_and_detached_decode():
    layer = CommittedValueLayer()
    assert layer.load("analysis/result") is MISSING
    transaction = layer.openTransaction()
    transaction.set("analysis/result", {"items": [1, 2]})
    transaction.commit()
    assert layer.revisionId("analysis/result") == 1
    loaded = layer.load("analysis/result")
    loaded["items"].append(3)
    assert layer.load("analysis/result") == {"items": [1, 2]}


def test_transaction_detaches_input_and_staged_loads():
    layer = CommittedValueLayer()
    transaction = layer.openTransaction()
    source = {"items": [1, 2]}
    transaction.set("analysis/result", source)
    source["items"].append(3)
    staged = transaction.load("analysis/result")
    assert staged == {"items": [1, 2]}
    staged["items"].append(4)
    assert transaction.load("analysis/result") == {"items": [1, 2]}
    transaction.commit()
    assert layer.load("analysis/result") == {"items": [1, 2]}


def test_conflict_detected_from_first_touch_revision():
    layer = CommittedValueLayer()
    first = layer.openTransaction()
    second = layer.openTransaction()
    assert first.load("counter") is MISSING
    second.set("counter", 1)
    second.commit()
    first.set("counter", 2)
    with pytest.raises(StateConflictError):
        first.commit()


def test_nested_commit_only_promotes_to_parent_until_outer_commit():
    layer = CommittedValueLayer()
    outer = layer.openTransaction()
    child = outer.openTransaction()
    child.set("value", "staged")
    child.commit()
    assert layer.load("value") is MISSING
    assert outer.load("value") == "staged"
    outer.commit()
    assert layer.load("value") == "staged"


def test_parent_is_suspended_while_child_transaction_is_active():
    layer = CommittedValueLayer()
    outer = layer.openTransaction()
    outer.set("value", "parent")
    child = outer.openTransaction()

    with pytest.raises(RuntimeError, match="unresolved active child"):
        outer.load("value")
    with pytest.raises(RuntimeError, match="unresolved active child"):
        outer.set("value", "ambiguous")
    with pytest.raises(RuntimeError, match="unresolved active child"):
        outer.openTransaction()

    child.set("value", "child")
    child.commit()
    assert outer.load("value") == "child"
