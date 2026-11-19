# file: tests/first_party/llmDrivers/test_llamaCppAbortCleanup.py ; version: 1
"""Exact-task cancellation through actual issued ER-01/02 calls with controlled IO."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.llm.llmTypes import LlmQuery
from backend.llm.errors import LlmExecutionCleanupError, LlmProviderProtocolError
from backend.orchestration import Job, JobState
from tests.first_party.llmDrivers.test_llamaCppExecutionResources import (
    rig, adapter, capability, stream,
)


def slots(task=11, active=False):
    """Provide exact two-slot native identity, leaving the peer independently active."""
    return [{'id': 0, 'id_task': task, 'is_processing': active},
            {'id': 1, 'id_task': 25, 'is_processing': True}]


def prepare(rig, monkeypatch, *, binding=None, cleanup=None, on_completed=False):
    """Issue a real Job/call while retaining its private provider ticket for assertions."""
    host, create, _, processes, requests, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability('switch@1', 'A')
    state['slotReplies'] = [slots(10), binding if binding is not None else slots(active=True)]
    state['slots'] = slots() if cleanup is None else cleanup
    sessions = []
    original = adapter._Session.bindNativeTask

    def bind(session):
        """Observe the actual issued session without replacing native validation."""
        sessions.append(session)
        original(session)

    monkeypatch.setattr(adapter._Session, 'bindNativeTask', bind)
    job = Job.new()

    def observe(event):
        """Cancel on the selected real parser boundary, never supply cleanup effects."""
        if event.eventType == ('completed' if on_completed else 'delta'):
            job.requestCancellation()

    def call(ctx, payload):
        """Use the granted invocation facade with a finite observation budget."""
        return ctx.llm.run(providerName='llama.cpp', model='A',
            query=LlmQuery(formatId='text/plain', payload='question'),
            providerOptions={'timeoutSeconds': 0.15}, streamObserver=observe)

    capability(runtime, 'cancel@1', call)
    return host, runtime, job, sessions, processes, requests, state


@pytest.mark.parametrize('on_completed', [False, True])
def test_exact_task_release_drains_cancelled_job_and_allows_idle_transition(rig, monkeypatch, on_completed):
    """Cancellation/natural-completion races retain Job rollback while releasing safe claims."""
    host, runtime, job, sessions, processes, _, state = prepare(rig, monkeypatch, on_completed=on_completed)
    assert runtime.runJob('cancel@1', job=job).state is JobState.CANCELLED
    session = sessions[0]
    assert session.nativeTask == 11 and session.completionId == 'chat-1'
    assert session.cleanupEffect == adapter._OK and session.readerDone.is_set()
    assert host.executionResources.snapshots[0].operations == 0
    assert host.executionResources.snapshots[0].state.value == 'ready'
    assert not processes[0].terminated
    state['slots'] = [{'id': 0, 'is_processing': False}, {'id': 1, 'is_processing': False}]
    runtime.invokeCapability('switch@1', 'B')
    assert len(processes) == 2 and processes[0].terminated
    assert job.state is JobState.CANCELLED


@pytest.mark.parametrize('binding', [
    slots(task=None, active=True), slots(task=True, active=True), slots(task=-1, active=True),
    slots(task=10, active=True), slots(task=9, active=True), slots(active=False),
    [{'id': 0, 'is_processing': True}, {'id': 1, 'is_processing': False}],
])
def test_missing_stale_or_unbound_native_identity_keeps_unknown(rig, monkeypatch, binding):
    """No amount of later idle can replace a valid pre-abort active-task binding."""
    host, runtime, job, sessions, processes, _, _ = prepare(rig, monkeypatch, binding=binding)
    assert runtime.runJob('cancel@1', job=job).state is JobState.CANCELLED
    assert sessions[0].nativeTask is None
    assert host.executionResources.snapshots[0].state.value == 'unknown'
    assert not processes[0].terminated


@pytest.mark.parametrize('cleanup', [
    slots(task=12), slots(task=True), slots(task=None), slots(active=True),
    [{'id': 0, 'id_task': 11, 'is_processing': False}, {'id': 0, 'is_processing': False}],
])
def test_wrong_slot_task_or_deadline_does_not_release_claim(rig, monkeypatch, cleanup):
    """Conflicting native identity, malformed sets and unacknowledged stops remain quarantined."""
    host, runtime, job, sessions, processes, _, state = prepare(rig, monkeypatch, cleanup=cleanup)
    assert runtime.runJob('cancel@1', job=job).state is JobState.CANCELLED
    ticket = sessions[0]
    assert ticket.cleanupEffect == adapter._UNKNOWN
    assert host.executionResources.snapshots[0].state.value == 'unknown'
    state['slots'] = slots()  # Late evidence cannot repair terminal unknown.
    assert ticket.adapter.closeExecution(ticket.admission, ticket, adapter.LlmSessionExit.CANCELLED).effect == adapter._UNKNOWN
    assert host.executionResources.snapshots[0].state.value == 'unknown'
    assert not processes[0].terminated


@pytest.mark.parametrize('replacement', ['process', 'generation'])
def test_changed_process_or_generation_cannot_acknowledge_original_task(rig, monkeypatch, replacement):
    """A valid-looking slot response from a different residency cannot free the old claim."""
    host, runtime, job, sessions, processes, _, state = prepare(rig, monkeypatch)

    def change(request):
        """Replace the exact identity only once the original task was actively bound."""
        if request.full_url.endswith('/slots') and sessions and sessions[0].nativeTask is not None:
            session = sessions[0]
            if replacement == 'process':
                session.driver.process = type(processes[0])(['replacement'])
            else:
                session.adapter.owner.resident = tuple([*session.resident])

    state['onHttp'] = change
    assert runtime.runJob('cancel@1', job=job).state is JobState.CANCELLED
    assert host.executionResources.snapshots[0].state.value == 'unknown'
    assert not processes[0].terminated


def test_duplicate_successful_finalization_does_not_repeat_native_io(rig, monkeypatch):
    """Accepted finalization is idempotent and cannot release another operation's slot."""
    _, runtime, job, sessions, _, requests, _ = prepare(rig, monkeypatch)
    runtime.runJob('cancel@1', job=job)
    session = sessions[0]
    count = len(requests)
    for _ in range(2):
        session.stop()
        assert session.adapter.closeExecution(session.admission, session, adapter.LlmSessionExit.CANCELLED).effect == adapter._OK
    assert len(requests) == count


@pytest.mark.parametrize('sent', [False, True])
def test_pre_header_cancellation_interrupts_owned_transport_without_fabricating_task(rig, monkeypatch, sent):
    """Known pre-send cancellation releases safely; unbound accepted IO remains unknown."""
    host, create, _, processes, _, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability('switch@1', 'A')
    entered, release = Event(), Event()
    base = adapter._transport.OwnedHttpTransport

    class PreHeader(base):
        """Hold fake header open while the actual signal can interrupt its owned transport."""

        def open(self, request, **kwargs):
            """Distinguish known unsent IO from a request possibly accepted by the server."""
            if not request.full_url.endswith('/v1/chat/completions'):
                return super().open(request, **kwargs)
            self.mayHaveSent = sent
            entered.set()
            assert release.wait(3)
            assert self.stopped
            raise InterruptedError('Controlled pre-header interruption.')

    monkeypatch.setattr(adapter._transport, 'OwnedHttpTransport', PreHeader)
    job = Job.new()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.runJob, 'run@1', job=job)
        assert entered.wait(2)
        try:
            job.requestCancellation()
        finally:
            release.set()
        assert pending.result(3).state is JobState.CANCELLED
    assert host.executionResources.snapshots[0].state.value == ('unknown' if sent else 'ready')
    assert not processes[0].terminated


def test_response_arriving_after_observation_deadline_cannot_confirm_drain(rig, monkeypatch):
    """Even matching idle is unknown when its acknowledgement arrives after the budget."""
    host, runtime, job, sessions, _, _, state = prepare(rig, monkeypatch)
    clock = {'now': 0.0}
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: clock['now'])

    def slow(request):
        """Advance the controlled clock while the post-stop slot request is outstanding."""
        if request.full_url.endswith('/slots') and sessions and sessions[0].stopped:
            clock['now'] += 1

    state['onHttp'] = slow
    runtime.runJob('cancel@1', job=job)
    assert host.executionResources.snapshots[0].state.value == 'unknown'


def test_foreign_queued_work_on_the_admitted_slot_is_rejected_before_send(rig):
    """A preexisting busy native slot cannot be relabeled as the new admitted request."""
    host, create, _, processes, requests, state, _, _, _ = rig
    runtime = create()
    runtime.invokeCapability('switch@1', 'A')
    state['slots'] = slots(active=True)
    with pytest.raises(LlmProviderProtocolError, match='already active'):
        runtime.invokeCapability('run@1', 'A')
    assert not any(url.endswith('/completions') for url, _ in requests)
    assert host.executionResources.snapshots[0].state.value == 'unknown'
    assert not processes[0].terminated


def test_buffer_final_close_failure_keeps_original_cancelled_job_and_unknown(rig, monkeypatch):
    """Cleanup uncertainty never promotes or overwrites the owning cancelled workflow."""
    host, runtime, job, sessions, _, _, _ = prepare(rig, monkeypatch)
    base = adapter._transport.OwnedHttpTransport

    class FailedClose(base):
        """Fail only local reader finalization after executing the actual fake response close."""

        def open(self, request, **kwargs):
            """Leave baseline/binding slot reads ordinary; target only the inference stream."""
            self.chat = request.full_url.endswith('/v1/chat/completions')
            return super().open(request, **kwargs)

        def close(self):
            """Report unavailable final close evidence instead of successful cleanup."""
            super().close()
            if self.chat:
                raise OSError('Controlled close failure.')

    monkeypatch.setattr(adapter._transport, 'OwnedHttpTransport', FailedClose)
    assert runtime.runJob('cancel@1', job=job).state is JobState.CANCELLED
    assert sessions[0].uncertain and sessions[0].cleanupEffect == adapter._UNKNOWN
    assert host.executionResources.snapshots[0].state.value == 'unknown'


@pytest.mark.parametrize('target', ['host', 'application'])
def test_revocation_retains_cleanup_authority_until_bound_task_drain(rig, monkeypatch, target):
    """Host/owner revocation cannot dispose a live reader; its later exact cleanup may succeed."""
    host, runtime, job, sessions, processes, _, state = prepare(rig, monkeypatch)
    entered, release = Event(), Event()

    def paused(ctx, payload):
        """Hold the granted operation after its actual provider task was bound."""
        def observe(event):
            """Expose a deterministic lifecycle boundary before another native event is read."""
            if not entered.is_set():
                entered.set()
                assert release.wait(3)

        return ctx.llm.run(providerName='llama.cpp', model='A',
            query=LlmQuery(formatId='text/plain', payload='question'),
            providerOptions={'timeoutSeconds': 0.15}, streamObserver=observe)

    capability(runtime, 'paused@1', paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.runJob, 'paused@1', job=job)
        assert entered.wait(2)
        try:
            with pytest.raises(LlmExecutionCleanupError):
                if target == 'host':
                    host.stop()
                else:
                    host.closeApplicationRun(runtime.applicationRun.applicationRunId)
            assert not processes[0].terminated
        finally:
            release.set()
        assert pending.result(3).state is not JobState.SUCCEEDED
    assert sessions[0].cleanupEffect == adapter._OK
    # A later ordinary lifecycle drain may now dispose, through accepted ER-01/02.
    host.stop()
    assert processes[0].terminated
