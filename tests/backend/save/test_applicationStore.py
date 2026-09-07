# file: tests/backend/save/test_applicationStore.py ; version: 2
from __future__ import annotations

import json

import pytest

from backend.save import ApplicationStore, SaveBundle
from backend.values import CommittedValueLayer, MISSING, ValueState


def _bundle(*, applicationId: str = "application-1") -> tuple[CommittedValueLayer, SaveBundle]:
    state = CommittedValueLayer()
    transaction = state.openTransaction()
    transaction.set(
        "chat/line/17/semantic",
        {"body": "hello"},
        metadata={
            "producer": {"implementationId": "chat-semantics"},
            "validity": {"rawLine": "viewer: hello"},
        },
    )
    transaction.setAbsent("chat/line/18/semantic")
    transaction.commit()
    return state, SaveBundle.create(
        appPackId="test.app",
        applicationId=applicationId,
        committedState=state,
    )


def test_application_store_creates_expected_application_layout_and_loads_root(tmp_path):
    state, bundle = _bundle()
    store = ApplicationStore(tmp_path / "saves")

    applicationPath = store.createApplication(bundle)

    assert applicationPath == tmp_path / "saves" / "test.app" / "application-1"
    assert (applicationPath / "application.json").is_file()
    assert (applicationPath / "current").is_file()
    assert (applicationPath / "generations" / "00000001.bundle").is_file()

    loaded = store.load(appPackId="test.app", applicationId="application-1")
    assert loaded.recoveredFromGeneration is None
    assert loaded.bundle.appPackId == "test.app"
    assert loaded.bundle.applicationId == "application-1"
    assert loaded.bundle.generation == 1

    restored = loaded.bundle.restoreCommittedState()
    assert restored.load("chat/line/17/semantic") == {"body": "hello"}
    assert restored.metadata("chat/line/17/semantic") == state.metadata("chat/line/17/semantic")
    assert restored.state("chat/line/18/semantic") is ValueState.ABSENT


def test_application_store_never_serializes_open_transaction_state(tmp_path):
    state, _initial = _bundle()

    openTransaction = state.openTransaction()
    openTransaction.set("chat/in-flight", {"mustNotPersist": True})

    bundle = SaveBundle.create(
        appPackId="test.app",
        applicationId="application-open-transaction",
        committedState=state,
    )
    store = ApplicationStore(tmp_path / "saves")
    store.createApplication(bundle)

    loadedState = store.load(
        appPackId="test.app",
        applicationId="application-open-transaction",
    ).bundle.restoreCommittedState()

    assert openTransaction.load("chat/in-flight") == {"mustNotPersist": True}
    assert state.load("chat/in-flight") is MISSING
    assert loadedState.load("chat/in-flight") is MISSING
    assert loadedState.revisionId("chat/in-flight") == 0

    openTransaction.abort()


def test_application_store_publishes_immutable_next_generation(tmp_path):
    state, first = _bundle()
    store = ApplicationStore(tmp_path / "saves")
    applicationPath = store.createApplication(first)

    update = state.openTransaction()
    update.set("chat/line/17/semantic", {"body": "changed"})
    update.commit()
    second = first.nextGeneration(committedState=state)

    generationPath = store.publish(second)

    assert generationPath == applicationPath / "generations" / "00000002.bundle"
    assert (applicationPath / "generations" / "00000001.bundle").read_bytes() == first.toBytes()
    assert generationPath.read_bytes() == second.toBytes()

    current = json.loads((applicationPath / "current").read_text(encoding="utf-8"))
    assert current["generation"] == 2
    assert current["saveBundleId"] == first.saveBundleId

    loaded = store.load(appPackId="test.app", applicationId="application-1")
    assert loaded.bundle.generation == 2
    restored = loaded.bundle.restoreCommittedState()
    assert restored.load("chat/line/17/semantic") == {"body": "changed"}
    assert restored.revisionId("chat/line/17/semantic") == 2


def test_application_store_rejects_nonsequential_or_wrong_identity_publication(tmp_path):
    state, first = _bundle()
    store = ApplicationStore(tmp_path / "saves")
    store.createApplication(first)

    second = first.nextGeneration(committedState=state)
    third = second.nextGeneration(committedState=state)
    with pytest.raises(ValueError, match="advance exactly one generation"):
        store.publish(third)

    wrongState = CommittedValueLayer()
    wrong = SaveBundle.create(
        appPackId="other.app",
        applicationId="application-1",
        committedState=wrongState,
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        store.publish(wrong)


def test_application_store_falls_back_to_previous_valid_generation_without_rewriting(tmp_path):
    state, first = _bundle()
    store = ApplicationStore(tmp_path / "saves")
    applicationPath = store.createApplication(first)

    update = state.openTransaction()
    update.set("chat/line/17/semantic", {"body": "generation-two"})
    update.commit()
    second = first.nextGeneration(committedState=state)
    secondPath = store.publish(second)

    currentBefore = (applicationPath / "current").read_bytes()
    firstBefore = (applicationPath / "generations" / "00000001.bundle").read_bytes()
    secondPath.write_bytes(b"{not-valid-json")

    loaded = store.load(appPackId="test.app", applicationId="application-1")

    assert loaded.bundle.generation == 1
    assert loaded.recoveredFromGeneration == 2
    assert loaded.bundle.restoreCommittedState().load("chat/line/17/semantic") == {"body": "hello"}

    assert (applicationPath / "current").read_bytes() == currentBefore
    assert (applicationPath / "generations" / "00000001.bundle").read_bytes() == firstBefore
    assert secondPath.read_bytes() == b"{not-valid-json"


def test_application_store_never_overwrites_generation_published_by_racing_writer(
    tmp_path,
    monkeypatch,
):
    state, first = _bundle()
    store = ApplicationStore(tmp_path / "saves")
    applicationPath = store.createApplication(first)

    update = state.openTransaction()
    update.set("chat/line/17/semantic", {"body": "candidate-two"})
    update.commit()
    second = first.nextGeneration(committedState=state)

    generationPath = applicationPath / "generations" / "00000002.bundle"
    competingPayload = b"already-published-by-other-writer"
    originalWrite = store._writeFileDurably

    def raceAfterExistenceCheck(path, payload):
        if path.name == ".00000002.bundle.tmp":
            generationPath.write_bytes(competingPayload)
        originalWrite(path, payload)

    monkeypatch.setattr(store, "_writeFileDurably", raceAfterExistenceCheck)

    with pytest.raises(FileExistsError):
        store.publish(second)

    assert generationPath.read_bytes() == competingPayload


def test_application_store_creation_failure_does_not_publish_application_directory(tmp_path, monkeypatch):
    _state, bundle = _bundle(applicationId="application-failure")
    store = ApplicationStore(tmp_path / "saves")
    target = store.applicationPath(
        appPackId="test.app",
        applicationId="application-failure",
    )

    originalWrite = store._writeFileDurably
    calls = 0

    def failSecondWrite(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated publication failure")
        originalWrite(path, payload)

    monkeypatch.setattr(store, "_writeFileDurably", failSecondWrite)

    with pytest.raises(OSError, match="simulated publication failure"):
        store.createApplication(bundle)

    assert not target.exists()
    assert not (target.parent / ".creating-application-failure").exists()


def test_application_store_rejects_path_traversal_identity(tmp_path):
    store = ApplicationStore(tmp_path / "saves")

    with pytest.raises(ValueError, match="one filesystem path segment"):
        store.applicationPath(appPackId="../escape", applicationId="application-1")
    with pytest.raises(ValueError, match="one filesystem path segment"):
        store.applicationPath(appPackId="test.app", applicationId=r"..\\escape")
