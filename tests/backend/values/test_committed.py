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
