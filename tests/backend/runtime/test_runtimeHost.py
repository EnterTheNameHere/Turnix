# file: tests/backend/runtime/test_runtimeHost.py ; version: 8
from pathlib import Path

import pytest

from backend.application import ApplicationRunState
from backend.context import CodeEntryIdentity
from backend.registration import RegistrationScope
from backend.runtime.runtimeHost import RuntimeHost
from backend.save import ApplicationStore, SaveBundle
from backend.values import MISSING, ValueState


class RaisingTracer:
    def emitEvent(self, **_kwargs):
        raise RuntimeError("trace destination failed")

    def close(self):
        raise RuntimeError("trace close failed")


def test_application_run_is_non_restartable_and_requires_active_work():
    host = RuntimeHost(appPackId="test.app")
    assert host.applicationRun.state is ApplicationRunState.CREATED

    with pytest.raises(RuntimeError):
        host.runJob("missing@1")

    host.start()
    assert host.applicationRun.state is ApplicationRunState.ACTIVE
    host.stop()
    assert host.applicationRun.state is ApplicationRunState.STOPPED

    with pytest.raises(RuntimeError):
        host.start()


def test_runtime_config_is_detached_from_caller_and_public_snapshots():
    source = {"nested": {"value": 1}}
    host = RuntimeHost(appPackId="test.app", config=source)
    source["nested"]["value"] = 2
    assert host.config == {"nested": {"value": 1}}

    snapshot = host.config
    snapshot["nested"]["value"] = 3
    assert host.config == {"nested": {"value": 1}}


def test_non_activation_context_rejects_registration():
    host = RuntimeHost(appPackId="test.app")
    host.start()
    identity = CodeEntryIdentity(
        applicationId=host.applicationRun.application.applicationId,
        applicationRunId=host.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="entry-instance",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    scope = RegistrationScope()
    context = host.createContext(identity=identity, packRoot=Path.cwd(), registrationScope=scope)
    try:
        with pytest.raises(RuntimeError):
            context.capabilities.register("test.capability@1", lambda ctx, payload: None)
    finally:
        context.invalidate()
        scope.withdraw()
        host.stop()


def test_trace_publication_failure_does_not_change_runtime_lifecycle():
    host = RuntimeHost(appPackId="test.app", tracer=RaisingTracer())

    host.start()
    assert host.applicationRun.state is ApplicationRunState.ACTIVE
    assert host.trace("test") is False

    host.stop()
    assert host.applicationRun.state is ApplicationRunState.STOPPED



def test_contexts_share_application_run_authoritative_memory():
    host = RuntimeHost(appPackId="test.app")
    host.start()
    identity = CodeEntryIdentity(
        applicationId=host.applicationRun.application.applicationId,
        applicationRunId=host.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="entry-instance",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )

    firstScope = RegistrationScope()
    first = host.createContext(
        identity=identity,
        packRoot=Path.cwd(),
        registrationScope=firstScope,
    )
    transaction = first.memory.openTransaction()
    transaction.set("test/value", {"count": 1})
    transaction.commit()
    first.invalidate()
    firstScope.withdraw()

    secondScope = RegistrationScope()
    second = host.createContext(
        identity=identity,
        packRoot=Path.cwd(),
        registrationScope=secondScope,
    )
    try:
        assert second.memory.load("test/value") == {"count": 1}
        assert second.memory.state("test/value") is ValueState.PRESENT
        assert second.memory.revisionId("test/value") == 1
    finally:
        second.invalidate()
        secondScope.withdraw()
        host.stop()



def test_save_bundle_rehydrates_same_application_into_new_run():
    firstHost = RuntimeHost(appPackId="test.app")
    firstHost.start()
    try:
        firstApplicationId = firstHost.applicationRun.application.applicationId
        firstRunId = firstHost.applicationRun.applicationRunId

        transaction = firstHost.applicationRun.committedState.openTransaction()
        transaction.set("chat/line/17/semantic", {"body": "hello"})
        transaction.setAbsent("chat/line/18/semantic")
        transaction.commit()

        bundle = firstHost.captureSaveBundle()
        assert bundle.appPackId == "test.app"
        assert bundle.applicationId == firstApplicationId
        assert bundle.generation == 1
        assert firstHost.applicationRun.saveBundleId == bundle.saveBundleId
    finally:
        firstHost.stop()

    persisted = SaveBundle.fromBytes(bundle.toBytes())
    secondHost = RuntimeHost(saveBundle=persisted)

    assert secondHost.applicationRun.application.applicationId == firstApplicationId
    assert secondHost.applicationRun.applicationRunId != firstRunId
    assert secondHost.applicationRun.saveBundleId == bundle.saveBundleId

    secondHost.start()
    try:
        state = secondHost.applicationRun.committedState
        assert state.load("chat/line/17/semantic") == {"body": "hello"}
        assert state.revisionId("chat/line/17/semantic") == 1
        assert state.state("chat/line/18/semantic") is ValueState.ABSENT
        assert state.revisionId("chat/line/18/semantic") == 1

        update = state.openTransaction()
        update.set("chat/line/17/semantic", {"body": "changed"})
        update.commit()

        secondBundle = secondHost.captureSaveBundle()
        assert secondBundle.saveBundleId == bundle.saveBundleId
        assert secondBundle.applicationId == firstApplicationId
        assert secondBundle.generation == 2
    finally:
        secondHost.stop()

    thirdHost = RuntimeHost(saveBundle=SaveBundle.fromBytes(secondBundle.toBytes()))
    assert thirdHost.applicationRun.application.applicationId == firstApplicationId
    assert thirdHost.applicationRun.applicationRunId not in {firstRunId, secondHost.applicationRun.applicationRunId}
    assert thirdHost.applicationRun.committedState.load("chat/line/17/semantic") == {"body": "changed"}
    assert thirdHost.applicationRun.committedState.revisionId("chat/line/17/semantic") == 2


def test_runtime_host_rejects_application_and_save_bundle_together():
    source = RuntimeHost(appPackId="test.app")
    bundle = source.captureSaveBundle()

    with pytest.raises(ValueError, match="either application or saveBundle"):
        RuntimeHost(
            application=source.applicationRun.application,
            saveBundle=bundle,
        )



def test_capability_memory_write_nests_under_supplied_transaction():
    host = RuntimeHost(appPackId="test.app")
    host.start()
    identity = CodeEntryIdentity(
        applicationId=host.applicationRun.application.applicationId,
        applicationRunId=host.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="entry-instance",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    host.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()

    def handler(ctx, _payload):
        child = ctx.memory.openTransaction()
        child.set("test/nested/value", {"count": 1})
        child.commit()
        return ctx.memory.load("test/nested/value")

    host.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.memorywrite@1",
        handler=handler,
    )
    scope.publish()

    outer = host.applicationRun.committedState.openTransaction()
    outerCommitted = False
    try:
        result = host.invokeCapability(
            "test.memorywrite@1",
            memoryView=outer,
        )

        assert result == {"count": 1}
        assert outer.load("test/nested/value") == {"count": 1}
        assert host.applicationRun.committedState.load("test/nested/value") is MISSING
        assert host.applicationRun.committedState.revisionId("test/nested/value") == 0

        outer.commit()
        outerCommitted = True

        assert host.applicationRun.committedState.load("test/nested/value") == {"count": 1}
        assert host.applicationRun.committedState.revisionId("test/nested/value") == 1
    finally:
        if not outerCommitted:
            outer.abort()
        scope.withdraw()
        host.unregisterCodeEntry(identity.codeEntryInstanceId)
        host.stop()



def test_runtime_host_persists_and_loads_application_through_filesystem_store(tmp_path):
    store = ApplicationStore(tmp_path / "saves")
    firstHost = RuntimeHost(
        appPackId="test.app",
        applicationStore=store,
    )
    firstApplicationId = firstHost.applicationRun.application.applicationId
    firstRunId = firstHost.applicationRun.applicationRunId

    root = firstHost.applicationRun.committedState
    transaction = root.openTransaction()
    transaction.set(
        "chat/line/17/semantic",
        {"body": "hello"},
        metadata={
            "producer": {"implementationId": "chat-semantics"},
            "validity": {"rawLine": "viewer: hello"},
        },
    )
    transaction.commit()

    firstBundle = firstHost.saveApplication()
    assert firstBundle.generation == 1
    assert (
        tmp_path
        / "saves"
        / "test.app"
        / firstApplicationId
        / "generations"
        / "00000001.bundle"
    ).is_file()

    del firstHost

    secondHost, loaded = RuntimeHost.loadApplication(
        applicationStore=store,
        appPackId="test.app",
        applicationId=firstApplicationId,
    )

    assert loaded.bundle.generation == 1
    assert loaded.recoveredFromGeneration is None
    assert secondHost.applicationRun.application.appPackId == "test.app"
    assert secondHost.applicationRun.application.applicationId == firstApplicationId
    assert secondHost.applicationRun.applicationRunId != firstRunId
    assert secondHost.applicationRun.committedState.load("chat/line/17/semantic") == {
        "body": "hello"
    }
    assert secondHost.applicationRun.committedState.metadata(
        "chat/line/17/semantic"
    ) == {
        "producer": {"implementationId": "chat-semantics"},
        "validity": {"rawLine": "viewer: hello"},
    }

    update = secondHost.applicationRun.committedState.openTransaction()
    update.set("chat/line/17/semantic", {"body": "changed"})
    update.commit()
    secondBundle = secondHost.saveApplication()

    assert secondBundle.saveBundleId == firstBundle.saveBundleId
    assert secondBundle.generation == 2

    del secondHost

    thirdHost, loadedAgain = RuntimeHost.loadApplication(
        applicationStore=ApplicationStore(tmp_path / "saves"),
        appPackId="test.app",
        applicationId=firstApplicationId,
    )

    assert loadedAgain.bundle.generation == 2
    assert thirdHost.applicationRun.committedState.load(
        "chat/line/17/semantic"
    ) == {"body": "changed"}
    assert thirdHost.applicationRun.committedState.revisionId(
        "chat/line/17/semantic"
    ) == 2


def test_runtime_host_failed_publication_does_not_advance_accepted_generation(
    tmp_path,
    monkeypatch,
):
    store = ApplicationStore(tmp_path / "saves")
    host = RuntimeHost(
        appPackId="test.app",
        applicationStore=store,
    )

    first = host.saveApplication()
    assert first.generation == 1

    transaction = host.applicationRun.committedState.openTransaction()
    transaction.set("test/value", 1)
    transaction.commit()

    originalPublish = store.publish

    def failPublish(_bundle):
        raise OSError("simulated durable publication failure")

    monkeypatch.setattr(store, "publish", failPublish)

    with pytest.raises(OSError, match="simulated durable publication failure"):
        host.saveApplication()

    assert host.applicationRun.saveBundleId == first.saveBundleId

    monkeypatch.setattr(store, "publish", originalPublish)
    second = host.saveApplication()

    assert second.generation == 2
    assert second.saveBundleId == first.saveBundleId
    loaded = store.load(
        appPackId="test.app",
        applicationId=host.applicationRun.application.applicationId,
    )
    assert loaded.bundle.generation == 2
    assert loaded.bundle.restoreCommittedState().load("test/value") == 1


def test_runtime_host_requires_app_pack_identity_for_new_application():
    with pytest.raises(ValueError, match="requires appPackId"):
        RuntimeHost()
