# file: tests/backend/application/test_applicationRuntime.py ; version: 10
from pathlib import Path

import pytest

from backend.application import ApplicationRunState
from backend.context import CodeEntryIdentity
from backend.registration import RegistrationScope
from backend.orchestration import JobState
from backend.packs.runtime import PackResolver
from backend.application.applicationRuntime import ApplicationRuntime
from backend.save import ApplicationStore, SaveBundle
from backend.values import MISSING, ValueState


class RaisingTracer:
    def emitEvent(self, **_kwargs):
        raise RuntimeError("trace destination failed")

    def close(self):
        raise RuntimeError("trace close failed")


def test_application_runtime_owns_one_pack_loader_for_its_lifetime():
    resolver = PackResolver(roots=())
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=resolver,
    )

    loader = runtime.packLoader

    assert runtime.packLoader is loader

    runtime.close()
    runtime.close()


def test_application_run_is_non_restartable_and_requires_active_work():
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))
    assert host.applicationRun.state is ApplicationRunState.CREATED

    with pytest.raises(RuntimeError):
        host.runJob("missing@1")

    host.start()
    assert host.applicationRun.state is ApplicationRunState.ACTIVE
    host.close()
    assert host.applicationRun.state is ApplicationRunState.STOPPED

    with pytest.raises(RuntimeError):
        host.start()


def test_runtime_config_is_detached_from_caller_and_public_snapshots():
    source = {"nested": {"value": 1}}
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()), config=source)
    source["nested"]["value"] = 2
    assert host.config == {"nested": {"value": 1}}

    snapshot = host.config
    snapshot["nested"]["value"] = 3
    assert host.config == {"nested": {"value": 1}}


def test_non_activation_context_rejects_registration():
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))
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
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()), tracer=RaisingTracer())

    host.start()
    assert host.applicationRun.state is ApplicationRunState.ACTIVE
    assert host.trace("test") is False

    host.close()
    assert host.applicationRun.state is ApplicationRunState.STOPPED



def test_application_physically_owns_authoritative_memory():
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))

    assert host.applicationRun.application.committedState is host.applicationRun.application.committedState

    transaction = host.applicationRun.application.committedState.openTransaction()
    transaction.set("test/application-owned", {"value": 1})
    transaction.commit()

    assert host.applicationRun.application.committedState.load("test/application-owned") == {"value": 1}


def test_contexts_share_application_run_authoritative_memory():
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))
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
    firstHost = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))
    firstHost.start()
    try:
        firstApplicationId = firstHost.applicationRun.application.applicationId
        firstRunId = firstHost.applicationRun.applicationRunId

        transaction = firstHost.applicationRun.application.committedState.openTransaction()
        transaction.set("chat/line/17/semantic", {"body": "hello"})
        transaction.setAbsent("chat/line/18/semantic")
        transaction.commit()

        bundle = firstHost.captureSaveBundle()
        assert bundle.appPackId == "test.app"
        assert bundle.applicationId == firstApplicationId
        assert bundle.generation == 1
        assert firstHost.applicationRun.application.saveBundleId == bundle.saveBundleId
        assert firstHost.applicationRun.application.saveBundleId == bundle.saveBundleId
    finally:
        firstHost.stop()

    persisted = SaveBundle.fromBytes(bundle.toBytes())
    secondHost = ApplicationRuntime(saveBundle=persisted, packResolver=PackResolver(roots=()))

    assert secondHost.applicationRun.application.applicationId == firstApplicationId
    assert secondHost.applicationRun.applicationRunId != firstRunId
    assert secondHost.applicationRun.application.saveBundleId == bundle.saveBundleId
    assert secondHost.applicationRun.application.saveBundleId == bundle.saveBundleId
    assert (
        secondHost.applicationRun.application.committedState
        is secondHost.applicationRun.application.committedState
    )

    secondHost.start()
    try:
        state = secondHost.applicationRun.application.committedState
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

    thirdHost = ApplicationRuntime(saveBundle=SaveBundle.fromBytes(secondBundle.toBytes()), packResolver=PackResolver(roots=()))
    assert thirdHost.applicationRun.application.applicationId == firstApplicationId
    assert thirdHost.applicationRun.applicationRunId not in {firstRunId, secondHost.applicationRun.applicationRunId}
    assert thirdHost.applicationRun.application.committedState.load("chat/line/17/semantic") == {"body": "changed"}
    assert thirdHost.applicationRun.application.committedState.revisionId("chat/line/17/semantic") == 2


def test_application_runtime_rejects_application_and_save_bundle_together():
    source = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))
    bundle = source.captureSaveBundle()

    with pytest.raises(ValueError, match="either application or saveBundle"):
        ApplicationRuntime(
            application=source.applicationRun.application,
            saveBundle=bundle,
            packResolver=PackResolver(roots=()),
        )



def test_capability_memory_write_nests_under_supplied_transaction():
    host = ApplicationRuntime(appPackId="test.app", packResolver=PackResolver(roots=()))
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

    outer = host.applicationRun.application.committedState.openTransaction()
    outerCommitted = False
    try:
        result = host.invokeCapability(
            "test.memorywrite@1",
            memoryView=outer,
        )

        assert result == {"count": 1}
        assert outer.load("test/nested/value") == {"count": 1}
        assert host.applicationRun.application.committedState.load("test/nested/value") is MISSING
        assert host.applicationRun.application.committedState.revisionId("test/nested/value") == 0

        outer.commit()
        outerCommitted = True

        assert host.applicationRun.application.committedState.load("test/nested/value") == {"count": 1}
        assert host.applicationRun.application.committedState.revisionId("test/nested/value") == 1
    finally:
        if not outerCommitted:
            outer.abort()
        scope.withdraw()
        host.unregisterCodeEntry(identity.codeEntryInstanceId)
        host.stop()



def test_application_runtime_persists_and_loads_application_through_filesystem_store(tmp_path):
    store = ApplicationStore(tmp_path / "saves")
    firstHost = ApplicationRuntime(
        appPackId="test.app",
        applicationStore=store,
        packResolver=PackResolver(roots=()),
    )
    firstApplicationId = firstHost.applicationRun.application.applicationId
    firstRunId = firstHost.applicationRun.applicationRunId

    root = firstHost.applicationRun.application.committedState
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

    secondHost, loaded = ApplicationRuntime.loadApplication(
        applicationStore=store,
        packResolver=PackResolver(roots=()),
        appPackId="test.app",
        applicationId=firstApplicationId,
    )

    assert loaded.bundle.generation == 1
    assert loaded.recoveredFromGeneration is None
    assert secondHost.applicationRun.application.appPackId == "test.app"
    assert secondHost.applicationRun.application.applicationId == firstApplicationId
    assert secondHost.applicationRun.applicationRunId != firstRunId
    assert secondHost.applicationRun.application.committedState.load("chat/line/17/semantic") == {
        "body": "hello"
    }
    assert secondHost.applicationRun.application.committedState.metadata(
        "chat/line/17/semantic"
    ) == {
        "producer": {"implementationId": "chat-semantics"},
        "validity": {"rawLine": "viewer: hello"},
    }

    update = secondHost.applicationRun.application.committedState.openTransaction()
    update.set("chat/line/17/semantic", {"body": "changed"})
    update.commit()
    secondBundle = secondHost.saveApplication()

    assert secondBundle.saveBundleId == firstBundle.saveBundleId
    assert secondBundle.generation == 2

    del secondHost

    thirdHost, loadedAgain = ApplicationRuntime.loadApplication(
        applicationStore=ApplicationStore(tmp_path / "saves"),
        packResolver=PackResolver(roots=()),
        appPackId="test.app",
        applicationId=firstApplicationId,
    )

    assert loadedAgain.bundle.generation == 2
    assert thirdHost.applicationRun.application.committedState.load(
        "chat/line/17/semantic"
    ) == {"body": "changed"}
    assert thirdHost.applicationRun.application.committedState.revisionId(
        "chat/line/17/semantic"
    ) == 2


def test_application_runtime_skips_orphan_generation_left_before_current_pointer_update(
    tmp_path,
):
    store = ApplicationStore(tmp_path / "saves")
    firstHost = ApplicationRuntime(
        appPackId="test.app",
        applicationStore=store,
        packResolver=PackResolver(roots=()),
    )
    applicationId = firstHost.applicationRun.application.applicationId
    first = firstHost.saveApplication()

    stagedState = first.restoreCommittedState()
    stagedUpdate = stagedState.openTransaction()
    stagedUpdate.set("test/orphan", "must-not-be-restored")
    stagedUpdate.commit()
    orphan = first.nextGeneration(committedState=stagedState)

    applicationPath = store.applicationPath(
        appPackId="test.app",
        applicationId=applicationId,
    )
    orphanPath = applicationPath / "generations" / "00000002.bundle"
    orphanPath.write_bytes(orphan.toBytes())
    orphanEvidence = orphanPath.read_bytes()

    loadedHost, loaded = ApplicationRuntime.loadApplication(
        applicationStore=store,
        packResolver=PackResolver(roots=()),
        appPackId="test.app",
        applicationId=applicationId,
    )

    assert loaded.bundle.generation == 1
    assert loaded.recoveredFromGeneration is None
    assert loaded.durableGeneration == 2
    assert loadedHost.applicationRun.application.durableGeneration == 2
    assert loadedHost.applicationRun.application.committedState.load("test/orphan") is MISSING

    update = loadedHost.applicationRun.application.committedState.openTransaction()
    update.set("test/recovered", "generation-three")
    update.commit()

    third = loadedHost.saveApplication()

    assert third.generation == 3
    assert orphanPath.read_bytes() == orphanEvidence
    assert (applicationPath / "generations" / "00000003.bundle").read_bytes() == third.toBytes()

    loadedAgain = store.load(
        appPackId="test.app",
        applicationId=applicationId,
    )
    assert loadedAgain.bundle.generation == 3
    restored = loadedAgain.bundle.restoreCommittedState()
    assert restored.load("test/orphan") is MISSING
    assert restored.load("test/recovered") == "generation-three"


def test_application_runtime_can_save_after_recovering_from_corrupt_current_generation(
    tmp_path,
):
    store = ApplicationStore(tmp_path / "saves")
    firstHost = ApplicationRuntime(
        appPackId="test.app",
        applicationStore=store,
        packResolver=PackResolver(roots=()),
    )
    applicationId = firstHost.applicationRun.application.applicationId

    first = firstHost.saveApplication()
    transaction = firstHost.applicationRun.application.committedState.openTransaction()
    transaction.set("test/value", "generation-two")
    transaction.commit()
    second = firstHost.saveApplication()
    assert second.generation == 2

    applicationPath = store.applicationPath(
        appPackId="test.app",
        applicationId=applicationId,
    )
    corruptSecond = applicationPath / "generations" / "00000002.bundle"
    corruptSecond.write_bytes(b"{corrupt-generation-two")
    corruptEvidence = corruptSecond.read_bytes()

    recoveredHost, loaded = ApplicationRuntime.loadApplication(
        applicationStore=store,
        packResolver=PackResolver(roots=()),
        appPackId="test.app",
        applicationId=applicationId,
    )

    assert loaded.bundle.generation == 1
    assert loaded.recoveredFromGeneration == 2
    assert recoveredHost.applicationRun.application.durableGeneration == 2

    update = recoveredHost.applicationRun.application.committedState.openTransaction()
    update.set("test/value", "recovered-generation-three")
    update.commit()

    third = recoveredHost.saveApplication()

    assert third.generation == 3
    assert corruptSecond.read_bytes() == corruptEvidence
    assert (applicationPath / "generations" / "00000003.bundle").read_bytes() == third.toBytes()

    loadedAgain = store.load(
        appPackId="test.app",
        applicationId=applicationId,
    )
    assert loadedAgain.recoveredFromGeneration is None
    assert loadedAgain.bundle.generation == 3
    assert loadedAgain.bundle.restoreCommittedState().load("test/value") == "recovered-generation-three"


def test_application_runtime_failed_publication_does_not_advance_accepted_generation(
    tmp_path,
    monkeypatch,
):
    store = ApplicationStore(tmp_path / "saves")
    host = ApplicationRuntime(
        appPackId="test.app",
        applicationStore=store,
        packResolver=PackResolver(roots=()),
    )

    first = host.saveApplication()
    assert first.generation == 1

    transaction = host.applicationRun.application.committedState.openTransaction()
    transaction.set("test/value", 1)
    transaction.commit()

    originalPublish = store.publish

    def failPublish(_bundle):
        raise OSError("simulated durable publication failure")

    monkeypatch.setattr(store, "publish", failPublish)

    with pytest.raises(OSError, match="simulated durable publication failure"):
        host.saveApplication()

    assert host.applicationRun.application.saveBundleId == first.saveBundleId

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


def test_application_runtime_requires_app_pack_identity_for_new_application():
    with pytest.raises(ValueError, match="requires appPackId"):
        ApplicationRuntime(packResolver=PackResolver(roots=()))


def test_run_job_commits_capability_mutation_only_after_successful_unit_completion():
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="job-entry",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()

    def handler(ctx, _payload):
        transaction = ctx.memory.openTransaction()
        transaction.set("test/job/value", {"accepted": True})
        transaction.commit()
        assert runtime.applicationRun.application.committedState.load("test/job/value") is MISSING
        return "ok"

    runtime.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.job@1",
        handler=handler,
    )
    scope.publish()

    try:
        job = runtime.runJob("test.job@1")

        assert job.state is JobState.SUCCEEDED
        assert job.authoritativeStateAccepted is True
        assert job.result == "ok"
        assert runtime.applicationRun.application.committedState.load("test/job/value") == {
            "accepted": True,
        }
        assert runtime.applicationRun.application.committedState.revisionId("test/job/value") == 1
    finally:
        scope.withdraw()
        runtime.unregisterCodeEntry(identity.codeEntryInstanceId)
        runtime.close()


def test_run_job_failure_discards_capability_mutation_from_authoritative_root():
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="failing-job-entry",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()

    def handler(ctx, _payload):
        transaction = ctx.memory.openTransaction()
        transaction.set("test/job/value", "must-disappear")
        transaction.commit()
        raise RuntimeError("job exploded")

    runtime.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.job.fail@1",
        handler=handler,
    )
    scope.publish()

    try:
        job = runtime.runJob("test.job.fail@1")

        assert job.state is JobState.FAILED
        assert job.authoritativeStateAccepted is False
        assert isinstance(job.error, RuntimeError)
        assert str(job.error) == "job exploded"
        assert runtime.applicationRun.application.committedState.load("test/job/value") is MISSING
        assert runtime.applicationRun.application.committedState.revisionId("test/job/value") == 0
    finally:
        scope.withdraw()
        runtime.unregisterCodeEntry(identity.codeEntryInstanceId)
        runtime.close()


def test_run_job_stages_managed_io_until_after_state_acceptance(tmp_path):
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="io-job-entry",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()
    output = tmp_path / "job-result.json"

    def handler(ctx, _payload):
        transaction = ctx.memory.openTransaction()
        transaction.set("test/job/io", "accepted")
        transaction.commit()
        ctx.io.writeJsonAtomic(output, {"saved": True})

        assert output.exists() is False
        assert runtime.applicationRun.application.committedState.load("test/job/io") is MISSING
        return "ok"

    runtime.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.job.io@1",
        handler=handler,
    )
    scope.publish()

    try:
        job = runtime.runJob("test.job.io@1")

        assert job.state is JobState.SUCCEEDED
        assert runtime.applicationRun.application.committedState.load("test/job/io") == "accepted"
        assert output.exists() is True
    finally:
        scope.withdraw()
        runtime.unregisterCodeEntry(identity.codeEntryInstanceId)
        runtime.close()


def test_run_job_failure_aborts_staged_managed_io(tmp_path):
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="io-failing-job-entry",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()
    output = tmp_path / "job-result.json"

    def handler(ctx, _payload):
        transaction = ctx.memory.openTransaction()
        transaction.set("test/job/io", "must-disappear")
        transaction.commit()
        ctx.io.writeJsonAtomic(output, {"saved": False})
        raise RuntimeError("fail after staging output")

    runtime.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.job.io.fail@1",
        handler=handler,
    )
    scope.publish()

    try:
        job = runtime.runJob("test.job.io.fail@1")

        assert job.state is JobState.FAILED
        assert output.exists() is False
        assert runtime.applicationRun.application.committedState.load("test/job/io") is MISSING
    finally:
        scope.withdraw()
        runtime.unregisterCodeEntry(identity.codeEntryInstanceId)
        runtime.close()


def test_run_job_reports_state_accepted_when_managed_io_publication_fails_after_commit(
    tmp_path,
    monkeypatch,
):
    runtime = ApplicationRuntime(
        appPackId="test.app",
        packResolver=PackResolver(roots=()),
    )
    runtime.start()
    identity = CodeEntryIdentity(
        applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId,
        packId="test.pack",
        packVersion="1.0.0",
        codeEntryId="entry",
        codeEntryInstanceId="io-publication-failure-entry",
        sourceSha256="source-sha",
        implementationFormat="python-source@1",
        implementationId="implementation-sha",
    )
    runtime.registerCodeEntry(identity, Path.cwd())
    scope = RegistrationScope()
    output = tmp_path / "result.json"

    def handler(ctx, _payload):
        transaction = ctx.memory.openTransaction()
        transaction.set("test/job/state", "accepted-before-io")
        transaction.commit()
        ctx.io.writeJsonAtomic(output, {"published": False})
        return "result-before-io-failure"

    runtime.capabilities.register(
        scope,
        ownerId=identity.codeEntryInstanceId,
        capabilityId="test.job.io.publication-fail@1",
        handler=handler,
    )
    scope.publish()

    originalOpen = runtime.io.openTransaction

    def openFailingIoTransaction():
        transaction = originalOpen()

        def failCommit():
            raise RuntimeError("simulated managed I/O publication failure")

        monkeypatch.setattr(transaction, "commit", failCommit)
        return transaction

    monkeypatch.setattr(runtime.io, "openTransaction", openFailingIoTransaction)

    try:
        job = runtime.runJob("test.job.io.publication-fail@1")

        assert job.state is JobState.FAILED
        assert job.authoritativeStateAccepted is True
        assert isinstance(job.error, RuntimeError)
        assert str(job.error) == "simulated managed I/O publication failure"
        assert runtime.applicationRun.application.committedState.load(
            "test/job/state"
        ) == "accepted-before-io"
        assert output.exists() is False
    finally:
        scope.withdraw()
        runtime.unregisterCodeEntry(identity.codeEntryInstanceId)
        runtime.close()
