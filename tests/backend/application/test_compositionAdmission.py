# file: tests/backend/application/test_compositionAdmission.py ; version: 1
"""R1 actual-use gate races: issued runtime tickets, real lifecycle, fake provider IO."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event, current_thread
import sys

import pytest

from backend.llm import providerComposition as gates
from backend.llm.errors import LlmExecutionOwnershipError, LlmExecutionCleanupError
from backend.orchestration import JobState
from backend.packs.runtime import ManualActivationPlan
from backend.application.applicationRuntime import ApplicationRuntime
from backend.application.lifecycle import ApplicationLifecycle
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import rig, capability


def call(runtime, entry, name):
    """Use the actual capability or Job entry, preserving ordinary Job semantics."""
    return getattr(runtime, entry)(name)


def shutdown(host, runtime, action):
    """Exercise each public shutdown surface with no private bypass flag."""
    if action == "host":
        return host.stop()
    if action == "application":
        return host.closeApplicationRun(runtime.applicationRun.applicationRunId)
    if action == "packs":
        return runtime.packLoader.close()
    return getattr(runtime, action)()


@pytest.mark.parametrize("entry", ["invokeCapability", "runJob"])
def test_checked_invocation_rejects_when_activation_wins_actual_admission(rig, monkeypatch, entry):
    """Original R1-A: detached readiness cannot authorize the later handler entry."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()
    checked, proceed, composing, release = Event(), Event(), Event(), Event()
    original = runtime.requireCompositionReady
    observed = []

    def ready():
        """Pause after the unchanged real diagnostic, before atomic admission."""
        original()
        if current_thread().name.startswith("checked-invocation"):
            checked.set()
            assert proceed.wait(5)

    class Block:
        """Keep a winning activation in its outside-lock compose phase."""

        def compose(self, snapshot):
            """Expose a deterministic interval in which handler execution is forbidden."""
            composing.set()
            assert release.wait(5)

    def handler(ctx, payload):
        """Any execution here during composition is an architectural failure."""
        observed.append(runtime._compositionAttempt.phase if runtime._compositionAttempt else None)

    capability(runtime, "phase@1", handler)
    monkeypatch.setattr(runtime, "requireCompositionReady", ready)
    runtime.providerComposers = (Block(),)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="checked-invocation") as callers:
        with ThreadPoolExecutor(max_workers=1) as activations:
            invocation = callers.submit(call, runtime, entry, "phase@1")
            assert checked.wait(5)
            activation = activations.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
            try:
                assert composing.wait(5)
                proceed.set()
                with pytest.raises(LlmExecutionOwnershipError):
                    invocation.result(5)
                assert observed == []
            finally:
                proceed.set()
                release.set()
                activation.result(5)


@pytest.mark.parametrize("entry", ["invokeCapability", "runJob"])
def test_invocation_reservation_winner_rejects_activation_before_execution(rig, monkeypatch, entry):
    """A reserved invocation excludes activation even while not yet in its handler."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()
    reserved, release = Event(), Event()
    original = gates.reserveInvocation
    observed = []

    def reserve(target):
        """Pause after the real ticket was installed, outside its short gate lane."""
        admission = original(target)
        if current_thread().name.startswith("reserved-invocation"):
            reserved.set()
            assert release.wait(5)
        return admission

    def handler(ctx, payload):
        """The handler runs only after activation has been rejected."""
        observed.append(runtime._compositionAttempt)

    capability(runtime, "phase@1", handler)
    monkeypatch.setattr(gates, "reserveInvocation", reserve)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="reserved-invocation") as callers:
        with ThreadPoolExecutor(max_workers=1) as activations:
            invocation = callers.submit(call, runtime, entry, "phase@1")
            assert reserved.wait(5)
            try:
                activation = activations.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
                with pytest.raises(LlmExecutionOwnershipError, match="busy"):
                    activation.result(5)
                assert observed == []
            finally:
                release.set()
            result = invocation.result(5)
    assert observed == [None]
    if entry == "runJob":
        assert result.state is JobState.SUCCEEDED
    assert not runtime._invocationAdmissions


@pytest.mark.parametrize("action", ["close", "stop", "packs", "application", "host"])
def test_activation_wins_before_shutdown_admission_no_finalizer_crosses_composer(rig, monkeypatch, action):
    """R1-B both runtime cases plus host/Pack equivalents, with activation winning."""
    host, create, _, processes, _, _, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    checked, proceed, composing, release = Event(), Event(), Event(), Event()
    original = gates.requireNoPendingComposition

    def reserve(target, **kwargs):
        """Pause before atomic shutdown reservation so activation may win it."""
        if current_thread().name.startswith("pending-shutdown"):
            checked.set()
            assert proceed.wait(5)
        return original(target, **kwargs)

    class Block:
        """Hold the actual activation snapshot, not a synthetic ownership assertion."""

        def compose(self, snapshot):
            """Keep the callback live until shutdown disposition was inspected."""
            composing.set()
            assert release.wait(5)

    monkeypatch.setattr(gates, "requireNoPendingComposition", reserve)
    monkeypatch.setattr(sys.modules[type(runtime).__module__], "requireNoPendingComposition", reserve)
    runtime.providerComposers = (Block(),)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pending-shutdown") as closers:
        with ThreadPoolExecutor(max_workers=1) as activations:
            closing = closers.submit(shutdown, host, runtime, action)
            assert checked.wait(5)
            activation = activations.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
            try:
                assert composing.wait(5)
                proceed.set()
                with pytest.raises(LlmExecutionCleanupError):
                    closing.result(5)
                assert not processes[0].terminated
                assert runtime.packLoader._loadedPacks
                assert runtime._compositionAttempt.revoked
            finally:
                proceed.set()
                release.set()
                with pytest.raises(Exception):
                    activation.result(5)


@pytest.mark.parametrize("action", ["close", "stop", "packs", "application", "host"])
def test_shutdown_reservation_winner_rejects_later_activation_before_callbacks(rig, monkeypatch, action):
    """An atomic shutdown check is admission closure, not a detached preflight."""
    host, create, _, processes, _, _, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    reserved, release, composerEntered = Event(), Event(), Event()
    original = gates.requireNoPendingComposition

    def reserve(target, **kwargs):
        """Pause after actual admission closure and before any provider finalizer."""
        result = original(target, **kwargs)
        if current_thread().name.startswith("reserved-shutdown"):
            reserved.set()
            assert release.wait(5)
        return result

    class Forbidden:
        """No callback may acquire a snapshot after shutdown won admission."""

        def compose(self, snapshot):
            """Record an incorrect activation winner without blocking the test."""
            composerEntered.set()

    monkeypatch.setattr(gates, "requireNoPendingComposition", reserve)
    monkeypatch.setattr(sys.modules[type(runtime).__module__], "requireNoPendingComposition", reserve)
    runtime.providerComposers = (Forbidden(),)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="reserved-shutdown") as closers:
        with ThreadPoolExecutor(max_workers=1) as activations:
            closing = closers.submit(shutdown, host, runtime, action)
            assert reserved.wait(5)
            assert not processes[0].terminated  # Reservation itself is callback-free.
            try:
                activation = activations.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
                with pytest.raises(LlmExecutionOwnershipError, match="closed"):
                    activation.result(5)
                assert not composerEntered.is_set()
                assert runtime._compositionAttempt is None
            finally:
                release.set()
            closing.result(5)
    assert processes[0].terminated
    if action == "packs":
        runtime.providerComposers = ()
        runtime.packLoader.activate(ManualActivationPlan(packIds=()))
        assert not runtime._shutdownAdmission and not runtime._unloadAdmission
    else:
        with pytest.raises(LlmExecutionOwnershipError, match="closed"):
            runtime.packLoader.activate(ManualActivationPlan(packIds=()))


@pytest.mark.parametrize("entry", ["invokeCapability", "runJob"])
def test_queued_invocation_revalidates_shutdown_at_actual_use_boundary(rig, monkeypatch, entry):
    """An issued but not-yet-entered call cannot mutate after shutdown closes admission."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()
    reserved, release = Event(), Event()
    original = gates.reserveInvocation
    observed = []

    def reserve(target):
        """Hold a real invocation ticket while its execution has not started."""
        admission = original(target)
        if current_thread().name.startswith("queued-invocation"):
            reserved.set()
            assert release.wait(5)
        return admission

    def handler(ctx, payload):
        """Shutdown must reject before this arbitrary handler is entered."""
        observed.append(True)

    capability(runtime, "phase@1", handler)
    monkeypatch.setattr(gates, "reserveInvocation", reserve)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="queued-invocation") as callers:
        invocation = callers.submit(call, runtime, entry, "phase@1")
        assert reserved.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                runtime.close()
            assert runtime.packLoader._loadedPacks  # No legacy unload across the admission ticket.
        finally:
            release.set()
        with pytest.raises(LlmExecutionOwnershipError, match="closed"):
            invocation.result(5)
    assert observed == [] and not runtime._invocationAdmissions
    runtime.close()


def test_outside_lane_finalization_reserves_against_new_activation(rig, monkeypatch):
    """The lifetime drain itself cannot recreate a detached check-to-callback window."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()
    runtime.providerComposers = ()
    entered, release = Event(), Event()
    original = runtime.llmExecutions.finishClose

    def finalize(registry=None):
        """Pause at actual finalizer entry after the runtime gate reservation."""
        if current_thread().name.startswith("outside-finalizer"):
            entered.set()
            assert release.wait(5)
        return original(registry)

    monkeypatch.setattr(runtime.llmExecutions, "finishClose", finalize)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="outside-finalizer") as drains:
        with ThreadPoolExecutor(max_workers=1) as activations:
            drain = drains.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
            assert entered.wait(5)
            try:
                assert runtime._compositionAttempt is None and runtime._cleanupAdmissions
                activation = activations.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
                with pytest.raises(LlmExecutionOwnershipError, match="busy"):
                    activation.result(5)
            finally:
                release.set()
            drain.result(5)
    assert not runtime._cleanupAdmissions
    runtime.packLoader.activate(ManualActivationPlan(packIds=()))


def test_copied_invocation_scope_cannot_authorize_an_actual_entry_after_expiry(rig):
    """The use boundary validates live ticket membership, not only copied ContextVars."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()
    held, observed = {}, []

    def capture(ctx, payload):
        """Capture only a context copy to pressure the independent ticket lifetime."""
        held["scope"] = copy_context()

    def handler(ctx, payload):
        """An expired copied admission must fail before arbitrary capability code."""
        observed.append(True)

    capability(runtime, "capture@1", capture)
    capability(runtime, "phase@1", handler)
    runtime.invokeCapability("capture@1")
    actual = type(runtime).invokeCapability.__wrapped__.__wrapped__
    with pytest.raises(LlmExecutionOwnershipError, match="expired"):
        held["scope"].run(actual, runtime, "phase@1")
    assert not observed


def test_successful_pack_unload_allows_explicit_fresh_binding_for_reactivation(rig):
    """A reversible unload must permit a new exact registration, not inherit old grants."""
    _, create, _, processes, _, _, _, plan, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    oldRegistration = runtime.llmProviders.requireRegistration("llama.cpp")
    oldBinding = runtime.llmExecutions.binding(runtime.llmProviders, oldRegistration)
    oldOwner = next(iter(oldBinding.participants))
    runtime.packLoader.close()
    assert processes[0].terminated
    runtime.packLoader.activate(plan)
    newRegistration = runtime.llmProviders.requireRegistration("llama.cpp")
    newBinding = runtime.llmExecutions.binding(runtime.llmProviders, newRegistration)
    assert newRegistration is not oldRegistration and newBinding is not oldBinding
    assert oldOwner._closed and oldOwner not in newBinding.participants
    assert len(processes) == 1
    runtime.invokeCapability("switch@1", "A")
    assert runtime.invokeCapability("run@1", "A") == "answer" and len(processes) == 2


def test_revoked_binding_cannot_complete_managed_composition_barrier(rig):
    """A non-null tombstone is not a successfully installed live binding."""
    host, create, composer, processes, _, _, _, _, _ = rig
    original = composer.compose

    def compose(snapshot):
        """Revoke the published registration's binding before the barrier commits."""
        original(snapshot)
        registration = snapshot.registry.requireRegistration("llama.cpp")
        snapshot.bindings.revokeBinding(snapshot.bindings.binding(snapshot.registry, registration))

    composer.compose = compose
    with pytest.raises(LlmExecutionOwnershipError, match="revoked"):
        create()
    assert not host.activeApplicationRuntimes and not host._pendingInitializations and not processes


def test_running_handler_rejects_activation_immediately_without_waiting_on_its_lane(rig):
    """Admission conflict is immediate even when application code holds the runtime lane."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()
    entered, release = Event(), Event()

    def handler(ctx, payload):
        """Hold the real execution lane until the competing admission is resolved."""
        entered.set()
        assert release.wait(5)

    capability(runtime, "hold@1", handler)
    with ThreadPoolExecutor(max_workers=1) as callers:
        with ThreadPoolExecutor(max_workers=1) as activations:
            invocation = callers.submit(runtime.invokeCapability, "hold@1")
            assert entered.wait(5)
            try:
                activation = activations.submit(runtime.packLoader.activate, ManualActivationPlan(packIds=()))
                with pytest.raises(LlmExecutionOwnershipError, match="busy"):
                    activation.result(5)
            finally:
                release.set()
            invocation.result(5)


def test_standalone_initialization_close_winner_cannot_resume_save_or_start(rig):
    """The issued standalone continuation uses the same shutdown/activation gate."""
    host, _, composer, processes, _, _, _, plan, config = rig
    runtime = ApplicationRuntime(appPackId="example", packResolver=host._packResolver,
        applicationStore=host._applicationStore, providerComposers=(composer,), config={"llamaCpp": config})
    entered, release = Event(), Event()
    original = composer.compose

    def compose(snapshot):
        """Hold the standalone attempt after exact binding publication."""
        original(snapshot)
        entered.set()
        assert release.wait(5)

    composer.compose = compose
    with ThreadPoolExecutor(max_workers=1) as initializers:
        initialization = initializers.submit(ApplicationLifecycle.create, runtime=runtime, plan=plan)
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                runtime.close()
            assert runtime._shutdownAdmission and not processes
        finally:
            release.set()
        with pytest.raises(LlmExecutionOwnershipError):
            initialization.result(5)
    assert runtime._closed and runtime.acceptedSaveBundle is None and not runtime.applicationRun.active
    with pytest.raises(LlmExecutionOwnershipError, match="closed"):
        runtime.packLoader.activate(ManualActivationPlan(packIds=()))


def test_peer_composition_does_not_hide_unrelated_unknown_cleanup(rig):
    """Skip the composing registry's effects, while still reporting another registry's failure."""
    host, create, _, processes, _, _, _, _, _ = rig
    first, second = create(), create()
    first.invokeCapability("switch@1", "A")
    registration = first.llmProviders.requireRegistration("llama.cpp")
    binding = first.llmExecutions.binding(first.llmProviders, registration)

    def failLease():
        """Retain uncertain lease cleanup without a still-active provider call."""
        raise RuntimeError("owned lease cleanup is uncertain")

    binding.leaseCloser = failLease
    entered, release = Event(), Event()

    class Block:
        """Hold only the peer Application's activation snapshot."""

        def compose(self, snapshot):
            """Keep peer ownership outstanding during unrelated close reporting."""
            entered.set()
            assert release.wait(5)

    second.providerComposers = (Block(),)
    with ThreadPoolExecutor(max_workers=1) as activations:
        activation = activations.submit(second.packLoader.activate, ManualActivationPlan(packIds=()))
        assert entered.wait(5)
        try:
            with pytest.raises(ExceptionGroup, match="cleanup"):
                host.closeApplicationRun(first.applicationRun.applicationRunId)
            assert not processes[0].terminated and second._compositionAttempt.phase == "composing"
        finally:
            release.set()
            activation.result(5)


def test_failed_composition_finalizer_keeps_runtime_owned_until_callback_unwinds(rig):
    """Direct abort cleanup also reserves the real callback lifetime against shutdown."""
    host, create, composer, _, _, _, _, _, _ = rig
    entered, release = Event(), Event()
    original = composer.compose
    held = {}

    def compose(snapshot):
        """Install a lease callback that keeps the failed activation's cleanup live."""
        original(snapshot)
        held["runtime"] = snapshot.ticket.runtime
        registration = snapshot.registry.requireRegistration("llama.cpp")
        binding = snapshot.bindings.binding(snapshot.registry, registration)
        close = binding.leaseCloser

        def closeLease():
            """The abort must retain its exact callback ownership until this returns."""
            entered.set()
            assert release.wait(5)
            close()

        binding.leaseCloser = closeLease
        raise ValueError("failed composition")

    composer.compose = compose
    with ThreadPoolExecutor(max_workers=1) as initializers:
        initialization = initializers.submit(create)
        assert entered.wait(5)
        runtime = held["runtime"]
        try:
            assert runtime._compositionAttempt.phase == "cleanup"
            with pytest.raises(LlmExecutionCleanupError):
                runtime.close()
            assert not runtime._closed and not runtime._tracerClosed
        finally:
            release.set()
        with pytest.raises(ValueError, match="failed composition"):
            initialization.result(5)
    assert runtime._closed and not host._pendingInitializations
