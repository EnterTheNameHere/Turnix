# file: tests/first_party/llmDrivers/test_llamaCppRequestEvidence.py ; version: 1
"""Private evidence through real managed admission/composition with synthetic IO."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from hashlib import sha256
import json
import traceback

import pytest

from backend.llm.llmTypes import LlmQuery
from backend.orchestration import Job, JobState
from backend.packs.runtime import PackResolver
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import (
    rig, adapter, capability,
)

EvidenceError = adapter._evidence.RequestEvidenceError
SECRET = "PRIVATE-path-and-body-8e94"


def setupCapture(rig, *, required=True, limit=2 * 1024 * 1024):
    """Bind capture before activation; obtain actual host-issued caller identity."""
    host, create, composer, _, requests, state, _, _, _ = rig
    broker = adapter.PrivateRequestEvidence()
    composer.requestEvidence = broker
    composer.evidencePolicies = (adapter.RequestEvidencePolicy(
        composer.grants[0].entry, required, limit),)
    runtime = create()
    runtime.invokeCapability("switch@1", "A")
    identity = next(i for i, _ in runtime._codeEntries.values() if i.codeEntryId == "main")
    owner = runtime._llmOwners[identity.codeEntryInstanceId]
    return host, runtime, composer, broker, owner, requests, state


def collect(events):
    """Return a synchronous ACK sink retaining only immutable private records."""
    def sink(event):
        """Record the private receipt and explicitly acknowledge it."""
        events.append(event)
        return True
    return sink


def completionBodies(requests):
    """Select inference traffic, independently of template and slot traffic."""
    return [body for url, body in requests if url.endswith("/completions")]


def test_exact_bytes_unicode_controls_mutation_and_cleanup(rig):
    """Evidence equals immutable transport bytes, not planned query serialization."""
    host, runtime, _, broker, owner, requests, state = setupCapture(rig)
    wire, events = [], []
    def observe(request):
        """Independently observe bytes at the synthetic transport boundary."""
        if request.full_url.endswith("/completions"):
            wire.append(request.data)
    state["onHttp"] = observe
    query = LlmQuery(formatId="text/plain", payload="Unicode žluťoučký 🦊")
    options = {"maxTokens": 7, "temperature": 0.2}
    def call(ctx, payload):
        """Exercise actual option resolution, estimator and inference."""
        return ctx.llm.run(providerName="llama.cpp", model="A", query=query, providerOptions=options)
    capability(runtime, "capture@1", call)
    with broker.scope(owner, correlation=("suite", "unit", "turn"), sink=collect(events)):
        result = runtime.runJob("capture@1", job=Job.new())
    assert result.state is JobState.SUCCEEDED
    options["maxTokens"] = 99
    assert [e.phase for e in events] == ["serialized", "transport-attempt", "response-headers-observed", "final"]
    serialized = events[0]
    assert serialized.body == wire[0] and serialized.length == len(wire[0])
    assert serialized.sha256 == sha256(wire[0]).hexdigest()
    body = json.loads(serialized.body)
    assert body["max_tokens"] == 7 and body["cache_prompt"] is False and body["id_slot"] == 0
    assert "🦊" in body["messages"][0]["content"]
    assert all(e.serverConsumption == "unknown" for e in events)
    assert events[-1].mayHaveSent is True and events[-1].responseBodyObserved is True
    assert host.executionResources.snapshots[0].state.value == "ready"
    assert serialized.body.decode() not in repr(serialized)
    assert len(completionBodies(requests)) == 1


@pytest.mark.parametrize("mode", ["missing", "wrong-owner", "wrong-broker", "small", "sink", "no-ack"])
def test_required_failure_prevents_inference_and_preserves_safe_cleanup(rig, mode):
    """Missing/broken required capture never silently opts out or sends."""
    host, runtime, _, broker, owner, requests, _ = setupCapture(rig, limit=1 if mode == "small" else 4096)
    def sink(event):
        """Inject a private error or invalid ACK before send."""
        if mode == "sink":
            raise OSError(SECRET)
        return False if mode == "no-ack" else True
    with pytest.raises(EvidenceError) as caught:
        if mode == "missing":
            runtime.invokeCapability("run@1", "A")
        else:
            locator = adapter.PrivateRequestEvidence() if mode == "wrong-broker" else broker
            with locator.scope(object() if mode == "wrong-owner" else owner, correlation=("unit",), sink=sink):
                runtime.invokeCapability("run@1", "A")
    assert not completionBodies(requests)
    assert host.executionResources.snapshots[0].state.value == "ready"
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_disabled_owner_does_not_capture_foreign_scope(rig):
    """A scope cannot enable capture for an explicitly disabled actual owner."""
    _, runtime, _, broker, _, requests, _ = setupCapture(rig, required=False)
    events = []
    with broker.scope(object(), correlation=("foreign",), sink=collect(events)):
        assert runtime.invokeCapability("run@1", "A") == "answer"
    assert not events and len(completionBodies(requests)) == 1


@pytest.mark.parametrize("phase", ["serialized", "transport-attempt", "response-headers-observed", "final"])
def test_sink_privacy_and_truthful_post_send_failure(rig, phase):
    """Private callback errors have no raw chains; final failure preserves actual READY."""
    host, runtime, _, broker, owner, requests, _ = setupCapture(rig)
    events = []
    def sink(event):
        """Fail only at the selected private phase."""
        events.append(event)
        if event.phase == phase:
            raise ValueError(SECRET)
        return True
    with pytest.raises(EvidenceError) as caught:
        with broker.scope(owner, correlation=("turn",), sink=sink) as scope:
            runtime.invokeCapability("run@1", "A")
    assert scope.failed
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    assert bool(completionBodies(requests)) is (phase in ("response-headers-observed", "final"))
    if phase == "final":
        assert host.executionResources.snapshots[0].state.value == "ready"
        assert events[-1].mayHaveSent is True


@pytest.mark.parametrize("sent", [False, True])
def test_transport_failure_keeps_actual_ambiguity_and_unavailable_headers(rig, monkeypatch, sent):
    """Failure before successful open cannot invent response observation or consumption."""
    _, runtime, _, broker, owner, _, _ = setupCapture(rig)
    original = adapter._transport.OwnedHttpTransport.open
    def fail(transport, request, **kwargs):
        """Fail selected inference open with a controlled owned-transport fact."""
        if request.full_url.endswith("/completions"):
            transport.mayHaveSent = sent
            raise OSError("synthetic transport failure")
        return original(transport, request, **kwargs)
    monkeypatch.setattr(adapter._transport.OwnedHttpTransport, "open", fail)
    events = []
    with pytest.raises(adapter.LlmProviderConnectionError):
        with broker.scope(owner, correlation=("turn",), sink=collect(events)):
            runtime.invokeCapability("run@1", "A")
    assert events[-1].phase == "final" and events[-1].mayHaveSent is sent
    assert events[-1].responseHeadersObserved is None
    assert events[-1].responseBodyObserved is None


@pytest.mark.parametrize("primary", [ValueError("provider primary"), KeyboardInterrupt("primary")])
def test_final_callback_failure_cannot_replace_primary(rig, primary):
    """Exact primary objects survive late private sink errors and normal cleanup."""
    _, runtime, _, broker, owner, _, state = setupCapture(rig)
    def fail(request):
        """Raise from actual inference transport after its attempt record."""
        if request.full_url.endswith("/completions"):
            raise primary
    state["onHttp"] = fail
    def sink(event):
        """Create a secondary private finalization failure."""
        if event.phase == "final":
            raise OSError(SECRET)
        return True
    with pytest.raises(type(primary)) as caught:
        with broker.scope(owner, correlation=("turn",), sink=sink) as scope:
            runtime.invokeCapability("run@1", "A")
    assert caught.value is primary and scope.failed
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_final_failure_keeps_successful_job_outcome(rig):
    """A host archive failure is separate from an already observed successful Job."""
    host, runtime, _, broker, owner, _, _ = setupCapture(rig)
    def sink(event):
        """Fail after actual provider completion/cleanup."""
        if event.phase == "final":
            raise OSError(SECRET)
        return True
    with pytest.raises(EvidenceError):
        with broker.scope(owner, correlation=("turn",), sink=sink):
            result = runtime.runJob("run@1", "A", job=Job.new())
    assert result.state is JobState.SUCCEEDED
    assert host.executionResources.snapshots[0].state.value == "ready"


def test_scope_atomic_use_expiry_nested_and_cross_thread():
    """Copied ContextVars do not duplicate one-use authority or permit thread crossing."""
    broker, owner, events = adapter.PrivateRequestEvidence(), object(), []
    with broker.scope(owner, correlation=("turn",), sink=collect(events)):
        copied = copy_context()
        with pytest.raises(EvidenceError):
            with broker.scope(owner, correlation=("nested",), sink=collect(events)):
                pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(EvidenceError):
                pool.submit(copied.run, broker.claim, owner, object()).result(timeout=3)
        ticket = broker.claim(owner, object())
        with pytest.raises(EvidenceError):
            copied.run(broker.claim, owner, object())
        ticket.finish(mayHaveSent=False, headers=None, body=None)
    with pytest.raises(EvidenceError):
        copied.run(broker.claim, owner, object())
    assert ticket.owner is None and ticket.sink is None and ticket.call is None


def test_repeated_compose_cannot_change_capture_policy(rig):
    """Existing-binding fast path rejects both broadening and removal of host policy."""
    _, runtime, composer, _, _, _, _ = setupCapture(rig)
    original = composer.evidencePolicies
    composer.evidencePolicies = ()
    late = rig[6].parent / "late"
    late.mkdir()
    (late / "manifest.json").write_text(json.dumps({"packId": "late", "kind": "modPack",
        "version": "1", "codeEntries": []}))
    with pytest.raises(adapter.LlmExecutionOwnershipError):
        runtime.packLoader.activatePack(PackResolver(roots=(late,)).requireSingle("late"))
    composer.evidencePolicies = original


@pytest.mark.parametrize("phase", ["serialized", "transport-attempt"])
@pytest.mark.parametrize("action", ["cancel", "revoke"])
def test_callback_cancellation_or_owner_revocation_rechecked_before_send(rig, phase, action):
    """Acknowledgement cannot extend revoked admission or ignore concurrent cancellation."""
    _, runtime, _, broker, owner, requests, _ = setupCapture(rig)
    job, events = Job.new(), []
    def sink(event):
        """Revoke exact execution while no broker/backend stop lock is held."""
        events.append(event)
        if event.phase == phase:
            if action == "cancel":
                job.requestCancellation()
            else:
                owner.revoke()
        return True
    with broker.scope(owner, correlation=("turn",), sink=sink):
        result = runtime.runJob("run@1", "A", job=job)
    assert result.state is not JobState.SUCCEEDED
    assert not completionBodies(requests)
    assert events[-1].phase == "final" and events[-1].mayHaveSent is False


def test_final_non_exception_failure_is_exact_after_safe_cleanup(rig):
    """Final non-Exception interruption is delayed only until actual cleanup finishes."""
    host, runtime, _, broker, owner, _, _ = setupCapture(rig)
    primary = KeyboardInterrupt("host interruption")
    def sink(event):
        """Inject the exact primary object during final private diagnostics."""
        if event.phase == "final":
            raise primary
        return True
    with pytest.raises(KeyboardInterrupt) as caught:
        with broker.scope(owner, correlation=("turn",), sink=sink):
            assert runtime.invokeCapability("run@1", "A") == "answer"
    assert caught.value is primary
    assert host.executionResources.snapshots[0].state.value == "ready"


def test_copied_context_overlapping_claim_is_atomic():
    """Second use in a copied context fails even while the original claim is live."""
    broker, owner = adapter.PrivateRequestEvidence(), object()
    with broker.scope(owner, correlation=("turn",), sink=collect([])):
        first, second = copy_context(), copy_context()
        ticket = first.run(broker.claim, owner, object())
        with pytest.raises(EvidenceError):
            second.run(broker.claim, owner, object())
        ticket.finish(mayHaveSent=False, headers=None, body=None)


def test_two_applications_do_not_share_caller_scopes(rig):
    """Same source policy binds two distinct runtime-issued owners without leakage."""
    _, first, _, broker, firstOwner, requests, _ = setupCapture(rig)
    second = rig[1]()
    identity = next(i for i, _ in second._codeEntries.values() if i.codeEntryId == "main")
    secondOwner = second._llmOwners[identity.codeEntryInstanceId]
    events = []
    with broker.scope(firstOwner, correlation=("first",), sink=collect(events)):
        with pytest.raises(EvidenceError):
            second.invokeCapability("run@1", "A")
        assert first.invokeCapability("run@1", "A") == "answer"
    with broker.scope(secondOwner, correlation=("second",), sink=collect(events)):
        assert second.invokeCapability("run@1", "A") == "answer"
    assert [e.correlation for e in events if e.phase == "serialized"] == [("first",), ("second",)]
    assert len(completionBodies(requests)) == 2


def test_callbacks_hold_no_broker_domain_backend_or_stop_lock(rig, monkeypatch):
    """A second thread can acquire authority/stop lanes during each private callback."""
    _, runtime, _, broker, owner, _, _ = setupCapture(rig)
    sessions = []
    original = adapter._Session.__init__
    def initialize(session, *args):
        """Observe an actual issued provider session without replacing its logic."""
        original(session, *args)
        sessions.append(session)
    monkeypatch.setattr(adapter._Session, "__init__", initialize)
    checked = []
    def sink(event):
        """Probe locks from another thread; same-thread RLock probes are insufficient."""
        ticket = sessions[-1]
        lanes = (ticket.capture.lane, owner._service._lane,
            ticket.adapter.owner.lane, ticket.stopLane, ticket.finalizeLane)
        def probe():
            """Acquire each unrelated lane with a bounded deadline and release it."""
            for lane in lanes:
                assert lane.acquire(timeout=1)
                lane.release()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(probe).result(timeout=6)
        checked.append(event.phase)
        return True
    with broker.scope(owner, correlation=("turn",), sink=sink):
        runtime.invokeCapability("run@1", "A")
    assert checked == ["serialized", "transport-attempt", "response-headers-observed", "final"]


def test_generic_job_diagnostics_exclude_private_sink_error(rig):
    """Ordinary sink messages and their cause chains never enter Job failure text."""
    _, runtime, _, broker, owner, _, _ = setupCapture(rig)
    def sink(event):
        """Inject distinctive private content with an explicit nested cause."""
        if event.phase == "serialized":
            try:
                raise ValueError(SECRET)
            except ValueError as cause:
                raise OSError(SECRET) from cause
        return True
    with pytest.raises(EvidenceError) as caught:
        with broker.scope(owner, correlation=("turn",), sink=sink):
            result = runtime.runJob("run@1", "A", job=Job.new())
    assert result.state is JobState.FAILED
    assert SECRET not in repr(result)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None and caught.value.__context__ is None
