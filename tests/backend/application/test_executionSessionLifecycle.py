# file: tests/backend/application/test_executionSessionLifecycle.py ; version: 1
"""Real facade/runtime/Job/host lifetime with synthetic controlled provider effects."""
import gc
import json
import weakref
from pathlib import Path
from threading import Event

import pytest

from backend.application.applicationRuntime import ApplicationRuntime
from backend.application.lifecycle import ApplicationLifecycle
from backend.context import CodeEntryIdentity
from backend.llm.errors import LlmExecutionOwnershipError, LlmExecutionCleanupError
from backend.llm.executionSessions import FreshContext, ReuseContext, LlmSessionCleanup, lifetimeBoundary
from backend.orchestration import Job, JobState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.registration import RegistrationScope
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.runtime.executionResources import ResourceState
from backend.values import MISSING
from backend.observation import ObservationRequest
from backend.llm.streamObservation import StreamOutcome
from tests.backend.llm.test_executionSessions import fixture, background, Residency, QUERY, OK, UNKNOWN


def identity(runtime, name):
    """Builds real registered identity evidence for each test CodeEntry incarnation."""
    return CodeEntryIdentity(applicationId=runtime.applicationRun.application.applicationId,
        applicationRunId=runtime.applicationRun.applicationRunId, packId="test", packVersion="1",
        codeEntryId=name, codeEntryInstanceId=name, sourceSha256="sha", implementationFormat="python-source@1",
        implementationId="sha")


def setupRuntime(runtime=None, *, domain=None, residency=None):
    """Registers actual runtime owners before trusted host binding, with no Pack context reuse."""
    runtime = runtime or ApplicationRuntime(appPackId="test", packResolver=PackResolver(roots=()))
    if not runtime.applicationRun.active:
        runtime.start()
    for name in ("caller", "helper", "provider-owner"):
        runtime.registerCodeEntry(identity(runtime, name), Path.cwd())
    values = fixture(registry=runtime.llmProviders,
                     owners=(runtime._llmOwners["caller"], runtime._llmOwners["helper"]),
                     domain=domain, residency=residency)
    return runtime, values


def register(runtime, name, handler, owner="caller"):
    """Publishes one real capability so production invocation/cancellation paths run."""
    scope = RegistrationScope()
    runtime.capabilities.register(scope, ownerId=owner, capabilityId=name, handler=handler)
    scope.publish()
    return scope


def test_fresh_invocations_can_reuse_run_owned_handles_without_retaining_context():
    """A saved handle survives one turn; its owner must supply a fresh Context next turn."""
    runtime, values = setupRuntime()
    _, service, domain, binding, adapter, _, _, _, _, _, _, _ = values
    state = {}
    contexts = []

    def first(ctx, payload):
        """Creates an owned reservation and retained resource in the actual facade."""
        contexts.append(weakref.ref(ctx))
        profile = ctx.llm.profile(providerName="controlled")
        state["session"] = ctx.llm.reserveConversation(profile)
        state["handle"] = ctx.llm.run(providerName="controlled", query=QUERY,
            session=state["session"], context=FreshContext(retain=True)).retainedContext

    def later(ctx, payload):
        """Uses a new invocation of the same owner, with no retained old facade."""
        return ctx.llm.run(providerName="controlled", query=QUERY,
                           session=state["session"], context=ReuseContext(state["handle"]))

    register(runtime, "first@1", first)
    register(runtime, "later@1", later)
    assert runtime.runJob("first@1").state is JobState.SUCCEEDED
    gc.collect()
    assert contexts[0]() is None
    assert runtime.runJob("later@1").state is JobState.SUCCEEDED
    assert len(adapter.residency.loads) == 1
    runtime.close()
    assert len(adapter.disposals) == 1 and domain.snapshot.retainedContexts == 0


def test_real_outer_job_abort_invalidates_inner_success_and_rolls_back_memory():
    """Resource receipts follow the real outer Job's mutation disposition."""
    runtime, values = setupRuntime()
    _, _, domain, _, adapter, _, _, _, _, _, _, _ = values
    state = {}

    def fail(ctx, payload):
        """Stages memory and returns provider state before a later capability failure."""
        state["handle"] = ctx.llm.run(providerName="controlled", query=QUERY,
                                     context=FreshContext(retain=True)).retainedContext
        transaction = ctx.memory.openTransaction()
        transaction.set("test/value", "uncommitted")
        transaction.commit()
        raise ValueError("outer failure")

    register(runtime, "fail@1", fail)
    job = runtime.runJob("fail@1")
    assert job.state is JobState.FAILED
    assert state["handle"]._lost
    assert runtime.applicationRun.application.committedState.load("test/value") is MISSING
    assert domain.snapshot.retainedContexts == 0 and len(adapter.disposals) == 1


def test_real_helper_bridge_propagates_parent_cancellation_and_requires_helper_grant():
    """The actual runtime callback bridge forwards the signal, not just facade arguments."""
    runtime, values = setupRuntime()
    _, _, domain, binding, adapter, _, _, helperOwner, _, _, _, _ = values
    entered, proceed = Event(), Event()

    def items(ctx, payload):
        """Returns valid reusable material before query helper estimation."""
        return [{"itemId": "item", "kind": "text", "content": "text", "metadata": {}}]

    def query(ctx, payload):
        """Reads parent cancellation through a newly constructed helper Context."""
        entered.set()
        assert proceed.wait(5)
        ctx.cancellation.raiseIfRequested()
        ctx.llm.estimateInputTokens(providerName="controlled", query=QUERY)
        return {"formatId": "text/plain", "payload": "text"}

    def run(ctx, payload):
        """Invokes the real pipeline capability bridge inside the Job transaction."""
        return ctx.llm.runProcessing(memoryKey="test", inputValue=None, providerName="controlled",
            buildQueryItemsCapabilityId="items@1", buildQueryCapabilityId="query@1")

    register(runtime, "items@1", items, "helper")
    register(runtime, "query@1", query, "helper")
    register(runtime, "run@1", run)
    job = Job.new()
    pending = background(lambda: runtime.runJob("run@1", job=job))
    try:
        assert entered.wait(5)
        job.requestCancellation()
    finally:
        proceed.set()
    assert pending.result(5).state is JobState.CANCELLED
    assert domain.snapshot.operations == 0 and len(adapter.closes) == 1


def test_runtime_close_disposer_and_lease_run_outside_runtime_lock():
    """A second thread must enter the runtime lane before cleanup acknowledges."""
    runtime, values = setupRuntime()
    _, _, domain, binding, adapter, _, _, _, _, _, _, leases = values
    observed = []

    def readRuntime():
        """Requires the real serialization lane from a different thread."""
        with runtime._lane:
            return runtime.applicationRun.applicationRunId

    original = adapter.dispose

    def dispose(payload):
        """Makes a held runtime lock fail the provider cleanup contract."""
        observed.append(background(readRuntime).result(5))
        return original(payload)

    adapter.dispose = dispose

    def retain(ctx, payload):
        """Returns a context whose cleanup is independent of this invocation."""
        return ctx.llm.run(providerName="controlled", query=QUERY, context=FreshContext(retain=True))

    register(runtime, "retain@1", retain)
    runtime.runJob("retain@1")
    runtime.close()
    runtime.close()
    assert len(observed) == 1 and leases == [1]


def hostFixture(tmp_path):
    """Creates a real empty app through the host, then installs trusted fake binding."""
    packs = tmp_path / "packs"
    app = packs / "app"
    app.mkdir(parents=True)
    (app / "manifest.json").write_text(json.dumps({"packId": "test.app", "kind": "appPack",
        "version": "1", "codeEntries": []}), encoding="utf-8")
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"), packResolver=PackResolver(roots=(packs,)))
    host.start()
    runtime = host.createApplication(appPackId="test.app", plan=ManualActivationPlan(packIds=("test.app",)))
    residency = Residency()
    domain = host.executionResources.registerDomain("fake", compatibilityKey=b"fake", adapter=residency)
    runtime, values = setupRuntime(runtime, domain=domain, residency=residency)
    return host, runtime, values


def test_host_removed_runtime_keeps_post_lock_cleanup_queue_and_lease(tmp_path):
    """Host-rooted service retains cleanup after closeApplicationRun removes the runtime."""
    host, runtime, values = hostFixture(tmp_path)
    _, service, _, _, adapter, _, _, _, _, _, _, leases = values
    observed = []
    original = adapter.dispose

    def dispose(payload):
        """Requires host and runtime lane entry from another thread during disposal."""
        def read():
            """Obtains both actual lanes rather than relying on RLock reentry."""
            with runtime._lane:
                return host.activeApplicationRuntimes
        observed.append(background(read).result(5))
        return original(payload)

    adapter.dispose = dispose

    def retain(ctx, payload):
        """Leaves retained derived state between calls, independent of invocation."""
        return ctx.llm.run(providerName="controlled", query=QUERY, context=FreshContext(retain=True))

    register(runtime, "retain@1", retain)
    runtime.runJob("retain@1")
    host.closeApplicationRun(runtime.applicationRun.applicationRunId)
    assert host.activeApplicationRuntimes == () and len(observed) == 1
    assert host.llmExecutions is service and leases == [1]
    host.stop()


def test_host_repeated_shutdown_reports_unknown_after_runtime_removed(tmp_path):
    """STOPPED early returns must not orphan a binding with unknown owned work."""
    host, runtime, values = hostFixture(tmp_path)
    _, _, domain, _, adapter, _, _, _, _, _, _, leases = values
    adapter.cleanup = UNKNOWN

    def run(ctx, payload):
        """Produces a confirmed stream with explicitly uncertain provider cleanup."""
        return ctx.llm.run(providerName="controlled", query=QUERY)

    register(runtime, "run@1", run)
    assert runtime.runJob("run@1").state is JobState.FAILED
    for _ in range(2):
        with pytest.raises((ExceptionGroup, LlmExecutionCleanupError)):
            host.stop()
    assert domain.snapshot.state is ResourceState.UNKNOWN
    assert leases == [] and len(adapter.closes) == 1


def test_creation_context_does_not_get_controlled_execution_authority():
    """Lifecycle/registration Contexts cannot invent normal invocation grants."""
    runtime, values = setupRuntime()
    context = runtime.createContext(identity=identity(runtime, "caller"), packRoot=Path.cwd(),
        registrationScope=RegistrationScope(), allowRegistration=True)
    try:
        with pytest.raises(LlmExecutionOwnershipError):
            context.llm.run(providerName="controlled", query=QUERY)
    finally:
        context.invalidate()
    assert values[4].opens == []


def test_failed_host_creation_retains_and_drains_new_lifetimes_outside_host_lock(tmp_path, monkeypatch):
    """A failed create cannot lose a binding/lease when its runtime was never registered."""
    packs = tmp_path / "packs"
    packs.mkdir()
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"), packResolver=PackResolver(roots=(packs,)))
    host.start()
    observed = []
    holders = []

    def failCreate(*, runtime, plan):
        """Installs a trusted fake after registration, then fails before host publication."""
        runtime, values = setupRuntime(runtime)
        holders.append(values)
        adapter = values[4]
        original = adapter.dispose

        def dispose(payload):
            """Requires host/runtime entry from a different thread during rollback cleanup."""
            def read():
                """Reads both actual lanes, detecting hidden teardown lock retention."""
                with runtime._lane:
                    return host.activeApplicationRuntimes
            observed.append(background(read).result(5))
            return original(payload)

        adapter.dispose = dispose
        runtime.llmPipeline.run(providerName="controlled", query=QUERY,
            executionAccess=runtime._llmOwners["caller"].invocation(), context=FreshContext(retain=True))
        raise ValueError("creation failure")

    monkeypatch.setattr(ApplicationLifecycle, "create", failCreate)
    with pytest.raises(ValueError, match="creation failure"):
        host.createApplication(appPackId="test", plan=ManualActivationPlan(packIds=()))
    assert host.activeApplicationRuntimes == () and observed == [()]
    assert holders[0][-1] == [1]


def test_real_pack_failure_before_publication_does_not_issue_controlled_authority(tmp_path):
    """onLoad cannot turn provisional provider registration into execution ownership."""
    packs = tmp_path / "packs"
    app = packs / "app"
    app.mkdir(parents=True)
    (app / "manifest.json").write_text(json.dumps({"packId": "test.app", "kind": "appPack",
        "version": "1", "codeEntries": [{"id": "entry", "source": "entry.py"}]}), encoding="utf-8")
    # The failure itself happens in onLoad before registration scope publication.
    (app / "entry.py").write_text('''# file: entry.py ; version: 1
def onLoad(ctx):
    """A failing provisional activation owns no normal invocation grant."""
    ctx.llm.profile(providerName="not-published")
''', encoding="utf-8")
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"), packResolver=PackResolver(roots=(packs,)))
    host.start()
    with pytest.raises(LlmExecutionOwnershipError):
        host.createApplication(appPackId="test.app", plan=ManualActivationPlan(packIds=("test.app",)))
    assert host.activeApplicationRuntimes == () and host.llmExecutions._bindings == {}


def test_late_operation_completion_during_legacy_pack_teardown_is_deferred(tmp_path, monkeypatch):
    """A legacy close callback can report completion without invoking adapter hooks under locks."""
    host, runtime, values = hostFixture(tmp_path)
    pipeline, service, domain, binding, adapter, _, _, _, access, _, _, leases = values
    entered, proceed = Event(), Event()
    observed = []

    def hold(session):
        """Leaves a trusted backend call active independently of runtime serialization."""
        entered.set()
        assert proceed.wait(5)

    def finalize(admission, session, reason):
        """Demands second-thread read access before adapter cleanup can confirm stop."""
        def read():
            """Proves both host and runtime lanes were released before this callback."""
            with runtime._lane:
                return host.activeApplicationRuntimes
        observed.append(background(read).result(5))
        return LlmSessionCleanup(OK)

    adapter.onStream, adapter.onClose = hold, finalize
    pending = background(lambda: pipeline.run(providerName="controlled", query=QUERY, executionAccess=access))

    def legacyClose():
        """Reports a late completion while still inside legacy runtime/host teardown."""
        call = next(iter(binding.calls))
        call.close(ValueError("legacy completion"))
        assert observed == []

    monkeypatch.setattr(runtime.packLoader, "close", legacyClose)

    @lifetimeBoundary
    def failedCreationCleanup(host):
        """Exercises the actual deferred legacy helper used by failed host creation."""
        with host._lane:
            host._cleanupFailedOperation(runtime=runtime)
            assert observed == []

    try:
        assert entered.wait(5)
        failedCreationCleanup(host)
        assert len(observed) == 1 and leases == [1]
    finally:
        proceed.set()
    with pytest.raises(LlmExecutionOwnershipError):
        pending.result(5)
    assert domain.snapshot.operations == 0 and len(adapter.closes) == 1


@pytest.mark.parametrize("closeKind", ("runtime", "application", "host"))
def test_active_job_close_requests_stop_outside_lanes_and_returns_pending(tmp_path, closeKind):
    """Close never waits across a running mutation lane for controlled work to cease."""
    host, runtime, values = hostFixture(tmp_path)
    _, service, _, _, adapter, _, _, _, _, _, _, leases = values
    entered, stopRequested, proceed = Event(), Event(), Event()

    def openHook(admission):
        """Installs a stop hook that proves both lanes are available, without claiming stop."""
        def stop():
            """A stop request is separate from confirmed operation quiescence."""
            assert background(lambda: host.activeApplicationRuntimes).result(5)
            stopRequested.set()
        admission.bindStop(stop)

    def hold(session):
        """Retains the runtime mutation lane until the test explicitly permits cleanup."""
        entered.set()
        assert proceed.wait(5)

    adapter.onOpen, adapter.onStream = openHook, hold

    def run(ctx, payload):
        """Runs the real serialized Job and provider pipeline."""
        return ctx.llm.run(providerName="controlled", query=QUERY)

    register(runtime, "active@1", run)
    job = background(lambda: runtime.runJob("active@1"))
    try:
        assert entered.wait(5)
        close = runtime.close if closeKind == "runtime" else (
            host.stop if closeKind == "host" else lambda: host.closeApplicationRun(runtime.applicationRun.applicationRunId))
        with pytest.raises(LlmExecutionCleanupError):
            background(close).result(5)
        assert stopRequested.is_set() and not job.done()
        assert leases == []
        assert runtime in host.activeApplicationRuntimes
    finally:
        proceed.set()
    assert job.result(5).state is JobState.FAILED
    if closeKind == "host":
        host.stop()
    elif closeKind == "application":
        host.closeApplicationRun(runtime.applicationRun.applicationRunId)
    else:
        runtime.close()
    assert leases == [1]


def test_committed_state_survives_late_io_failure_and_context_receipt_is_accepted(monkeypatch):
    """A late Job IO error cannot pretend an already accepted Value mutation rolled back."""
    from backend.io.managedIo import ManagedIoTransaction
    runtime, values = setupRuntime()
    state = {}

    def run(ctx, payload):
        """Produces retained state and stages transcript evidence in the actual outer mutation."""
        state["handle"] = ctx.llm.run(providerName="controlled", query=QUERY,
                                     context=FreshContext(retain=True)).retainedContext
        transaction = ctx.memory.openTransaction()
        transaction.set("test/transcript", "accepted")
        transaction.commit()

    def failCommit(transaction):
        """Fails only the later ManagedIo phase, after authoritative Value acceptance."""
        raise OSError("late IO commit")

    register(runtime, "io@1", run)
    monkeypatch.setattr(ManagedIoTransaction, "commit", failCommit)
    job = runtime.runJob("io@1")
    assert job.state is JobState.FAILED and job.authoritativeStateAccepted
    assert runtime.applicationRun.application.committedState.load("test/transcript") == "accepted"
    assert not state["handle"]._lost and state["handle"]._provisional is None
    runtime.close()


def test_controlled_cleanup_error_preserves_run_observation_release_without_hardware():
    """Optional stream-only observation stops on uncertain cleanup and adds no hardware claims."""
    runtime, values = setupRuntime()
    values[4].cleanup = UNKNOWN
    sessions = []
    evidence = []

    def run(ctx, payload):
        """Attaches the existing PM observation facade to a controlled provider call."""
        session = ctx.observations.request(ObservationRequest(memory=False, cpu=False, gpu=False, stream=True))
        sessions.append(session)
        try:
            return ctx.llm.run(providerName="controlled", query=QUERY, observation=session)
        finally:
            evidence.append(session.snapshot())

    register(runtime, "observe@1", run)
    assert runtime.runJob("observe@1").state is JobState.FAILED
    snapshot = sessions[0].snapshot()
    assert snapshot.released and snapshot.hardware == ()
    assert evidence[0].stream.outcome is StreamOutcome.FAILED
