# file: tests/backend/application/test_cleanupEffectOwnership.py ; version: 1
"""R2 real failed-start cleanup ownership versus service-wide resource effects."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, current_thread

import pytest

from backend.llm import providerComposition as gates
from backend.llm.errors import LlmExecutionCleanupError
from backend.runtime import RuntimeHostState
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import rig, adapter, stream


def failedLoadedInitializer(rig, *, afterClose=False, pauseLease=True):
    """Install a real granted materialization hook then fail before its first save."""
    host, create, composer, processes, _, _, app, _, _ = rig
    source = app / "entry.py"
    source.write_text(source.read_text() + '''
def onApplicationCreate(ctx, state):
    """Use ordinary capability authority, then fail before saving creation."""
    ctx.capabilities.call("switch@1", "A")
    raise ValueError("hook failed after materialization")
''')
    caller = adapter.EntrySelector.fromSource("example", "1", "main", source)
    composer.grants = (adapter.LlamaCppGrant(caller, (None, "A", "B"), True),)
    entered, release = Event(), Event()
    if not pauseLease:
        release.set()
    held = {"leaseCalls": 0, "events": []}
    original = composer.compose

    def compose(snapshot):
        """Wrap only the actual broker-owned callback, preserving its real disposal."""
        original(snapshot)
        held["runtime"] = snapshot.ticket.runtime
        registration = snapshot.registry.requireRegistration("llama.cpp")
        binding = snapshot.bindings.binding(snapshot.registry, registration)
        close = binding.leaseCloser
        held["binding"] = binding

        def closeLease():
            """The service must retain this callback's ownership until return."""
            held["leaseCalls"] += 1
            held["events"].append("lease")
            held["resourceOwnerAtLease"] = gates._effectOwnership(snapshot.resources).disposer
            if "probe" in held:
                held["probe"]()
            if afterClose:
                close()
            entered.set()
            assert release.wait(5)
            if not afterClose:
                close()

        binding.leaseCloser = closeLease

    composer.compose = compose
    return host, create, processes, entered, release, held


@pytest.mark.parametrize("afterClose", [False, True])
def test_host_stop_preserves_loaded_abort_callback_until_it_unwinds(rig, afterClose):
    """Original R2 assertion plus a callback still live after its own inner disposal."""
    host, create, processes, entered, release, held = failedLoadedInitializer(rig, afterClose=afterClose)

    def probe():
        """A second thread can acquire every relevant lane during the actual callback."""
        runtime = held["runtime"]

        def acquire():
            """Reservations must not hold locks across provider callbacks."""
            with host._lane, runtime._lane, runtime._admissionLane:
                with gates._effectOwnership(host.executionResources).lane:
                    with runtime.llmProviders.executionPublication(), host.llmExecutions._lane:
                        with host.executionResources._lane, held["binding"].domain._lane:
                            return True

        with ThreadPoolExecutor(max_workers=1) as readers:
            assert readers.submit(acquire).result(5)

    held["probe"] = probe
    with ThreadPoolExecutor(max_workers=1) as initializers:
        initialization = initializers.submit(create)
        assert entered.wait(5)
        try:
            runtime = held["runtime"]
            assert runtime._compositionAttempt.phase == "cleanup" and runtime._cleanupAdmissions
            assert len(processes) == 1 and processes[0].terminated is afterClose
            with pytest.raises(LlmExecutionCleanupError):
                host.stop()
            assert processes[0].terminated is afterClose
            assert not host.executionResources._closed  # Do not poison the live owner's unload.
            assert host.state is RuntimeHostState.ACTIVE
        finally:
            release.set()
            with pytest.raises(ValueError, match="hook failed after materialization"):
                initialization.result(5)
    assert processes[0].terminated and held["leaseCalls"] == 1
    assert not host._pendingInitializations
    host.stop()
    host.stop()
    assert host.state is RuntimeHostState.STOPPED and held["leaseCalls"] == 1
    assert not host._pendingInitializations


def test_resource_disposer_winner_defers_new_abort_callback_then_drains(rig, monkeypatch):
    """Both effect boundaries choose atomically; a deferred cleanup attempt can retry."""
    host, create, processes, leaseEntered, _, held = failedLoadedInitializer(rig, pauseLease=False)
    beforeScope, startScope, disposerEntered, finishDisposer = Event(), Event(), Event(), Event()
    originalScope = gates.finalizationScope
    originalFinish = host.executionResources.finishClose

    @contextmanager
    def scope(runtimes):
        """Pause the initializer before its actual cleanup ownership reservation."""
        if current_thread().name.startswith("late-abort") and any(
                runtime._compositionAttempt is not None and runtime._compositionAttempt.phase == "cleanup" for runtime in runtimes):
            beforeScope.set()
            assert startScope.wait(5)
        with originalScope(runtimes) as blocked:
            yield blocked

    def finish():
        """The real service's effect entry occurs after its exclusive reservation."""
        if not disposerEntered.is_set():
            held["events"].append("resource-start")
            disposerEntered.set()
            assert finishDisposer.wait(5)
        result = originalFinish()
        held["events"].append("resource-return")
        return result

    monkeypatch.setattr(gates, "finalizationScope", scope)
    monkeypatch.setattr(host.executionResources, "finishClose", finish)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-abort") as initializers:
        with ThreadPoolExecutor(max_workers=1) as closers:
            initialization = initializers.submit(create)
            assert beforeScope.wait(5)
            closing = closers.submit(host.stop)
            assert disposerEntered.wait(5)
            try:
                assert gates._effectOwnership(host.executionResources).disposer is not None
                startScope.set()
                with pytest.raises(ExceptionGroup) as failed:
                    initialization.result(5)
                assert any(isinstance(error, ValueError) for error in failed.value.exceptions)
                assert not leaseEntered.is_set() and not processes[0].terminated
                assert host._pendingInitializations
            finally:
                startScope.set()
                finishDisposer.set()
            closing.result(5)
    assert processes[0].terminated and held["leaseCalls"] == 1
    assert held["resourceOwnerAtLease"] is None
    assert held["events"].index("resource-return") < held["events"].index("lease")
    assert not host._pendingInitializations and host.state is RuntimeHostState.STOPPED


@pytest.mark.parametrize("action", ["runtime", "application", "packs"])
def test_host_resource_effects_respect_direct_cleanup_callbacks(rig, action):
    """Explicit preflight drains need ownership too, not only attempt/lifetime drains."""
    host, create, _, processes, _, _, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    registration = runtime.llmProviders.requireRegistration("llama.cpp")
    binding = runtime.llmExecutions.binding(runtime.llmProviders, registration)
    original = binding.leaseCloser
    entered, release = Event(), Event()
    calls = []

    def lease():
        """Keep a real loaded residency owned during a direct close surface."""
        calls.append(True)
        entered.set()
        assert release.wait(5)
        original()

    def close():
        """Use public lifecycle entry points, not a synthetic scope counter."""
        if action == "runtime":
            return runtime.close()
        if action == "application":
            return host.closeApplicationRun(runtime.applicationRun.applicationRunId)
        return runtime.packLoader.close()

    binding.leaseCloser = lease
    with ThreadPoolExecutor(max_workers=1) as closers:
        closing = closers.submit(close)
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                host.stop()
            assert not processes[0].terminated and not host.executionResources._closed
        finally:
            release.set()
        if action == "application":
            with pytest.raises(RuntimeError):
                closing.result(5)  # Host stopping won before public removal, after its preflight.
        else:
            closing.result(5)
    host.stop()
    assert processes[0].terminated and len(calls) == 1


def test_service_ownership_survives_runtime_removal_from_host_tables(rig):
    """Host shutdown cannot infer absence of a callback from runtime table membership."""
    host, create, _, processes, _, state, _, _, _ = rig
    first, second = create(), create()
    first.invokeCapability("switch@1", "A")
    registration = first.llmProviders.requireRegistration("llama.cpp")
    binding = first.llmExecutions.binding(first.llmProviders, registration)
    original = binding.leaseCloser
    entered, release, inferenceEntered, inferenceRelease = Event(), Event(), Event(), Event()
    calls = []

    def lease():
        """The callback belongs to a removed runtime, but still owns its exact objects."""
        calls.append(True)
        entered.set()
        assert release.wait(5)
        original()

    def lines():
        """Hold peer execution so the first revoked binding cannot yet release its lease."""
        for index, line in enumerate(stream()):
            yield line
            if index == 1:
                inferenceEntered.set()
                assert inferenceRelease.wait(5)

    binding.leaseCloser = lease
    state["lines"] = lines()
    with ThreadPoolExecutor(max_workers=1) as callers:
        inference = callers.submit(second.invokeCapability, "run@1", "A")
        assert inferenceEntered.wait(5)
        try:
            with pytest.raises(ExceptionGroup):
                host.closeApplicationRun(first.applicationRun.applicationRunId)
            assert first.applicationRun.applicationRunId not in host._runtimesByRunId
            assert not entered.is_set()
        finally:
            inferenceRelease.set()
        assert inference.result(5) == "answer"
    with ThreadPoolExecutor(max_workers=1) as closers:
        closing = closers.submit(host.stop)
        assert entered.wait(5)
        try:
            assert not first._cleanupAdmissions  # The remaining runtime set is insufficient.
            with pytest.raises(LlmExecutionCleanupError):
                host.stop()
            assert not processes[0].terminated
        finally:
            release.set()
        closing.result(5)
    assert processes[0].terminated and len(calls) == 1


def test_unknown_disposal_after_owner_unwind_stays_reported_without_repeating_stop(rig, monkeypatch):
    """Live-owner pending and failed/unknown disposition remain separate after return."""
    host, create, processes, entered, release, held = failedLoadedInitializer(rig)
    waits = []

    def failWait(self, **kwargs):
        """Leave the exact owned child unreaped; no timer or local close proves exit."""
        waits.append(self)
        raise RuntimeError("unconfirmed owned process exit")

    from tests.first_party.llmDrivers.test_llamaCppExecutionResources import Process
    monkeypatch.setattr(Process, "wait", failWait)
    with ThreadPoolExecutor(max_workers=1) as initializers:
        initialization = initializers.submit(create)
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                host.stop()
            assert not processes[0].terminated
        finally:
            release.set()
        with pytest.raises(ExceptionGroup) as failed:
            initialization.result(5)
    assert any(isinstance(error, ValueError) for error in failed.value.exceptions)
    assert len(waits) == 1 and processes[0].returncode is None
    for _ in range(2):
        with pytest.raises(LlmExecutionCleanupError):
            host.stop()
    assert len(waits) == 1 and held["leaseCalls"] == 1
    assert held["binding"].adapter.owner.resident[1].process is processes[0]
    assert held["binding"].leaseFailed and host._pendingInitializations


def test_reentrant_host_stop_cannot_dispose_its_calling_cleanup_owner(rig):
    """Service ownership also prevents same-thread resource-finalizer reentry."""
    host, create, processes, _, release, held = failedLoadedInitializer(rig)
    release.set()
    observed = []

    def nestedStop():
        """Exercise the public host path while the actual lease callback is live."""
        with pytest.raises(LlmExecutionCleanupError):
            host.stop()
        observed.append(processes[0].terminated)
        assert not host.executionResources._closed

    held["probe"] = nestedStop
    with pytest.raises(ValueError, match="hook failed after materialization"):
        create()
    assert observed == [False] and processes[0].terminated and held["leaseCalls"] == 1
    host.stop()
    assert host.state is RuntimeHostState.STOPPED
