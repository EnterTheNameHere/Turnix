# file: tests/backend/values/test_committed.py ; version: 3
import pytest

from backend.values.committed import CommittedValueLayer, StateConflictError, ValueState
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


def test_explicit_absence_is_authoritative_revisioned_state():
    layer = CommittedValueLayer()

    assert layer.state("analysis/result") is ValueState.ABSENT
    assert layer.revisionId("analysis/result") == 0
    assert layer.load("analysis/result") is MISSING

    transaction = layer.openTransaction()
    transaction.setAbsent("analysis/result")

    assert transaction.state("analysis/result") is ValueState.ABSENT
    assert layer.revisionId("analysis/result") == 0

    transaction.commit()

    assert layer.state("analysis/result") is ValueState.ABSENT
    assert layer.revisionId("analysis/result") == 1
    assert layer.load("analysis/result") is MISSING


def test_invalidation_replaces_same_logical_cell_and_advances_revision():
    layer = CommittedValueLayer()

    first = layer.openTransaction()
    first.set("chat/line/17/semantic", {"body": "old"})
    first.commit()

    assert layer.state("chat/line/17/semantic") is ValueState.PRESENT
    assert layer.revisionId("chat/line/17/semantic") == 1

    invalidate = layer.openTransaction()
    invalidate.invalidate("chat/line/17/semantic")

    assert invalidate.state("chat/line/17/semantic") is ValueState.INVALIDATED
    assert layer.state("chat/line/17/semantic") is ValueState.PRESENT

    invalidate.commit()

    assert layer.state("chat/line/17/semantic") is ValueState.INVALIDATED
    assert layer.load("chat/line/17/semantic") is MISSING
    assert layer.revisionId("chat/line/17/semantic") == 2

    replacement = layer.openTransaction()
    replacement.set("chat/line/17/semantic", {"body": "new"})
    replacement.commit()

    assert layer.state("chat/line/17/semantic") is ValueState.PRESENT
    assert layer.load("chat/line/17/semantic") == {"body": "new"}
    assert layer.revisionId("chat/line/17/semantic") == 3


def test_aborted_authority_transition_does_not_change_committed_state():
    layer = CommittedValueLayer()
    seed = layer.openTransaction()
    seed.set("analysis/result", "current")
    seed.commit()

    transaction = layer.openTransaction()
    transaction.invalidate("analysis/result")
    transaction.abort()

    assert layer.state("analysis/result") is ValueState.PRESENT
    assert layer.load("analysis/result") == "current"
    assert layer.revisionId("analysis/result") == 1



def test_committed_metadata_is_detached_and_describable():
    layer = CommittedValueLayer()
    metadata = {
        "producer": {"implementationId": "impl-1"},
        "validity": {"inputRevision": 4},
        "provenance": {"source": {"path": "input.txt"}},
    }

    transaction = layer.openTransaction()
    transaction.set("derived/value", {"answer": 42}, metadata=metadata)
    metadata["validity"]["inputRevision"] = 99
    transaction.commit()

    loadedMetadata = layer.metadata("derived/value")
    assert loadedMetadata == {
        "producer": {"implementationId": "impl-1"},
        "validity": {"inputRevision": 4},
        "provenance": {"source": {"path": "input.txt"}},
    }

    loadedMetadata["validity"]["inputRevision"] = 100
    assert layer.metadata("derived/value")["validity"]["inputRevision"] == 4
    assert layer.describe("derived/value")["revisionId"] == 1



def test_dependency_identity_is_stable_across_outer_commit():
    layer = CommittedValueLayer()
    outer = layer.openTransaction()
    child = outer.openTransaction()
    child.set(
        "derived/input",
        {"value": [1, 2, 3]},
        metadata={
            "producer": {"implementationId": "impl"},
            "validity": {"source": "same"},
            "provenance": {"path": "input.txt"},
        },
    )
    child.commit()

    stagedDependency = outer.dependency("derived/input")
    assert stagedDependency["state"] == "present"
    assert stagedDependency["contentSha256"]
    assert stagedDependency["metadataSha256"]

    outer.commit()

    assert layer.dependency("derived/input") == stagedDependency
    assert layer.revisionId("derived/input") == 1


def test_dependency_identity_changes_when_payload_or_metadata_changes():
    layer = CommittedValueLayer()
    first = layer.openTransaction()
    first.set(
        "derived/input",
        {"value": 1},
        metadata={"producer": {"implementationId": "a"}},
    )
    first.commit()
    original = layer.dependency("derived/input")

    second = layer.openTransaction()
    second.set(
        "derived/input",
        {"value": 2},
        metadata={"producer": {"implementationId": "a"}},
    )
    second.commit()
    changedPayload = layer.dependency("derived/input")
    assert changedPayload["contentSha256"] != original["contentSha256"]

    third = layer.openTransaction()
    third.set(
        "derived/input",
        {"value": 2},
        metadata={"producer": {"implementationId": "b"}},
    )
    third.commit()
    changedMetadata = layer.dependency("derived/input")
    assert changedMetadata["contentSha256"] == changedPayload["contentSha256"]
    assert changedMetadata["metadataSha256"] != changedPayload["metadataSha256"]
