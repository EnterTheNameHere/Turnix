# file: tests/backend/save/test_saveBundle.py ; version: 2
from __future__ import annotations

import pytest

from backend.save import SaveBundle
from backend.values import CommittedValueLayer, MISSING, ValueState


def _state() -> CommittedValueLayer:
    state = CommittedValueLayer()

    first = state.openTransaction()
    first.set("chat/line/17/semantic", {"body": "hello"})
    first.set("chat/large", {"text": "x" * 4096})
    first.setAbsent("chat/line/18/semantic")
    first.set("chat/line/19/semantic", {"body": "old"})
    first.commit()

    second = state.openTransaction()
    second.invalidate("chat/line/19/semantic")
    second.commit()

    return state


def test_committed_state_snapshot_round_trip_preserves_revisions_and_authority() -> None:
    state = _state()

    restored = CommittedValueLayer.fromSnapshot(state.snapshot())

    assert restored.load("chat/line/17/semantic") == {"body": "hello"}
    assert restored.revisionId("chat/line/17/semantic") == 1
    assert restored.state("chat/line/17/semantic") is ValueState.PRESENT

    assert restored.load("chat/large") == {"text": "x" * 4096}
    assert restored.revisionId("chat/large") == 1

    assert restored.load("chat/line/18/semantic") is MISSING
    assert restored.state("chat/line/18/semantic") is ValueState.ABSENT
    assert restored.revisionId("chat/line/18/semantic") == 1

    assert restored.load("chat/line/19/semantic") is MISSING
    assert restored.state("chat/line/19/semantic") is ValueState.INVALIDATED
    assert restored.revisionId("chat/line/19/semantic") == 2


def test_committed_state_snapshot_contains_only_reachable_chunks() -> None:
    state = _state()
    snapshot = state.snapshot()

    assert snapshot["formatId"] == "actant.committed-values@2"
    chunks = snapshot["chunks"]
    assert isinstance(chunks, list)
    assert len(chunks) == 1
    assert chunks[0]["chunkType"] == "stateValue"


def test_save_bundle_bytes_are_deterministic_and_round_trip() -> None:
    state = _state()
    bundle = SaveBundle.create(applicationId="application-1", committedState=state)

    firstBytes = bundle.toBytes()
    secondBytes = bundle.toBytes()

    assert firstBytes == secondBytes

    restoredBundle = SaveBundle.fromBytes(firstBytes)
    assert restoredBundle.saveBundleId == bundle.saveBundleId
    assert restoredBundle.applicationId == "application-1"
    assert restoredBundle.generation == 1

    restoredState = restoredBundle.restoreCommittedState()
    assert restoredState.load("chat/line/17/semantic") == {"body": "hello"}
    assert restoredState.state("chat/line/19/semantic") is ValueState.INVALIDATED
    assert restoredState.revisionId("chat/line/19/semantic") == 2


def test_next_generation_keeps_bundle_and_application_identity() -> None:
    state = _state()
    first = SaveBundle.create(applicationId="application-1", committedState=state)

    update = state.openTransaction()
    update.set("chat/line/17/semantic", {"body": "changed"})
    update.commit()

    second = first.nextGeneration(committedState=state)

    assert second.saveBundleId == first.saveBundleId
    assert second.applicationId == first.applicationId
    assert second.generation == 2
    assert second.restoreCommittedState().revisionId("chat/line/17/semantic") == 2
    assert second.restoreCommittedState().load("chat/line/17/semantic") == {"body": "changed"}


def test_save_bundle_rejects_tampered_chunk_payload() -> None:
    bundle = SaveBundle.create(applicationId="application-1", committedState=_state())
    snapshot = bundle.snapshot()
    chunks = snapshot["committedState"]["chunks"]
    assert isinstance(chunks, list) and chunks

    chunks[0]["payloadBase64"] = "eA=="

    with pytest.raises(ValueError, match="integrity mismatch"):
        SaveBundle.fromSnapshot(snapshot)



def test_save_bundle_preserves_value_metadata():
    state = CommittedValueLayer()
    transaction = state.openTransaction()
    metadata = {
        "producer": {"implementationId": "impl"},
        "validity": {"sourceSha256": "source"},
        "provenance": {"path": "chat.txt"},
    }
    transaction.set("chat/line/17/semantic", {"body": "hello"}, metadata=metadata)
    transaction.commit()

    bundle = SaveBundle.create(applicationId="application-1", committedState=state)
    restored = SaveBundle.fromBytes(bundle.toBytes()).restoreCommittedState()

    assert restored.metadata("chat/line/17/semantic") == metadata
    assert restored.describe("chat/line/17/semantic") == {
        "address": "chat/line/17/semantic",
        "revisionId": 1,
        "state": "present",
        "metadata": metadata,
    }
