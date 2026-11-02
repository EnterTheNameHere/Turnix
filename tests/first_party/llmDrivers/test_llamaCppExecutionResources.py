# file: tests/first_party/llmDrivers/test_llamaCppExecutionResources.py ; version: 1
"""Actual ER-01/02/llama adapter with deterministic fake process/HTTP boundaries."""
import importlib.util
import json
import sys
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.application.applicationRuntime import ApplicationRuntime
from backend.application.lifecycle import ApplicationLifecycle
from backend.llm.errors import LlmExecutionConflictError, LlmExecutionOwnershipError, LlmExecutionCleanupError, LlmProviderProtocolError
from backend.llm.executionSessions import FreshContext
from backend.llm.llmTypes import LlmQuery
from backend.orchestration import Job, JobState
from backend.packs.runtime import ManualActivationPlan, PackResolver
from backend.runtime import RuntimeHost
from backend.save import ApplicationStore
from backend.registration import RegistrationScope

ROOT = Path(__file__).parents[3]
DRIVER = ROOT / "first-party/llmDrivers/llamaCpp"
SPEC = importlib.util.spec_from_file_location("er03AdapterTests", DRIVER / "executionAdapter.py")
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)
QUERY = LlmQuery(formatId="text/plain", payload="hello")


class Process:
    """Deterministic owned child with explicit termination/reap evidence."""

    def __init__(self, args, **kwargs):
        """Record immutable launch inputs without executing any binary."""
        self.args, self.kwargs, self.returncode, self.terminated = args, kwargs, None, False

    def poll(self):
        """Return only test-controlled child disposition."""
        return self.returncode

    def terminate(self):
        """Record a stop attempt without declaring exit until wait."""
        self.terminated = True

    def wait(self, **kwargs):
        """Confirm fake child exit."""
        self.returncode = 0
        return 0

    def kill(self):
        """Record forced termination of only this fake owned child."""
        self.terminated = True


class Response:
    """HTTP response context with independent content and local close state."""

    def __init__(self, data):
        """Keep configured bytes/lines and a local-transport closed flag."""
        self.data, self.closed = data, False

    def __enter__(self):
        """Enter the fake transport without modifying provider disposition."""
        return self

    def __exit__(self, *args):
        """Local context exit only closes the transport."""
        self.close()

    def close(self):
        """No assertion about server quiescence follows this local close."""
        self.closed = True

    def read(self):
        """Return a configured synchronous JSON body."""
        return self.data

    def __iter__(self):
        """Expose deterministic SSE lines, independently of local close."""
        return iter(self.data)


def stream(*, finish="stop", ident="chat-1", done=True, text="answer"):
    """Build a normal native-terminal stream, including valid empty-text cases."""
    lines = [b": heartbeat\n", ("data: " + json.dumps({"id": ident, "choices": [
        {"index": 0, "delta": {"content": text}, "finish_reason": None}]}) + "\n").encode()]
    if finish is not None:
        lines.append(("data: " + json.dumps({"id": ident, "choices": [
            {"index": 0, "delta": {}, "finish_reason": finish}]}) + "\n").encode())
    lines.append(("data: " + json.dumps({"id": ident, "choices": [], "usage": {"completion_tokens": 1}}) + "\n").encode())
    if done:
        lines.append(b"data: [DONE]\n")
    return lines


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Construct actual host/catalog/manifest composition over fake IO only."""
    executable, model = tmp_path / "server.exe", tmp_path / "model.gguf"
    executable.touch()
    model.touch()
    app = tmp_path / "packs/app"
    app.mkdir(parents=True)
    source = app / "entry.py"
    source.write_text('''from backend.llm.llmTypes import LlmQuery
from backend.llm.executionSessions import FreshContext
def switch(ctx, payload):
    """Explicit host-granted initial/idle transition."""
    ctx.llm.transitionProfile(ctx.llm.profile(providerName="llama.cpp", model=payload))
def run(ctx, payload):
    """Use the actual invocation bridge for every controlled inference."""
    return ctx.llm.run(providerName="llama.cpp", model=payload or "A",
        query=LlmQuery(formatId="text/plain", payload="hello")).rawText
def required(ctx, payload):
    """Reject required provider contracts before any transport is entered."""
    return ctx.llm.run(providerName="llama.cpp", query=LlmQuery(formatId="text/plain", payload="hello"),
        context=FreshContext(required=payload == "fresh", retain=payload == "retain"))
def onLoad(ctx):
    """Register only; initialization has no controlled execution access."""
    ctx.capabilities.register("switch@1", switch)
    ctx.capabilities.register("run@1", run)
    ctx.capabilities.register("required@1", required)
''', encoding="utf-8")
    (app / "manifest.json").write_text(json.dumps({"packId": "example", "kind": "appPack", "version": "1",
        "codeEntries": [{"id": "main", "source": "entry.py"}]}), encoding="utf-8")
    config = {"manageServer": True, "executable": str(executable), "defaultModel": "A", "models": {
        "A": {"modelPath": str(model), "parallelSlots": 2, "serverReasoning": "off"},
        "B": {"modelPath": str(model), "parallelSlots": 2, "serverReasoning": "on"}}}
    caller = adapter.EntrySelector.fromSource("example", "1", "main", source)
    provider = adapter.EntrySelector.fromSource("llmDrivers.llamaCpp", "0.3.0", "driver", DRIVER / "structuredCodeEntry.py")
    composer = adapter.LlamaCppComposition(domainId="primary", catalog=adapter.LlamaCppCatalog.fromConfig(config),
        providerEntries=(provider,), grants=(adapter.LlamaCppGrant(caller, (None, "A", "B"), True),))
    processes, requests = [], []
    state = {"lines": stream(), "slots": [{"id": 0, "is_processing": False}, {"id": 1, "is_processing": False}]}

    def popen(args, **kwargs):
        """Fake only process creation; exercise actual driver lifecycle code."""
        process = Process(args, **kwargs)
        processes.append(process)
        return process

    def urlopen(request, **kwargs):
        """Record wire payloads and separate terminal stream from slot acknowledgement."""
        path = request.full_url
        requests.append((path, None if request.data is None else json.loads(request.data)))
        if "onHttp" in state:
            state["onHttp"](request)
        if path.endswith("/slots"):
            return Response(json.dumps(state["slots"]).encode())
        if path.endswith("/apply-template"):
            return Response(b'{"prompt":"rendered"}')
        if path.endswith("/tokenize"):
            return Response(b'{"tokens":[1,2,3]}')
        response = Response(state["lines"])
        state["response"] = response
        return response

    monkeypatch.setattr(adapter._impl.subprocess, "Popen", popen)
    def ready(*args):
        """Fake only readiness; no binary or health server is started."""
        return None

    monkeypatch.setattr(adapter._impl.LlamaCppDriver, "_waitReady", ready)
    monkeypatch.setattr(adapter.http, "urlopen", urlopen)
    host = RuntimeHost(applicationStore=ApplicationStore(tmp_path / "saves"),
        packResolver=PackResolver(roots=(DRIVER, app.parent)), providerComposers=(composer,))
    host.start()
    plan = ManualActivationPlan(packIds=("llmDrivers.llamaCpp", "example"))

    def create():
        """Use the real manifest and host initialization path."""
        return host.createApplication(appPackId="example", plan=plan, config={"llamaCpp": config})

    yield host, create, composer, processes, requests, state, app, plan, config
    try:
        host.stop()
    except Exception:
        pass  # Unknown-state tests intentionally retain fail-closed tombstones.


def test_explicit_transition_and_compatible_fresh_reuse(rig):
    """Normal native completion drains claims and fresh calls reuse loaded weights."""
    host, create, _, processes, requests, _, _, _, _ = rig
    runtime = create()
    assert not processes
    with pytest.raises(LlmExecutionConflictError):
        runtime.invokeCapability("run@1", "A")
    runtime.invokeCapability("switch@1", "A")
    assert runtime.invokeCapability("run@1", "A") == "answer"
    assert runtime.invokeCapability("run@1", "A") == "answer"
    assert len(processes) == 1 and not processes[0].terminated
    bodies = [body for url, body in requests if url.endswith("/completions")]
    assert all(body["id_slot"] == 0 and body["cache_prompt"] is False for body in bodies)
    runtime.invokeCapability("switch@1", "B")
    assert len(processes) == 2 and processes[0].terminated
    assert host.executionResources.snapshots[0].operations == 0


@pytest.mark.parametrize("lines", [stream(finish=None), stream(done=False), [b"data: [DONE]\n"], stream(ident="")])
def test_unproven_terminal_quarantines_even_when_slot_reports_idle(rig, lines):
    """An idle observation cannot repair missing/invalid native completion evidence."""
    host, create, _, processes, requests, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    state["lines"] = lines
    with pytest.raises(Exception):
        runtime.invokeCapability("run@1", "A")
    assert host.executionResources.snapshots[0].state.value == "unknown"
    with pytest.raises(LlmExecutionConflictError):
        runtime.invokeCapability("switch@1", "B")
    assert len(processes) == 1 and not processes[0].terminated
    assert not any(url.endswith("/slots") for url, _ in requests)


@pytest.mark.parametrize("contract", ["fresh", "retain"])
def test_unsupported_required_contract_rejects_before_io(rig, contract):
    """Unsupported stronger contracts do not reset, load or enter HTTP."""
    _, create, _, processes, requests, _, _, _, _ = rig
    runtime = create()
    with pytest.raises(Exception, match="context contract"):
        runtime.invokeCapability("required@1", contract)
    assert processes == [] and requests == []


def test_two_applications_share_stable_residency_and_final_drained_release(rig):
    """One Application's unload cannot stop another's compatible resident process."""
    host, create, _, processes, _, _, _, _, _ = rig
    first, second = create(), create()
    first.invokeCapability("switch@1", "A")
    assert second.invokeCapability("run@1", "A") == "answer"
    host.closeApplicationRun(first.applicationRun.applicationRunId)
    assert not processes[0].terminated
    host.closeApplicationRun(second.applicationRun.applicationRunId)
    assert processes[0].terminated


def test_hook_capability_work_is_bound_before_create_hook_and_save(rig):
    """A creation hook can call ordinary mediated capabilities after composition."""
    _, create, composer, processes, _, _, app, _, _ = rig
    source = app / "entry.py"
    source.write_text(source.read_text() + '''
def onApplicationCreate(ctx, state):
    """No lifecycle execution grant; enter the normal capability bridge."""
    ctx.capabilities.call("switch@1", "A")
    assert ctx.capabilities.call("run@1", "A") == "answer"
''')
    composer.grants = (adapter.LlamaCppGrant(adapter.EntrySelector.fromSource("example", "1", "main", source),
        (None, "A", "B"), True),)
    runtime = create()
    assert runtime.acceptedSaveBundle is not None and len(processes) == 1


@pytest.mark.parametrize("bindFirst", [False, True])
def test_stop_during_composition_cannot_run_hook_save_or_publish(rig, bindFirst):
    """Events prove pending initialization ownership survives a shutdown winner."""
    host, create, composer, processes, _, _, _, _, _ = rig
    entered, release = Event(), Event()
    original = composer.compose

    def compose(snapshot):
        """Pause after successful binding to exercise published-ticket revocation."""
        if bindFirst:
            original(snapshot)
        entered.set()
        assert release.wait(5)
        if not bindFirst:
            original(snapshot)

    composer.compose = compose
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(create)
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                host.stop()
            assert host.activeApplicationRuntimes == () and processes == []
        finally:
            release.set()
        with pytest.raises(Exception):
            pending.result(5)
    host.stop()  # Once compose unwound, explicit retry may drain deferred close.
    assert not host._pendingInitializations
    assert list(host._applicationStore.savesRoot.glob("**/current*")) == []


def test_managed_raw_driver_and_both_facade_paths_fail_closed(rig):
    """Direct substrate selection/start/HTTP cannot bypass the controlled binding."""
    _, _, _, processes, requests, _, _, _, config = rig
    driver = adapter._impl.LlamaCppDriver(config)
    for operation in (driver.start, driver.stop, lambda: driver.ensureModel("A"),
                      lambda: driver.postJson("/tokenize", {}, timeoutSeconds=1),
                      lambda: driver._startModel(driver.models["A"])):
        with pytest.raises(LlmExecutionOwnershipError):
            operation()
    for structured in (False, True):
        facade = adapter._impl._ManagedFacade(config, structured=structured)
        with pytest.raises(LlmExecutionOwnershipError):
            facade.getExecutionProfile(model="A", providerOptions={})
    assert not processes and not requests


def capability(runtime, name, handler):
    """Publish a test capability for the actual initially granted caller incarnation."""
    identity = next(i for i, _ in runtime._codeEntries.values() if i.codeEntryId == "main")
    scope = RegistrationScope()
    runtime.capabilities.register(scope, ownerId=identity.codeEntryInstanceId, capabilityId=name, handler=handler)
    scope.publish()
    return scope


def test_busy_application_cannot_switch_process_during_peer_stream(rig):
    """Architectural reproduction: an open A response prevents B's replacement."""
    host, create, _, processes, _, state, _, _, _ = rig
    first, second = create(), create()
    first.invokeCapability("switch@1", "A")
    entered, release = Event(), Event()

    def lines():
        """Hold after an actual answer delta and before native completion."""
        for index, line in enumerate(stream()):
            yield line
            if index == 1:
                entered.set()
                assert release.wait(5)

    state["lines"] = lines()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.invokeCapability, "run@1", "A")
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionConflictError, match="busy"):
                second.invokeCapability("switch@1", "B")
            assert len(processes) == 1 and not processes[0].terminated
            assert not state["response"].closed
        finally:
            release.set()
        assert pending.result(5) == "answer"
    second.invokeCapability("switch@1", "B")
    assert processes[0].terminated and len(processes) == 2
    assert host.executionResources.snapshots[0].operations == 0


def test_query_estimation_through_stream_is_one_pinned_generation(rig):
    """Architectural reproduction: A's helper estimator cannot run against B residency."""
    _, create, _, processes, requests, state, _, _, _ = rig
    first, second = create(), create()
    first.invokeCapability("switch@1", "A")
    entered, release = Event(), Event()
    options = {"reasoningFormat": "deepseek", "chatTemplateKwargs": {"enable_thinking": False}}

    def before(request):
        """Hold actual template IO while a competing Application tries to switch."""
        if request.full_url.endswith("/apply-template"):
            entered.set()
            assert release.wait(5)

    def items(ctx, payload):
        """Provide normal processing query items through the capability bridge."""
        return [{"itemId": "one", "kind": "text", "content": "hello", "metadata": {}}]

    def query(ctx, payload):
        """Borrow read-only estimation through this actual granted helper invocation."""
        assert ctx.llm.estimateInputTokens(providerName="llama.cpp", query=QUERY, providerOptions=options) == 3
        return {"formatId": "text/plain", "payload": "hello"}

    def processing(ctx, payload):
        """Protect query helpers, estimate, stream and cleanup under the outer claim."""
        return ctx.llm.runProcessing(memoryKey="resource-test", inputValue={}, providerName="llama.cpp", model="A",
            buildQueryItemsCapabilityId="items@1", buildQueryCapabilityId="query@1", providerOptions=options).llm.rawText

    for name, handler in (("items@1", items), ("query@1", query), ("processing@1", processing)):
        capability(first, name, handler)
    state["onHttp"] = before
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.invokeCapability, "processing@1")
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionConflictError, match="busy"):
                second.invokeCapability("switch@1", "B")
            assert len(processes) == 1 and not processes[0].terminated
        finally:
            release.set()
        assert pending.result(5) == "answer"
    bodies = [body for url, body in requests if url.endswith(("/apply-template", "/completions"))]
    assert all(body["reasoning_format"] == "deepseek" for body in bodies)
    assert all(body["chat_template_kwargs"] == {"enable_thinking": False} for body in bodies)


def test_exclusive_reservation_denies_even_compatible_peer_operations(rig):
    """A conversation's ER-01 reservation is the only exclusion mechanism."""
    _, create, _, processes, requests, _, _, _, _ = rig
    first, second = create(), create()
    first.invokeCapability("switch@1", "A")
    held = {}

    def reserve(ctx, payload):
        """Save a run-owned reservation without retaining its invocation Context."""
        held["session"] = ctx.llm.reserveConversation(ctx.llm.profile(providerName="llama.cpp", model="A"))

    def own(ctx, payload):
        """Use a fresh invocation of the exact same owner within its reservation."""
        return ctx.llm.run(providerName="llama.cpp", query=QUERY, session=held["session"]).rawText

    capability(first, "reserve@1", reserve)
    capability(first, "own@1", own)
    first.invokeCapability("reserve@1")
    with pytest.raises(LlmExecutionConflictError):
        second.invokeCapability("run@1", "A")
    assert not requests and len(processes) == 1
    assert first.invokeCapability("own@1") == "answer"
    held["session"].close()
    first.llmExecutions.finishClose(first.llmProviders)
    assert second.invokeCapability("run@1", "A") == "answer"


@pytest.mark.parametrize("slots", [None, [], [{"id": 0, "is_processing": True}, {"id": 1, "is_processing": False}],
    [{"id": 9, "is_processing": False}, {"id": 1, "is_processing": False}],
    [{"id": False, "is_processing": False}, {"id": 1, "is_processing": False}]])
def test_native_terminal_with_unproven_owned_slot_still_quarantines(rig, slots):
    """Provider completion and owned idle-slot acknowledgement are distinct evidence."""
    host, create, _, processes, requests, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    state["slots"] = slots
    with pytest.raises(LlmExecutionCleanupError):
        runtime.invokeCapability("run@1", "A")
    assert host.executionResources.snapshots[0].state.value == "unknown"
    assert requests[-1][0].endswith("/slots") and not processes[0].terminated


def test_valid_empty_completion_and_usage_do_not_require_answer_text(rig):
    """A native terminal for an empty answer can confirm drain and preserve usage."""
    host, create, _, _, _, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    state["lines"] = stream(text="")
    assert runtime.invokeCapability("run@1", "A") == ""
    assert host.executionResources.snapshots[0].operations == 0


def test_foreign_completion_id_does_not_release_claim(rig):
    """Trailing usage cannot attribute a second request's terminal to this call."""
    host, create, _, _, requests, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    state["lines"] = [line.replace(b"chat-1", b"foreign") if b'"usage"' in line else line for line in stream()]
    with pytest.raises(LlmProviderProtocolError):
        runtime.invokeCapability("run@1", "A")
    assert host.executionResources.snapshots[0].state.value == "unknown"
    assert not any(url.endswith("/slots") for url, _ in requests)


def test_cancelled_job_keeps_unknown_even_if_local_response_closed(rig):
    """Job cancellation/rollback survives uncertain provider quiescence."""
    host, create, _, processes, requests, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    entered, release = Event(), Event()

    def lines():
        """Return another frame only after explicit cancellation was delivered."""
        for index, line in enumerate(stream()):
            yield line
            if index == 1:
                entered.set()
                assert release.wait(5)

    state["lines"] = lines()
    job = Job.new()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.runJob, "run@1", job=job)
        assert entered.wait(5)
        try:
            job.requestCancellation()
            assert state["response"].closed
        finally:
            release.set()
        assert pending.result(5).state is JobState.CANCELLED
    assert host.executionResources.snapshots[0].state.value == "unknown"
    assert not processes[0].terminated and not any(url.endswith("/slots") for url, _ in requests)


def test_unmigrated_managed_activation_fails_before_hooks_save_or_start(rig):
    """No composer is a clear activation failure, never legacy managed execution."""
    host, create, _, processes, requests, _, _, _, _ = rig
    host.providerComposers = ()
    with pytest.raises(LlmExecutionOwnershipError, match="trusted host composition"):
        create()
    assert host.activeApplicationRuntimes == () and not host._pendingInitializations
    assert not processes and not requests


def test_later_owner_gets_no_implicit_grant_and_old_binding_remains_live(rig):
    """Later activation never mutates the initial owner grant snapshot."""
    _, create, _, processes, _, _, app, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    late = app.parent / "late"
    late.mkdir()
    (late / "manifest.json").write_text(json.dumps({"packId": "late", "kind": "modPack", "version": "1",
        "codeEntries": [{"id": "later", "source": "entry.py"}]}))
    (late / "entry.py").write_text('''from backend.llm.llmTypes import LlmQuery
def run(ctx, payload):
    """A new actual owner cannot borrow an earlier incarnation's grants."""
    ctx.llm.run(providerName="llama.cpp", model="A", query=LlmQuery(formatId="text/plain",payload="hello"))
def onLoad(ctx):
    """Register an ungranted later caller."""
    ctx.capabilities.register("late@1", run)
''')
    runtime.packLoader.activatePack(PackResolver(roots=(late,)).requireSingle("late"))
    with pytest.raises(LlmExecutionOwnershipError):
        runtime.invokeCapability("late@1")
    assert runtime.invokeCapability("run@1", "A") == "answer" and len(processes) == 1


def test_multiple_composers_share_one_stable_compatible_owner(rig):
    """Duplicate compatible composition cannot introduce a second domain adapter."""
    host, create, composer, processes, _, _, _, _, config = rig
    another = adapter.LlamaCppComposition(domainId="primary", catalog=adapter.LlamaCppCatalog.fromConfig(config),
        providerEntries=composer.providerEntries, grants=composer.grants)
    host.providerComposers = (composer, another)
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    assert runtime.invokeCapability("run@1", "A") == "answer"
    assert len(host.executionResources.snapshots) == 1 and len(processes) == 1


def test_conflicting_composer_envelope_is_not_silently_ignored(rig):
    """An existing binding cannot hide a conflicting domain/catalog authority."""
    host, create, composer, processes, _, _, _, _, config = rig
    conflict = dict(config, port=8081)
    host.providerComposers = (composer, adapter.LlamaCppComposition(domainId="primary",
        catalog=adapter.LlamaCppCatalog.fromConfig(conflict), providerEntries=composer.providerEntries, grants=composer.grants))
    with pytest.raises(LlmExecutionOwnershipError):
        create()
    assert host.activeApplicationRuntimes == () and processes == []
    assert not host._pendingInitializations


def test_composition_and_partial_rollback_run_outside_host_and_runtime_lanes(rig):
    """Second-thread probes catch hidden RLock ownership during both callback phases."""
    host, create, composer, processes, _, _, _, _, _ = rig
    snapshots = []
    original = composer.compose

    def check(runtime):
        """A second thread must enter the real host/runtime lanes immediately."""
        with host._lane:
            with runtime._lane:
                return True

    def compose(snapshot):
        """Publish the first composer then probe lanes from another thread."""
        snapshots.append(snapshot)
        runtime = host._pendingInitializations[next(iter(host._pendingInitializations))]
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(check, runtime).result(5)
        original(snapshot)
        binding = snapshot.bindings.binding(snapshot.registry, snapshot.registrations[0])
        close = binding.leaseCloser

        def closeLease():
            """Rollback lease finalization cannot inherit the enclosing lifecycle lanes."""
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(check, runtime).result(5)
            close()

        binding.leaseCloser = closeLease

    class FailingComposer:
        """Fail only after the first binding transferred lifetime ownership."""

        def compose(self, snapshot):
            """Exercise rollback of the whole new activation, not only the last provider."""
            raise ValueError("second composer failed")

    composer.compose = compose
    host.providerComposers = (composer, FailingComposer())
    with pytest.raises(ValueError, match="second composer failed"):
        create()
    snapshot = snapshots[0]
    assert not snapshot.registry._registry.snapshot()
    assert not host._pendingInitializations and not host.activeApplicationRuntimes and not processes
    assert snapshot.ticket.phase == "aborted"
    assert not snapshot.ticket.runtime._codeEntries and not snapshot.ticket.runtime.packLoader._loadedPacks


def test_later_batch_failure_preserves_old_binding_owners_and_residency(rig):
    """Partial activation rollback must not revoke the pre-existing registry."""
    _, create, _, processes, _, _, app, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    oldOwners = dict(runtime._llmOwners)
    new = app.parent / "new"
    new.mkdir()
    (new / "manifest.json").write_text(json.dumps({"packId": "new", "kind": "modPack", "version": "1", "codeEntries": []}))

    class Fail:
        """Reject a new activation batch after its scope is staged."""

        def compose(self, snapshot):
            """Do not modify any existing binding grant."""
            raise ValueError("later batch failed")

    runtime.providerComposers = (*runtime.providerComposers, Fail())
    with pytest.raises(ValueError, match="later batch failed"):
        runtime.packLoader.activatePack(PackResolver(roots=(new,)).requireSingle("new"))
    assert all(runtime._llmOwners[key] is owner and not owner._closed for key, owner in oldOwners.items())
    assert runtime.invokeCapability("run@1", "A") == "answer"
    assert len(processes) == 1 and not processes[0].terminated
    runtime.providerComposers = runtime.providerComposers[:-1]
    runtime.packLoader.activatePack(PackResolver(roots=(new,)).requireSingle("new"))


def test_standalone_full_plan_uses_the_same_composition_barrier(rig):
    """A host is not required, but its trusted composition and issued owners are."""
    host, _, composer, processes, _, _, _, plan, config = rig
    runtime = ApplicationRuntime(appPackId="example", packResolver=host._packResolver,
        applicationStore=host._applicationStore, providerComposers=(composer,), config={"llamaCpp": config})
    try:
        ApplicationLifecycle.create(runtime=runtime, plan=plan)
        runtime.invokeCapability("switch@1", "A")
        assert runtime.invokeCapability("run@1", "A") == "answer"
        assert len(processes) == 1
    finally:
        runtime.close()
    assert processes[0].terminated


def test_reentrant_activation_is_rejected_without_dropping_invocation_lane(rig):
    """A Pack cannot trigger outside-lock composition while a Job holds its lane."""
    _, create, _, _, _, _, _, _, _ = rig
    runtime = create()

    def nested(ctx, payload):
        """Try activation from an ordinary mediated capability."""
        runtime.packLoader.activate(ManualActivationPlan(packIds=()))

    capability(runtime, "nested@1", nested)
    with pytest.raises(LlmExecutionOwnershipError, match="inside an invocation"):
        runtime.invokeCapability("nested@1")


def test_direct_substrate_manifest_adopts_the_same_controlled_binding(rig, monkeypatch):
    """Selecting codeEntry.py itself cannot restore eager start or unbound execution."""
    from dataclasses import replace
    from backend.packs.runtime import CodeEntryDefinition
    host, create, composer, processes, _, _, _, _, _ = rig
    old = host._packResolver.requireSingle

    def definition(name):
        """Select the supported substrate entry without editing the real manifest."""
        pack = old(name)
        return replace(pack, codeEntries=(CodeEntryDefinition("driver", "codeEntry.py"),)) if name == "llmDrivers.llamaCpp" else pack

    monkeypatch.setattr(host._packResolver, "requireSingle", definition)
    composer.providerEntries = (adapter.EntrySelector.fromSource("llmDrivers.llamaCpp", "0.3.0", "driver", DRIVER / "codeEntry.py"),)
    runtime = create()
    assert not processes
    runtime.invokeCapability("switch@1", "A")
    assert runtime.invokeCapability("run@1", "A") == "answer" and len(processes) == 1


@pytest.mark.parametrize("failure", ["readiness", "reap"])
def test_failed_startup_has_known_empty_or_unknown_owned_process_evidence(rig, monkeypatch, failure):
    """A failed readiness check cannot discard an unreaped child reference."""
    host, create, _, processes, _, _, _, _, _ = rig

    def failReady(*args):
        """Fail only after fake Popen has returned an owned child."""
        raise RuntimeError("readiness failed")

    def failWait(*args, **kwargs):
        """An unconfirmed exit must retain the original child as a blocker."""
        raise RuntimeError("wait failed")

    monkeypatch.setattr(adapter._impl.LlamaCppDriver, "_waitReady", failReady)
    if failure == "reap":
        monkeypatch.setattr(Process, "wait", failWait)
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    snapshot = host.executionResources.snapshots[0]
    if failure == "readiness":
        assert snapshot.state.value == "failed" and processes[0].returncode == 0
    else:
        assert snapshot.state.value == "unknown" and processes[0].returncode is None
        binding = runtime.llmExecutions.binding(runtime.llmProviders, runtime.llmProviders.requireRegistration("llama.cpp"))
        assert binding.adapter.owner.resident[1].process is processes[0]


def test_old_live_shared_driver_lease_is_neither_stolen_nor_stopped(rig):
    """A legacy lease is an explicit migration conflict rather than cleanup authority."""
    host, create, _, processes, _, _, _, _, _ = rig
    closed = []
    lease = host.sharedServices.acquire(serviceId="llm.driver.llama.cpp.managed", compatibilityKey="legacy",
        factory=object, closer=lambda value: closed.append(value))
    try:
        with pytest.raises(LlmExecutionOwnershipError, match="live legacy"):
            create()
        assert not closed and not lease.released and not processes
    finally:
        lease.release()


def test_request_sampling_changes_reuse_residency_without_transition(rig):
    """Per-call controls do not accidentally enter startup/profile compatibility."""
    _, create, _, processes, requests, _, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")

    def run(ctx, payload):
        """Forward independent immutable per-request options through actual admission."""
        return ctx.llm.run(providerName="llama.cpp", model="A", query=QUERY, providerOptions=payload).rawText

    capability(runtime, "options@1", run)
    assert runtime.invokeCapability("options@1", {"temperature": 0.2}) == "answer"
    assert runtime.invokeCapability("options@1", {"temperature": 0.8, "reasoningFormat": "none"}) == "answer"
    assert len(processes) == 1
    assert [body["temperature"] for url, body in requests if url.endswith("/completions")] == [0.2, 0.8]


def test_auxiliary_weights_change_the_whole_selected_residency(rig):
    """Same target plus different speculative weights requires an explicit transition."""
    host, create, composer, processes, _, _, app, _, config = rig
    auxiliary = app / "draft.gguf"
    auxiliary.touch()
    config["models"]["B"].update(specMethods=["draft-simple"], draftModelPath=str(auxiliary))
    host.providerComposers = (adapter.LlamaCppComposition(domainId="primary", catalog=adapter.LlamaCppCatalog.fromConfig(config),
        providerEntries=composer.providerEntries, grants=composer.grants),)
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    with pytest.raises(LlmExecutionConflictError):
        runtime.invokeCapability("run@1", "B")
    assert not processes[0].terminated
    runtime.invokeCapability("switch@1", "B")
    assert processes[0].terminated and len(processes) == 2
    assert "--spec-draft-model" in processes[1].args and str(auxiliary) in processes[1].args


def test_preview_rejects_changed_generation_before_provider_io(rig):
    """A preview contains no live estimator and cannot silently bind to a later A."""
    from backend.llm.errors import LlmStalePreviewError
    _, create, _, processes, requests, _, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    held = {}

    def items(ctx, payload):
        """Prepare ordinary authoritative query material."""
        return [{"itemId": "one", "kind": "text", "content": "hello", "metadata": {}}]

    def query(ctx, payload):
        """Return the explicit prompt chosen by this Pack."""
        return {"formatId": "text/plain", "payload": "hello"}

    def prepare(ctx, payload):
        """Save the issued preview rather than an invocation-scoped estimator."""
        held["preview"] = ctx.llm.prepareProcessing(memoryKey="preview", inputValue={}, providerName="llama.cpp", model="A",
            buildQueryItemsCapabilityId="items@1", buildQueryCapabilityId="query@1")

    def run(ctx, payload):
        """Require explicit provenance revalidation on the issued owner preview."""
        return ctx.llm.runPreview(held["preview"]).rawText

    for name, handler in (("items@1", items), ("query@1", query), ("prepare@1", prepare), ("preview-run@1", run)):
        capability(runtime, name, handler)
    runtime.invokeCapability("prepare@1")
    assert held["preview"].executionProfile.tokenEstimator is None
    assert runtime.invokeCapability("preview-run@1") == "answer"
    runtime.invokeCapability("switch@1", "B")
    runtime.invokeCapability("switch@1", "A")
    count = len(requests)
    with pytest.raises(LlmStalePreviewError):
        runtime.invokeCapability("preview-run@1")
    assert len(requests) == count and len(processes) == 3


def test_process_loss_after_native_terminal_cannot_confirm_wrong_residency(rig):
    """A post-terminal idle response from a dead process cannot authorize reuse."""
    host, create, _, processes, _, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")

    def lose(request):
        """Lose process identity between terminal consumption and slot acknowledgement."""
        if request.full_url.endswith("/slots"):
            processes[0].returncode = 1

    state["onHttp"] = lose
    with pytest.raises(LlmExecutionCleanupError):
        runtime.invokeCapability("run@1", "A")
    assert host.executionResources.snapshots[0].state.value == "unknown"


def test_final_lease_close_while_call_active_does_not_destroy_owned_process(rig):
    """An Application unload requests stop but cannot force release across its Job."""
    host, create, _, processes, _, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    entered, release = Event(), Event()

    def lines():
        """Keep provider work outstanding independently of local transport close."""
        for index, line in enumerate(stream()):
            yield line
            if index == 1:
                entered.set()
                assert release.wait(5)

    state["lines"] = lines()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.invokeCapability, "run@1", "A")
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                host.closeApplicationRun(runtime.applicationRun.applicationRunId)
            assert not processes[0].terminated and state["response"].closed
        finally:
            release.set()
        with pytest.raises(Exception):
            pending.result(5)
    assert host.executionResources.snapshots[0].state.value == "unknown"
    assert not processes[0].terminated


def test_runtime_close_during_composition_revokes_attempt_without_racing_unload(rig):
    """Direct close uses the same pending gate; the host can later create a fresh run."""
    host, create, composer, processes, _, _, _, _, _ = rig
    entered, release = Event(), Event()
    original = composer.compose

    def compose(snapshot):
        """Hold callback ownership after binding transferred its lifetime ticket."""
        original(snapshot)
        entered.set()
        assert release.wait(5)

    composer.compose = compose
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(create)
        assert entered.wait(5)
        runtime = next(iter(host._pendingInitializations.values()))
        try:
            with pytest.raises(LlmExecutionCleanupError):
                runtime.close()
            assert not processes and runtime._compositionAttempt.revoked
        finally:
            release.set()
        with pytest.raises(Exception):
            pending.result(5)
    composer.compose = original
    retry = create()
    retry.invokeCapability("switch@1", "A")
    assert retry.invokeCapability("run@1", "A") == "answer"


def test_caller_grant_does_not_imply_transition_authority(rig):
    """A host must grant idle transitions separately from approved profile access."""
    _, create, composer, processes, requests, _, _, _, _ = rig
    grant = composer.grants[0]
    composer.grants = (adapter.LlamaCppGrant(grant.entry, grant.selectors, False),)
    runtime = create()
    with pytest.raises(LlmExecutionConflictError, match="unauthorized"):
        runtime.invokeCapability("switch@1", "A")
    with pytest.raises(LlmExecutionConflictError):
        runtime.invokeCapability("run@1", "A")
    assert not processes and not requests


def test_revoked_binding_keeps_controlled_tombstone_and_never_falls_back(rig):
    """Revocation cannot turn an existing managed facade into a legacy execution path."""
    _, create, _, processes, requests, _, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    registration = runtime.llmProviders.requireRegistration("llama.cpp")
    binding = runtime.llmExecutions.binding(runtime.llmProviders, registration)
    runtime.llmExecutions.revokeBinding(binding)
    with pytest.raises(LlmExecutionOwnershipError):
        runtime.invokeCapability("run@1", "A")
    assert not requests
    assert processes[0].terminated  # Revoked, drained lifetime finalized once outside invocation.


def test_private_configuration_accessor_keeps_planned_and_actual_evidence_distinct(rig):
    """Trusted opt-in evidence is immutable and does not launch a process to inspect."""
    host, create, composer, processes, _, _, _, _, _ = rig
    before = composer.configurationEvidence(host.executionResources, "A")
    assert before["launch"]["actualArgv"] is None and not processes
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    after = composer.configurationEvidence(host.executionResources, "A")
    assert after["launch"]["actualArgv"] == tuple(processes[0].args)
    assert before["launch"]["actualArgv"] is None
    assert after["providerReportedEffective"]["status"] == "unknown-unqueried"
    with pytest.raises(TypeError):
        after["launch"]["status"] = "fabricated"


@pytest.mark.parametrize("action", ["host", "application"])
def test_shutdown_gates_later_activation_before_any_owned_process_finalizer(rig, action):
    """Pending activation of an already-active runtime is also a shutdown blocker."""
    host, create, _, processes, _, _, app, _, _ = rig
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    late = app.parent / "pending"
    late.mkdir()
    (late / "manifest.json").write_text(json.dumps({"packId": "pending", "kind": "modPack", "version": "1", "codeEntries": []}))
    entered, release = Event(), Event()

    class Block:
        """Keep a later activation callback outstanding until the test releases it."""

        def compose(self, snapshot):
            """No new grant or process ownership is created by this callback."""
            entered.set()
            assert release.wait(5)

    runtime.providerComposers = (*runtime.providerComposers, Block())
    pack = PackResolver(roots=(late,)).requireSingle("pending")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.packLoader.activatePack, pack)
        assert entered.wait(5)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                if action == "host":
                    host.stop()
                else:
                    host.closeApplicationRun(runtime.applicationRun.applicationRunId)
            assert not processes[0].terminated
        finally:
            release.set()
        with pytest.raises(Exception):
            pending.result(5)
    if action == "host":
        host.stop()
    else:
        host.closeApplicationRun(runtime.applicationRun.applicationRunId)
    assert processes[0].terminated


def test_changed_observed_binary_rejects_stale_catalog_before_launch(rig):
    """Known replacement cannot launch new bytes under an old observed profile identity."""
    host, create, _, processes, requests, _, _, _, config = rig
    Path(config["executable"]).write_bytes(b"changed observed binary")
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    assert host.executionResources.snapshots[0].state.value == "failed"
    assert not processes and not requests
