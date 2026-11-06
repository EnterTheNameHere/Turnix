# file: tests/backend/application/test_boundedProcess.py ; version: 1
"""Actual Job/child ownership and deterministic ACK/cancellation/privacy regressions."""
from __future__ import annotations

import copy
import json
import sys
import time
import traceback
from dataclasses import FrozenInstanceError
from contextvars import ContextVar
from pathlib import Path
from threading import Event, Thread

import pytest

from backend.application.applicationRuntime import ApplicationRuntime
from backend.context.codeEntryContext import CodeEntryIdentity
from backend.orchestration import Job, JobState
from backend.packs.runtime import PackResolver
from backend.process import ProcessOperationError, ProcessCancelled, ProcessCleanupPendingError
from backend.process import capture
from backend.registration import RegistrationScope
from backend.tracing.exceptionSnapshot import captureExceptionSnapshot


def waitFor(predicate, seconds=5):
    """Bound test synchronization without guessing a worker's scheduling delay."""
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "Owned test worker did not resolve."
        time.sleep(.005)


@pytest.fixture
def runtime():
    """Start one actual ApplicationRun with only its test interpreter configured."""
    runtime = ApplicationRuntime(appPackId="test.bounded",
        packResolver=PackResolver(roots=()),
        config={"processTools": {"python": str(Path(sys.executable).resolve())}})
    runtime.start()
    identity = CodeEntryIdentity(runtime.applicationRun.application.applicationId,
        runtime.applicationRun.applicationRunId, "test.bounded", "1.0.0", "main",
        "bounded-entry", "0" * 64, "python-source@1", "test")
    runtime.registerCodeEntry(identity, Path.cwd())
    def invoke(handler, job=None):
        """Publish an actual capability and execute it as a real mutation Job."""
        scope = RegistrationScope()
        runtime.capabilities.register(scope, ownerId=identity.codeEntryInstanceId,
            capabilityId="test.bounded@1", handler=handler)
        scope.publish()
        try:
            return runtime.runJob("test.bounded@1", job=job)
        finally:
            scope.withdraw()
    yield runtime, invoke
    waitFor(lambda: runtime.processRunner._active is None)
    runtime.close()


def prepared(ctx, code="print('ok')", **kwargs):
    """Prepare one exact synthetic child against the invocation workspace."""
    path = Path(ctx.workspace.materializeText("candidate.py", "value = 1\r\n"))
    return ctx.process.prepare("python", ("-c", code), workingDirectory=path.parent, **kwargs)


def test_normal_nonzero_exact_ack_and_private_raw_streams(runtime):
    """Nonzero completion has exact ACK intent, joined workers and raw CRLF bytes."""
    host, invoke = runtime
    seen = []
    ambient = ContextVar("test.process.ambient-authority", default=None)
    def handler(ctx, payload):
        """Capture exact intent before starting an owned child."""
        token = ambient.set("caller-only-authority")
        ticket = prepared(ctx, "import os; os.write(1,b'raw\\r\\n\\xff'); raise SystemExit(7)")
        def ack(request):
            """Record only immutable intent from outside runtime-owned callback locks."""
            seen.append(request)
            assert request.jobId is not None
            assert ambient.get() is None
            assert host._lane.acquire(blocking=False) is False
            return True
        value = ctx.process.execute(ticket, beforeLaunch=ack)
        assert value.request is seen[0]
        assert value.stdout.data == b"raw\r\n\xff"
        assert value.exitCode == 7 and value.status == "completed"
        assert value.cleanup.disposition == "confirmed" and value.cleanup.workersSettled
        assert value.stdout.eof and value.stderr.eof
        assert not host.processRunner._active
        with pytest.raises(FrozenInstanceError):
            value.status = "failed"
        json.dumps(value.snapshot())
        with pytest.raises(RuntimeError):
            ctx.process.execute(ticket, beforeLaunch=lambda request: True)
        with pytest.raises(TypeError):
            copy.copy(ticket)
        ambient.reset(token)
        return "ok"
    result = invoke(handler)
    assert result.state is JobState.SUCCEEDED


@pytest.mark.parametrize("accept", [False, None, 1])
def test_ack_rejection_never_spawns(runtime, monkeypatch, accept):
    """Only literal True is a valid pre-launch durable acknowledgement."""
    calls = []
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *a, **k: calls.append(a))
    def handler(ctx, payload):
        """Reject the intent before any process launch attempt."""
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(prepared(ctx), beforeLaunch=lambda request: accept)
        value = caught.value.evidence
        assert value.launchState == "not-attempted" and value.exitCode is None
        assert value.failureCategory == "ack-rejected"
        assert value.cleanup.disposition == "confirmed" and value.cleanup.ackResolved
    assert runtime[1](handler).state is JobState.SUCCEEDED
    assert not calls


def test_spawn_error_and_ack_error_trace_evidence_are_private(runtime, monkeypatch):
    """Existing snapshotter and actual Job error traces see no raw private attributes."""
    sentinel = "PRIVATE_ARG_CWD_OUTPUT_ACK_741"
    observed = []
    events = []
    monkeypatch.setattr(runtime[0].tracer, "emitEvent", lambda **event: events.append(event))
    def failSpawn(*args, **kwargs):
        """Inject an OS launch failure whose raw message must never escape."""
        raise OSError(sentinel)
    monkeypatch.setattr(capture.subprocess, "Popen", failSpawn)
    def handler(ctx, payload):
        """Propagate the safe exception through the actual runtime failure path."""
        ticket = prepared(ctx, sentinel)
        try:
            ctx.process.execute(ticket, beforeLaunch=lambda request: True)
        except ProcessOperationError as error:
            observed.append(error)
            assert sentinel in error.evidence.request.arguments[-1]
            assert error.evidence.cleanup.disposition == "confirmed"
            assert sentinel not in repr(captureExceptionSnapshot(error))
            assert sentinel not in "".join(traceback.format_exception(error))
            raise
    result = runtime[1](handler)
    assert result.state is JobState.FAILED
    assert observed and sentinel not in str(result.error)
    assert any(e["label"] == "capability-invocation-failed" for e in events)
    assert sentinel not in repr(events)
    grouped = ExceptionGroup("Process cleanup failure.", [observed[0]])
    assert sentinel not in repr(captureExceptionSnapshot(grouped))
    assert sentinel not in "".join(traceback.format_exception(grouped))


def test_ack_exception_context_safe(runtime):
    """Ordinary callback exceptions are sanitized without a formatted cause chain."""
    def handler(ctx, payload):
        """Raise callback-private text before launch."""
        def ack(request):
            """Simulate private archive failure text that must not reach tracing."""
            raise ValueError("PRIVATE_CALLBACK_873")
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(prepared(ctx), beforeLaunch=ack)
        assert caught.value.evidence.failureCategory == "ack-failed"
        assert "PRIVATE_CALLBACK_873" not in repr(captureExceptionSnapshot(caught.value))
        assert "PRIVATE_CALLBACK_873" not in "".join(traceback.format_exception(caught.value))
    assert runtime[1](handler).state is JobState.SUCCEEDED


def test_blocked_late_ack_retains_workspace_and_sticky_quarantine(runtime, monkeypatch):
    """Returned unknown roots callback/pin, stops same-call inference and rejects late spawn."""
    host, invoke = runtime
    monkeypatch.setattr(capture, "_TICKET_SECONDS", .03)
    release, entered, values, paths, calls = Event(), Event(), [], [], []
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *a, **k: calls.append(a))
    def handler(ctx, payload):
        """Catch unknown and prove subsequent same-capability work remains blocked."""
        ticket = prepared(ctx, cleanupSeconds=.03)
        paths.append(Path(ticket.request.workingDirectory))
        def ack(request):
            """Hold a real callback frame until test-owned release, without Context."""
            entered.set()
            assert release.wait(4)
            return True
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(ticket, beforeLaunch=ack)
        values.append(caught.value.evidence)
        assert entered.is_set()
        assert values[0].cleanup.disposition == "unknown"
        assert not values[0].cleanup.ackResolved and not values[0].cleanup.workersSettled
        with pytest.raises(ProcessCleanupPendingError):
            ctx.process.run("python", ("-c", "print('must not run')"))
        with pytest.raises(ProcessCleanupPendingError):
            ctx.process.prepare("python", (), workingDirectory=paths[0])
        with pytest.raises(ProcessCleanupPendingError):
            ctx.llm.run(providerName="missing", query=None)
        with pytest.raises(ProcessCleanupPendingError):
            ctx.capabilities.call("missing@1")
    try:
        result = invoke(handler)
        assert result.state is JobState.FAILED
        assert paths[0].exists() and not calls
        with pytest.raises(ProcessCleanupPendingError):
            host.runJob("missing@1")
        with pytest.raises(ExceptionGroup):
            host.close()
        with pytest.raises(ProcessCleanupPendingError):
            host.close()
        assert paths[0].exists()
        before = values[0].snapshot()
    finally:
        release.set()
        waitFor(lambda: host.processRunner._active is None)
    assert not paths[0].exists() and not calls
    assert values[0].snapshot() == before
    with pytest.raises(ProcessCleanupPendingError):
        host.processRunner.requireReady()


def test_timeout_reaps_exact_child_and_keeps_partial_raw_output(runtime):
    """A real timed-out child is reaped and partial output remains private evidence."""
    def handler(ctx, payload):
        """Stop one owned synthetic sleeping child."""
        ticket = prepared(ctx, "import os,time; os.write(1,b'partial'); time.sleep(3)",
                          timeoutSeconds=.2, cleanupSeconds=1)
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(ticket, beforeLaunch=lambda request: True)
        value = caught.value.evidence
        assert value.status == "timeout" and value.stopReason == "timeout"
        assert value.stdout.data == b"partial"
        assert value.cleanup.childReaped and value.cleanup.workersSettled
        assert value.cleanup.disposition == "confirmed" and value.exitCode is not None
    assert runtime[1](handler).state is JobState.SUCCEEDED


def test_concurrent_stream_overflow_is_bounded_before_buffer_growth(runtime):
    """Concurrent native pipe producers cannot exceed either raw byte allocation cap."""
    def handler(ctx, payload):
        """Produce larger-than-cap data on both streams without a sequential drain."""
        code = ("import os,threading; "
                "t=threading.Thread(target=lambda:os.write(2,b'e'*50000)); t.start(); "
                "os.write(1,b'o'*50000); t.join()")
        ticket = prepared(ctx, code, stdoutBytes=123, stderrBytes=137, timeoutSeconds=2)
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(ticket, beforeLaunch=lambda request: True)
        value = caught.value.evidence
        assert value.status == "output-limit"
        assert len(value.stdout.data) <= 123 and value.stdout.observedBytes <= 124
        assert len(value.stderr.data) <= 137 and value.stderr.observedBytes <= 138
        assert value.stdout.limitExceeded or value.stderr.limitExceeded
        assert value.cleanup.disposition == "confirmed"
    assert runtime[1](handler).state is JobState.SUCCEEDED


def test_actual_job_cancel_before_prepare_and_legacy_admission(runtime):
    """Cancellation before admission blocks both APIs and remains a Cancelled Job."""
    job = Job.new()
    def handler(ctx, payload):
        """Request actual caller-owned Job cancellation before either process API."""
        job.requestCancellation()
        with pytest.raises(ProcessCancelled):
            prepared(ctx)
        ctx.process.run("python", ("-c", "raise SystemExit(0)"))
    assert runtime[1](handler, job).state is JobState.CANCELLED


def test_cancel_during_actual_child_uses_actual_job_signal(runtime):
    """Cancellation callback requests stop off the runtime lane and waits owned cleanup."""
    host, invoke = runtime
    job, completed = Job.new(), []
    def cancel():
        """Wait for registered native child, then request actual Job cancellation."""
        waitFor(lambda: host.processRunner._active is not None and
                host.processRunner._active.child is not None)
        job.requestCancellation()
    thread = Thread(target=cancel)
    def handler(ctx, payload):
        """Observe cancellation evidence while preserving Job semantics."""
        try:
            ctx.process.execute(prepared(ctx, "import time; time.sleep(3)"),
                                beforeLaunch=lambda request: True)
        except ProcessCancelled as error:
            completed.append(error.evidence)
            raise
    thread.start()
    try:
        assert invoke(handler, job).state is JobState.CANCELLED
    finally:
        thread.join(5)
    assert not thread.is_alive()
    assert completed[0].cleanup.disposition == "confirmed"


def test_descendant_pipe_unknown_known_exit_and_late_drain(runtime):
    """EOF held by a test-owned descendant retains pin after exact parent exit."""
    host, invoke = runtime
    values, paths = [], []
    def handler(ctx, payload):
        """Spawn only a bounded naturally-exiting test descendant retaining pipes."""
        code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c',"
                "'import time; time.sleep(.8)']); raise SystemExit(7)")
        ticket = prepared(ctx, code, timeoutSeconds=.2, cleanupSeconds=.03)
        paths.append(Path(ticket.request.workingDirectory))
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(ticket, beforeLaunch=lambda request: True)
        values.append(caught.value.evidence)
        assert values[0].exitCode == 7
        assert values[0].cleanup.disposition == "unknown"
        assert not values[0].cleanup.readersJoined
    assert invoke(handler).state is JobState.FAILED
    assert paths[0].exists()
    waitFor(lambda: host.processRunner._active is None)
    assert not paths[0].exists()


def test_same_nonexception_primary_survives_ack(runtime):
    """An exact interruption retains identity after independent worker cleanup."""
    primary = KeyboardInterrupt()
    def handler(ctx, payload):
        """Interrupt within callback without running native code."""
        def ack(request):
            """Raise the exact test-owned primary from the callback thread."""
            raise primary
        ctx.process.execute(prepared(ctx), beforeLaunch=ack)
    with pytest.raises(KeyboardInterrupt) as caught:
        runtime[1](handler)
    assert caught.value is primary
    assert primary.processEvidence.cleanup.disposition == "confirmed"


def test_reentrant_prepare_from_ack_rejected_without_deadlock(runtime):
    """A facade captured improperly by callback cannot allocate another ticket."""
    def handler(ctx, payload):
        """Test defensive reentry denial; production callbacks do not capture Context."""
        ticket = prepared(ctx)
        def ack(request):
            """Try only nonblocking process preparation, never runtime reentry."""
            with pytest.raises(RuntimeError, match="busy"):
                ctx.process.prepare("python", (), workingDirectory=request.workingDirectory)
            return True
        ctx.process.execute(ticket, beforeLaunch=ack)
    assert runtime[1](handler).state is JobState.SUCCEEDED


def test_stalled_spawn_ownership_and_cancel_registration_race(runtime, monkeypatch):
    """An already-attempted native spawn may resolve late; its exact child is stopped."""
    host, invoke = runtime
    entered, release, paths, values = Event(), Event(), [], []
    native = capture.subprocess.Popen
    def stalled(*args, **kwargs):
        """Hold inside the already-entered native launch attempt until test release."""
        entered.set()
        assert release.wait(4)
        return native(*args, **kwargs)
    monkeypatch.setattr(capture.subprocess, "Popen", stalled)
    def handler(ctx, payload):
        """Stop at finite deadline while the native attempt has no registered child."""
        ticket = prepared(ctx, "import time; time.sleep(2)", timeoutSeconds=.04, cleanupSeconds=.03)
        paths.append(Path(ticket.request.workingDirectory))
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(ticket, beforeLaunch=lambda request: True)
        values.append(caught.value.evidence)
        assert entered.is_set() and values[0].launchState == "attempted"
        assert not values[0].cleanup.spawnResolved
    try:
        assert invoke(handler).state is JobState.FAILED
        assert paths[0].exists()
    finally:
        release.set()
        waitFor(lambda: host.processRunner._active is None)
    assert not paths[0].exists()
    assert values[0].cleanup.disposition == "unknown"


def test_stalled_reader_retained_until_all_owned_workers_end(runtime, monkeypatch):
    """Known parent exit is insufficient while an owned reader still has a live frame."""
    host, invoke = runtime
    release, entered, values = Event(), Event(), []
    actual = capture._Operation._read
    def stalled(self, index, pipe, cap):
        """Delay only stdout reader; release ensures exact test resource cleanup."""
        if index == 0:
            entered.set()
            assert release.wait(4)
        return actual(self, index, pipe, cap)
    monkeypatch.setattr(capture._Operation, "_read", stalled)
    def handler(ctx, payload):
        """Retain immutable unknown even after real parent has exited normally."""
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(prepared(ctx, timeoutSeconds=.15, cleanupSeconds=.03),
                                beforeLaunch=lambda request: True)
        values.append(caught.value.evidence)
        assert entered.is_set() and values[0].cleanup.disposition == "unknown"
        assert not values[0].cleanup.readersJoined
    try:
        assert invoke(handler).state is JobState.FAILED
    finally:
        release.set()
        waitFor(lambda: host.processRunner._active is None)


def test_cancellation_inside_ack_never_enters_spawn(runtime, monkeypatch):
    """Actual Job signal requested during ACK wins admission after callback returns."""
    job, calls, values = Job.new(), [], []
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *a, **k: calls.append(a))
    def handler(ctx, payload):
        """Deliver cancellation from callback without runtime or Context access there."""
        signal = job.cancellationSignal
        def ack(request):
            """Request only the off-lane cancellation signal and acknowledge intent."""
            signal.request()
            return True
        try:
            ctx.process.execute(prepared(ctx), beforeLaunch=ack)
        except ProcessCancelled as error:
            values.append(error.evidence)
            raise
    assert runtime[1](handler, job).state is JobState.CANCELLED
    assert not calls and values[0].launchState == "not-attempted"
    assert values[0].cleanup.disposition == "confirmed"
    assert not job.cancellationSignal._callbacks


def test_expired_and_foreign_ticket_never_ack(runtime, monkeypatch):
    """Expiry and invocation identity are checked before arbitrary client callbacks."""
    monkeypatch.setattr(capture, "_TICKET_SECONDS", .01)
    tickets, called = [], []
    def first(ctx, payload):
        """Retain one ticket past its exact invocation for misuse testing."""
        tickets.append(prepared(ctx))
    assert runtime[1](first).state is JobState.SUCCEEDED
    def second(ctx, payload):
        """Reject foreign invocation and expired local intent independently."""
        with pytest.raises(TypeError):
            ctx.process.execute(tickets[0], beforeLaunch=lambda request: called.append(1))
        ticket = prepared(ctx)
        time.sleep(.02)
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(ticket, beforeLaunch=lambda request: called.append(1))
        assert caught.value.evidence.failureCategory == "expired"
        assert caught.value.evidence.cleanup.disposition == "confirmed"
    assert runtime[1](second).state is JobState.SUCCEEDED
    assert not called


def test_invalidation_attempts_independent_cleanup_without_replacing_primary(runtime, monkeypatch):
    """Workspace failure cannot prevent memory/observation close or replace interruption."""
    primary, calls, workspaces = KeyboardInterrupt(), [], []
    from backend.workspace import EphemeralWorkspace
    original = EphemeralWorkspace.close
    def failedClose(self):
        """Inject a workspace cleanup failure after recording owned workspace."""
        workspaces.append(self)
        raise ValueError("workspace cleanup injection")
    monkeypatch.setattr(EphemeralWorkspace, "close", failedClose)
    def handler(ctx, payload):
        """Inject independent cleanup actions then raise an exact first exit."""
        monkeypatch.setattr(type(ctx.memory), "close", lambda self: calls.append("memory"))
        monkeypatch.setattr(type(ctx.observations), "close", lambda self: calls.append("observations"))
        raise primary
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            result = runtime[1](handler)
            assert result.state is JobState.SUCCEEDED, repr(result.error)
        assert caught.value is primary and primary.invocationCleanupFailed
        assert set(calls) == {"memory", "observations"}
    finally:
        for workspace in workspaces:
            original(workspace)


@pytest.mark.parametrize("mode", ["ack", "timeout", "legacy"])
def test_private_failure_in_actual_capability_trace(runtime, monkeypatch, mode, tmp_path):
    """Arguments/cwd/output/callback strings stay private through real runtime errors."""
    sentinel = "PRIVATE_TRACE_668"
    directory = tmp_path / sentinel
    directory.mkdir()
    events, errors = [], []
    monkeypatch.setattr(runtime[0].tracer, "emitEvent", lambda **value: events.append(value))
    def handler(ctx, payload):
        """Raise a safe operation error, allowing runtime to trace its actual text."""
        code = "import os,time; os.write(1,b'" + sentinel + "'); time.sleep(2)"
        try:
            if mode == "legacy":
                ctx.process.run("python", ("-c", code), workingDirectory=directory, timeoutSeconds=.15)
            else:
                ticket = ctx.process.prepare("python", ("-c", code), workingDirectory=directory,
                                             timeoutSeconds=.15, cleanupSeconds=1)
                def ack(request):
                    """Expose private text only as the cause of a sanitized ACK failure."""
                    if mode == "ack":
                        raise ValueError(sentinel)
                    return True
                ctx.process.execute(ticket, beforeLaunch=ack)
        except Exception as error:
            errors.append(error)
            raise
    assert runtime[1](handler).state is JobState.FAILED
    assert any(e["label"] == "capability-invocation-failed" for e in events)
    assert sentinel not in repr(events)
    assert sentinel not in repr(captureExceptionSnapshot(errors[0]))
    assert sentinel not in "".join(traceback.format_exception(errors[0]))


@pytest.mark.parametrize("field,value", [("timeoutSeconds", float("nan")),
    ("timeoutSeconds", float("inf")), ("timeoutSeconds", True),
    ("timeoutSeconds", 0), ("timeoutSeconds", 121), ("cleanupSeconds", 6),
    ("stdoutBytes", True), ("stdoutBytes", 0), ("stderrBytes", 1048577)])
def test_invalid_capture_controls_before_any_callback(runtime, field, value):
    """Bounded API never coerces malformed numeric resource controls into execution."""
    def handler(ctx, payload):
        """Reject malformed controls while preserving ordinary Job success."""
        with pytest.raises(ValueError):
            prepared(ctx, **{field: value})
        assert runtime[0].processRunner._active is None
    assert runtime[1](handler).state is JobState.SUCCEEDED


def test_reader_failure_preserves_exact_nonexception_primary(runtime, monkeypatch):
    """A same-object critical reader failure survives cleanup and Job boundaries."""
    primary = KeyboardInterrupt()
    native = capture.subprocess.Popen
    class BrokenReader:
        """Wrap a real owned pipe with an exact injected first read failure."""

        def __init__(self, pipe):
            """Keep the real native pipe owned until independent close."""
            self.pipe = pipe

        @property
        def closed(self):
            """Report actual native descriptor close state."""
            return self.pipe.closed

        def read(self, count):
            """Raise exactly the test-owned primary without copying it."""
            raise primary

        def close(self):
            """Still close the original descriptor after the injected failure."""
            self.pipe.close()
    def wrapped(*args, **kwargs):
        """Launch a real child but inject failure on its owned stdout reader."""
        child = native(*args, **kwargs)
        child.stdout = BrokenReader(child.stdout)
        return child
    monkeypatch.setattr(capture.subprocess, "Popen", wrapped)
    def handler(ctx, payload):
        """Keep the interruption primary even if the child also exits nonzero."""
        ctx.process.execute(prepared(ctx), beforeLaunch=lambda request: True)
    with pytest.raises(KeyboardInterrupt) as caught:
        runtime[1](handler)
    assert caught.value is primary
    assert primary.processEvidence.cleanup.disposition == "confirmed"


def test_callback_ownership_root_cleared_after_resolution(runtime, monkeypatch):
    """Resolved callback/port closures are not held by retained ticket or evidence."""
    import gc
    import weakref
    class Port:
        """A standalone weakly observable archive-port analogue."""

        def __call__(self, request):
            """Acknowledge without retaining invocation authority."""
            return True
    references = []
    def handler(ctx, payload):
        """Resolve one callback and verify only private immutable evidence remains."""
        callback = Port()
        references.append(weakref.ref(callback))
        value = ctx.process.execute(prepared(ctx), beforeLaunch=callback)
        del callback
        gc.collect()
        assert references[0]() is None and value.cleanup.disposition == "confirmed"
    assert runtime[1](handler).state is JobState.SUCCEEDED


def test_cancel_after_process_completion_keeps_distinct_job_outcome(runtime, monkeypatch):
    """A completed child result does not hide cancellation of its enclosing Job."""
    actual, outcomes = capture._Operation.work, []
    def completedThenCancel(self):
        """Request cancellation only after process outcome facts are terminal."""
        actual(self)
        self.state.signal.request()
    monkeypatch.setattr(capture._Operation, "work", completedThenCancel)
    def handler(ctx, payload):
        """Retain normal process completion independently of eventual Job cancellation."""
        outcomes.append(ctx.process.execute(prepared(ctx), beforeLaunch=lambda request: True))
    assert runtime[1](handler).state is JobState.CANCELLED
    assert outcomes[0].status == "completed" and outcomes[0].cleanup.disposition == "confirmed"


def test_supervisor_start_failure_clears_exact_roots_and_keeps_errors_safe(runtime, monkeypatch):
    """Failed thread creation cannot leak a workspace pin or private failure text."""
    actual = Thread.start
    def failedStart(self):
        """Reject only the owned supervisor, leaving test/runtime infrastructure intact."""
        if self.name == "actant-process-supervisor":
            raise RuntimeError("PRIVATE_THREAD_CREATION_144")
        return actual(self)
    monkeypatch.setattr(Thread, "start", failedStart)
    def handler(ctx, payload):
        """Observe a no-spawn operational failure and complete the Job normally."""
        with pytest.raises(ProcessOperationError) as caught:
            ctx.process.execute(prepared(ctx), beforeLaunch=lambda request: True)
        assert caught.value.evidence.launchState == "not-attempted"
        assert caught.value.evidence.cleanup.disposition == "confirmed"
        assert "PRIVATE_THREAD_CREATION_144" not in repr(captureExceptionSnapshot(caught.value))
        assert runtime[0].processRunner._active is None
        assert not capture._pending
    assert runtime[1](handler).state is JobState.SUCCEEDED
