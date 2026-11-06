# file: tests/backend/application/test_processPrimaryR1.py ; version: 1
"""Ordered cross-thread critical-primary arbitration, including false-valued objects."""
from pathlib import Path
import sys
import time
import traceback
from threading import Event, Thread

import pytest

from backend.orchestration.cancellation import CancellationSignal
from backend.process import capture
from backend.process.context import ProcessFacade
from backend.process.runtime import ProcessRunner, ProcessToolDefinition, ProcessToolRegistry
from backend.tracing.exceptionSnapshot import captureExceptionSnapshot
from backend.workspace import EphemeralWorkspace


class FalseCritical(BaseException):
    """Exercise exception identity independently of its arbitrary boolean value."""

    def __bool__(self):
        """Return false to catch exception selection using boolean operators."""
        return False


@pytest.fixture
def bound():
    """Bind the actual public process facade to exact test-owned workspace/signal."""
    workspace = EphemeralWorkspace()
    runner = ProcessRunner(ProcessToolRegistry((
        ProcessToolDefinition("python", Path(sys.executable).resolve()),)))
    signal = CancellationSignal()
    facade = ProcessFacade(runner=runner, requireValid=lambda: None)
    facade._bind(capture.ProcessInvocation(signal=signal, workspace=workspace))
    yield facade, workspace, runner, signal
    deadline = time.monotonic() + 5
    while runner._active is not None:
        assert time.monotonic() < deadline, "Exact test ownership did not resolve."
        time.sleep(.005)
    assert not signal._callbacks
    workspace.close()


def ticket(bound, *, cleanupSeconds=1):
    """Prepare a real configured child whose argv must remain private on failure."""
    facade, workspace, _, _ = bound
    return facade.prepare("python", ("-c", "pass # PRIVATE_REQUEST_982"),
                          workingDirectory=workspace.root, timeoutSeconds=2, cleanupSeconds=cleanupSeconds)


def assertFirst(first, caught, runner, *, confirmed=True):
    """Check exact identity, cleanup, no generic evidence leakage and no live roots."""
    assert caught.value is first
    evidence = first.processEvidence
    assert evidence.cleanup.disposition == ("confirmed" if confirmed else "unknown")
    assert "PRIVATE_REQUEST_982" not in repr(captureExceptionSnapshot(first))
    assert "PRIVATE_REQUEST_982" not in "".join(traceback.format_exception(first))
    if confirmed:
        assert runner._active is None
        assert evidence.cleanup.workersSettled
        assert evidence.request.requestId not in capture._pending


@pytest.mark.parametrize("falseFirst", [False, True])
def test_worker_first_then_caller_and_unregister_are_secondary(bound, monkeypatch, falseFirst):
    """A stored worker object wins over explicitly later caller/withdrawal exits."""
    first = FalseCritical("first") if falseFirst else KeyboardInterrupt("first")
    second, third = SystemExit("second"), KeyboardInterrupt("third")
    original = capture._Operation.__init__
    calls = []
    def instrument(operation, *args, **kwargs):
        """Inject the caller interruption only after the real worker recorded first."""
        original(operation, *args, **kwargs)
        native = operation.done.wait
        injected = False
        def wait(timeout=None):
            """Enforce observation order rather than assume a scheduling delay."""
            nonlocal injected
            ready = native(timeout)
            if ready and operation.primary is first and not injected:
                injected = True
                calls.append("caller-second")
                raise second
            return ready
        operation.done.wait = wait
    monkeypatch.setattr(capture._Operation, "__init__", instrument)
    register = bound[3].registerCallback
    def registerWithFailure(callback):
        """Withdraw actual signal registration, then inject a third critical exit."""
        unregister = register(callback)
        def withdraw():
            """Retain the third exit as secondary after real callback withdrawal."""
            unregister()
            calls.append("unregister-third")
            raise third
        return withdraw
    monkeypatch.setattr(bound[3], "registerCallback", registerWithFailure)
    def ack(request):
        """Raise the exact first object before any native spawn attempt."""
        raise first
    with pytest.raises(BaseException) as caught:
        bound[0].execute(ticket(bound), beforeLaunch=ack)
    assert calls == ["caller-second", "unregister-third"]
    assertFirst(first, caught, bound[2])
    assert first.processEvidence.launchState == "not-attempted"


@pytest.mark.parametrize("falseFirst", [False, True])
def test_caller_first_then_worker_is_secondary(bound, monkeypatch, falseFirst):
    """Always preferring the worker is wrong when caller arbitration happened first."""
    first = FalseCritical("first") if falseFirst else SystemExit("first")
    second, gate = KeyboardInterrupt("worker-second"), Event()
    original, record = capture._Operation.__init__, capture._Operation.recordCritical
    ordering = []
    def remember(operation, error):
        """Release ACK only AFTER actual operation-owned caller-first arbitration."""
        record(operation, error)
        if error is first:
            ordering.append("caller-recorded")
            gate.set()
        elif error is second:
            ordering.append("worker-recorded")
    monkeypatch.setattr(capture._Operation, "recordCritical", remember)
    def instrument(operation, *args, **kwargs):
        """Raise caller-first on its initial wait while ACK is still gated."""
        original(operation, *args, **kwargs)
        native, injected = operation.done.wait, False
        def wait(timeout=None):
            """Deliver one exact caller primary before permitting worker's second."""
            nonlocal injected
            if not injected:
                injected = True
                raise first
            return native(timeout)
        operation.done.wait = wait
    monkeypatch.setattr(capture._Operation, "__init__", instrument)
    def ack(request):
        """A critical worker exit cannot precede the observed caller latch."""
        assert gate.wait(3)
        raise second
    with pytest.raises(BaseException) as caught:
        bound[0].execute(ticket(bound), beforeLaunch=ack)
    assert ordering == ["caller-recorded", "worker-recorded"]
    assertFirst(first, caught, bound[2])


@pytest.mark.parametrize("falseFirst", [False, True])
def test_real_reader_first_beats_later_caller_and_pipe_close(bound, monkeypatch, falseFirst):
    """Real capture reader's first object outranks caller wait and reader cleanup."""
    first = FalseCritical("reader-first") if falseFirst else KeyboardInterrupt("reader-first")
    second, third = SystemExit("caller-second"), KeyboardInterrupt("close-secondary")
    popen, original = capture.subprocess.Popen, capture._Operation.__init__
    class Reader:
        """Own a real native stdout pipe with ordered read/close fault injection."""

        def __init__(self, pipe):
            """Retain the exact owned descriptor, never another process's handle."""
            self.pipe = pipe

        @property
        def closed(self):
            """Report the actual descriptor state after independent close."""
            return self.pipe.closed

        def read(self, count):
            """Observe reader-first before the final caller injection is enabled."""
            raise first

        def close(self):
            """Dispose descriptor then raise a distinct secondary cleanup object."""
            self.pipe.close()
            raise third
    def owned(*args, **kwargs):
        """Start exactly the configured synthetic interpreter child."""
        child = popen(*args, **kwargs)
        child.stdout = Reader(child.stdout)
        return child
    monkeypatch.setattr(capture.subprocess, "Popen", owned)
    def instrument(operation, *args, **kwargs):
        """Inject caller second only after native reader's latch is visible."""
        original(operation, *args, **kwargs)
        native, injected = operation.done.wait, False
        def wait(timeout=None):
            """Require the real supervisor to finish after the reader observation."""
            nonlocal injected
            ready = native(timeout)
            if ready and operation.primary is first and not injected:
                injected = True
                raise second
            return ready
        operation.done.wait = wait
    monkeypatch.setattr(capture._Operation, "__init__", instrument)
    with pytest.raises(BaseException) as caught:
        bound[0].execute(ticket(bound), beforeLaunch=lambda request: True)
    assertFirst(first, caught, bound[2])
    assert first.processEvidence.cleanup.childReaped


@pytest.mark.parametrize("critical", [False, True])
def test_release_failure_cannot_replace_earlier_primary(bound, monkeypatch, critical):
    """Interrupted release retains unknown roots without replacing the first object."""
    first = FalseCritical("worker-first")
    second = SystemExit("release-second") if critical else ValueError("PRIVATE_SECONDARY_684")
    original, owners = capture._Operation.release, []
    def interrupted(operation):
        """Fail before touching actual exact-resource pin/registry ownership."""
        owners.append(operation)
        raise second
    monkeypatch.setattr(capture._Operation, "release", interrupted)
    def ack(request):
        """Raise worker-first before any child can exist."""
        raise first
    try:
        with pytest.raises(BaseException) as caught:
            bound[0].execute(ticket(bound), beforeLaunch=ack)
        assertFirst(first, caught, bound[2], confirmed=False)
        assert bound[2]._quarantined.is_set()
        assert owners[0].request.requestId in capture._pending
        assert "PRIVATE_SECONDARY_684" not in repr(captureExceptionSnapshot(first))
    finally:
        # Resume the exact original test-owned release after removing injection;
        # no force-clearing an owner or borrowing/terminating a native child.
        for operation in owners:
            original(operation)


@pytest.mark.parametrize("falseFirst", [False, True])
def test_caller_start_first_beats_later_worker(bound, monkeypatch, falseFirst):
    """An interruption after thread creation still uses the operation-wide latch."""
    first = FalseCritical("caller-start-first") if falseFirst else SystemExit("caller-start-first")
    second, entered, release = KeyboardInterrupt("worker-second"), Event(), Event()
    native, record = Thread.start, capture._Operation.recordCritical
    observed = []
    def remember(operation, error):
        """Allow worker's second observation only after caller's first is latched."""
        record(operation, error)
        if error is first:
            observed.append("caller")
            release.set()
        elif error is second:
            observed.append("worker")
    monkeypatch.setattr(capture._Operation, "recordCritical", remember)
    def start(worker):
        """Raise from caller start only after the real supervisor entered ACK."""
        native(worker)
        if worker.name == "actant-process-supervisor":
            assert entered.wait(3)
            raise first
    monkeypatch.setattr(Thread, "start", start)
    def ack(request):
        """Hold actual ACK frame while caller's start path arbitrates first."""
        entered.set()
        assert release.wait(3)
        raise second
    with pytest.raises(BaseException) as caught:
        bound[0].execute(ticket(bound), beforeLaunch=ack)
    assert observed == ["caller", "worker"]
    assertFirst(first, caught, bound[2])


@pytest.mark.parametrize("falseFirst", [False, True])
def test_retained_owner_start_is_secondary_to_recorded_worker(bound, monkeypatch, falseFirst):
    """Unknown-owner scheduling failure does not replace already recorded primary."""
    first = FalseCritical("worker-first") if falseFirst else KeyboardInterrupt("worker-first")
    second, tail, owners = SystemExit("retained-start-second"), Event(), []
    work, start = capture._Operation.work, Thread.start
    def delayedExit(operation):
        """Keep supervisor alive after its real terminal first-primary observation."""
        owners.append(operation)
        work(operation)
        assert tail.wait(3)
    monkeypatch.setattr(capture._Operation, "work", delayedExit)
    def cannotStart(worker):
        """Inject a later caller critical error while scheduling retained cleanup."""
        if worker.name == "actant-process-retained-owner":
            raise second
        return start(worker)
    monkeypatch.setattr(Thread, "start", cannotStart)
    def ack(request):
        """Raise original before any child exists."""
        raise first
    value = ticket(bound, cleanupSeconds=.03)
    try:
        with pytest.raises(BaseException) as caught:
            bound[0].execute(value, beforeLaunch=ack)
        assertFirst(first, caught, bound[2], confirmed=False)
        assert owners[0].request.requestId in capture._pending
    finally:
        tail.set()
        for operation in owners:
            operation.reap()


@pytest.mark.parametrize("falseFirst", [False, True])
def test_caller_first_then_actual_reader_is_secondary(bound, monkeypatch, falseFirst):
    """A later reader critical object must not override a caller already latched."""
    first = FalseCritical("caller-first") if falseFirst else SystemExit("caller-first")
    second, entered, release = KeyboardInterrupt("reader-second"), Event(), Event()
    original, native, record = capture._Operation.__init__, capture.subprocess.Popen, capture._Operation.recordCritical
    observed = []
    def remember(operation, error):
        """Publish test release only after operation-owned arbitration has returned."""
        record(operation, error)
        if error is first:
            observed.append("caller")
            release.set()
        elif error is second:
            observed.append("reader")
    monkeypatch.setattr(capture._Operation, "recordCritical", remember)
    class Reader:
        """Hold a native read's critical observation behind the caller latch."""

        def __init__(self, pipe):
            """Retain only the exact native pipe owned by this test child."""
            self.pipe = pipe

        @property
        def closed(self):
            """Report actual descriptor close status."""
            return self.pipe.closed

        def read(self, count):
            """Wait for known caller-first observation before reader-second."""
            entered.set()
            assert release.wait(3)
            raise second

        def close(self):
            """Dispose only the native pipe this exact reader owns."""
            self.pipe.close()
    def owned(*args, **kwargs):
        """Launch a real bounded synthetic child and wrap its stdout only."""
        child = native(*args, **kwargs)
        child.stdout = Reader(child.stdout)
        return child
    monkeypatch.setattr(capture.subprocess, "Popen", owned)
    def instrument(operation, *args, **kwargs):
        """Inject caller first once reader is blocked before its own observation."""
        original(operation, *args, **kwargs)
        wait, injected = operation.done.wait, False
        def observe(timeout=None):
            """Use the actual read-entry barrier, not a guessed sleep."""
            nonlocal injected
            ready = wait(timeout)
            if entered.is_set() and not injected:
                injected = True
                raise first
            return ready
        operation.done.wait = observe
    monkeypatch.setattr(capture._Operation, "__init__", instrument)
    with pytest.raises(BaseException) as caught:
        bound[0].execute(ticket(bound), beforeLaunch=lambda request: True)
    assert observed == ["caller", "reader"]
    assertFirst(first, caught, bound[2])
