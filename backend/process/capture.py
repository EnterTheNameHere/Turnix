# file: backend/process/capture.py ; version: 1
"""Bounded configured-child ownership, outside runtime lanes and client callbacks."""
from __future__ import annotations

import base64
import math
import os
import subprocess
import time
from contextlib import contextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass, fields
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import uuid4

from backend.orchestration.cancellation import ExecutionCancelled
from backend.process.runtime import ProcessExecutionError

_pending = {}
_pendingLane = RLock()
_job = ContextVar("actant.process.actual-job", default=None)
_TICKET_SECONDS = 30.0


@contextmanager
def processJobScope(job):
    """Bind actual Job identity/signal while runtime executes its capability."""
    token = _job.set((job.jobId, job.cancellationSignal))
    try:
        yield
    finally:
        _job.reset(token)


class _PrivateRecord:
    """Opt-in immutable evidence; incidental tracing sees a constant representation."""

    def __repr__(self):
        """Hide private fields from existing exception-attribute repr fallback."""
        return "<" + type(self).__name__ + ">"

    def snapshot(self):
        """Explicitly export detached private evidence, encoding retained raw bytes."""
        result = {} if self._schema is None else {"schemaId": self._schema}
        for field in fields(self):
            value = getattr(self, field.name)
            key = field.name
            if isinstance(value, _PrivateRecord):
                value = value.snapshot()
            elif type(value) is bytes:
                key, value = key + "Base64", base64.b64encode(value).decode("ascii")
            elif type(value) is tuple:
                value = list(value)
            result[key] = value
        return result


@dataclass(frozen=True, slots=True, repr=False)
class ProcessRequest(_PrivateRecord):
    """Exact resolved process intent; it grants no execution authority."""

    _schema = "actant.process.request@1"
    requestId: str
    applicationRunId: str | None
    codeEntryInstanceId: str | None
    invocationId: str
    jobId: str | None
    toolName: str
    configuredExecutable: str
    resolvedExecutable: str
    arguments: tuple[str, ...]
    workingDirectory: str
    timeoutSeconds: float
    stdoutBytes: int
    stderrBytes: int
    cleanupSeconds: float
    environmentPolicy: str
    preparedTimeNs: int

    def snapshot(self):
        """Export resolution observation without claiming loaded image identity."""
        value = super(ProcessRequest, self).snapshot()
        value["executableObservation"] = dict(status="not-hashed", bytes=None, sha256=None,
                                            observedTimeNs=self.preparedTimeNs)
        return value


@dataclass(frozen=True, slots=True, repr=False)
class CapturedStream(_PrivateRecord):
    """Retain only bounded raw bytes and observed read facts, never inferred totals."""

    _schema = None
    data: bytes
    observedBytes: int
    eof: bool
    limitExceeded: bool
    readFailure: bool


@dataclass(frozen=True, slots=True, repr=False)
class ProcessCleanup(_PrivateRecord):
    """State owned-child/ACK/capture obligations, without process-tree claims."""

    _schema = None
    scope: str
    childReaped: bool
    readersJoined: bool
    pipesClosed: bool
    spawnResolved: bool
    ackResolved: bool
    workersSettled: bool
    stopAttempted: bool
    disposition: str
    retentionId: str | None
    workspaceDisposition: str


@dataclass(frozen=True, slots=True, repr=False)
class ProcessOutcome(_PrivateRecord):
    """Detached outcome remains unchanged if retained cleanup later resolves."""

    _schema = "actant.process.outcome@1"
    request: ProcessRequest
    status: str
    phase: str
    launchState: str
    startedTimeNs: int | None
    endedTimeNs: int
    durationNs: int
    exitCode: int | None
    stopReason: str | None
    stdout: CapturedStream
    stderr: CapturedStream
    cleanup: ProcessCleanup
    failureCategory: str | None


class ProcessOperationError(ProcessExecutionError):
    """Operational failure with opaque private evidence safe under generic tracing."""

    def __init__(self, category, evidence=None):
        """Attach only safe category and an opaque immutable evidence object."""
        super().__init__("Configured process operation failed.")
        self.category, self.evidence = category, evidence


class ProcessCancelled(ExecutionCancelled):
    """Preserve actual Job cancellation while exposing private evidence explicitly."""

    def __init__(self, evidence=None):
        """Use fixed public text; no callback/OS exception chain is retained."""
        super().__init__("Configured process execution cancelled.")
        self.category, self.evidence = "cancelled", evidence


class ProcessCleanupPendingError(ProcessExecutionError):
    """Sticky execution quarantine does not grant recovery or cleanup authority."""

    def __init__(self):
        """Expose neither private resource identifiers nor pending callback state."""
        super().__init__("Configured process execution is quarantined.")


class ProcessInvocation:
    """Independent lifetime token; workers never retain a live Context or Job."""

    def __init__(self, identity=None, signal=None, workspace=None):
        """Freeze runtime identity and bind the exact cancellation signal/workspace."""
        self.valid = Event()
        self.valid.set()
        self.identity, self.signal, self.workspace = identity, signal, workspace
        self.invocationId = uuid4().hex
        binding = _job.get()
        self.jobId = binding[0] if binding is not None and binding[1] is signal else None


class PreparedProcess:
    """Opaque one-use request bound to the exact issuing invocation facade."""

    __slots__ = ("_request", "_environment", "_state", "_expires", "_used", "_lane")

    def __init__(self, request, environment, state):
        """Keep a frozen private environment until admission consumes this ticket."""
        self._request, self._environment, self._state = request, environment, state
        self._expires, self._used, self._lane = time.monotonic() + _TICKET_SECONDS, False, RLock()

    @property
    def request(self):
        """Inspect immutable intent without issuing a second execution token."""
        return self._request

    def __repr__(self):
        """Hide private argv and environment from incidental representation."""
        return "<PreparedProcess>"

    def __copy__(self):
        """Reject duplicate authority objects even before one-use validation."""
        raise TypeError("Prepared process cannot be copied.")

    def __deepcopy__(self, memo):
        """Reject deep copying of invocation-bound execution authority."""
        raise TypeError("Prepared process cannot be copied.")

    def __reduce__(self):
        """Prevent serialization of live admission authority."""
        raise TypeError("Prepared process cannot be serialized.")


def prepare(runner, state, toolName, arguments=(), *, workingDirectory,
            timeoutSeconds=30.0, stdoutBytes=262144, stderrBytes=262144, cleanupSeconds=2.0):
    """Freeze bounded caller controls/resolution; create no worker or child."""
    runner.requireReady()
    with runner._processLane:
        if runner._active is not None:
            raise RuntimeError("Configured process execution is busy.")
    if not state.valid.is_set():
        raise RuntimeError("Process invocation is inactive.")
    if state.signal is not None and state.signal.requested:
        raise ProcessCancelled()
    for number, maximum in ((timeoutSeconds, 120), (cleanupSeconds, 5)):
        if type(number) not in (int, float) or not math.isfinite(number) or not 0 < number <= maximum:
            raise ValueError("Invalid process deadline.")
    if any(type(n) is not int or not 1 <= n <= 1048576 for n in (stdoutBytes, stderrBytes)):
        raise ValueError("Invalid process stream bound.")
    if isinstance(arguments, (str, bytes)):
        raise TypeError("Process arguments require a string sequence.")
    args = []
    try:
        for item in arguments:
            if len(args) >= 64 or type(item) is not str or "\x00" in item:
                raise ValueError("Invalid process arguments.")
            args.append(item)
        if sum(len(item.encode("utf-8")) for item in args) > 8192:
            raise ValueError("Invalid process arguments.")
    except UnicodeError:
        raise ValueError("Invalid process arguments.") from None
    failed = False
    try:
        definition = runner._registry.require(toolName)
        cwd = Path(workingDirectory).resolve(strict=True)
        executable = definition.executable.resolve(strict=True)
        if not cwd.is_dir() or not executable.is_file():
            failed = True
        if (type(toolName) is not str or "\x00" in toolName
                or sum(len(x.encode("utf-8")) for x in (toolName, str(cwd), str(executable))) > 32768):
            failed = True
    except (OSError, ValueError, TypeError, ProcessExecutionError, UnicodeError):
        failed = True
    if failed:
        raise ProcessOperationError("resolution")
    environment = os.environ.copy()
    if definition.environment is not None:
        environment.update(definition.environment)
    request = ProcessRequest(uuid4().hex, getattr(state.identity, "applicationRunId", None),
        getattr(state.identity, "codeEntryInstanceId", None), state.invocationId, state.jobId,
        toolName, str(definition.executable), str(executable), tuple(args), str(cwd),
        float(timeoutSeconds), stdoutBytes, stderrBytes, float(cleanupSeconds),
        "inherited-host-plus-configured-overrides-frozen", time.time_ns())
    return PreparedProcess(request, environment, state)


class _Operation:
    """Root exact ACK/spawn/reader ownership until all worker frames have ended."""

    def __init__(self, runner, prepared, callback):
        """Keep no Context/Job; callback closure may retain its bounded archive port."""
        self.runner, self.prepared, self.callback = runner, prepared, callback
        self.request, self.state = prepared.request, prepared._state
        self.lane, self.stop, self.done = RLock(), Event(), Event()
        self.ack = False
        self.phase, self.launch, self.reason, self.failure = "ack", "not-attempted", None, None
        self.started, self.child, self.exitCode, self.deadline = None, None, None, None
        self.spawnResolved = True
        self.readers, self.streams = [], [CapturedStream(b"", 0, False, False, False)] * 2
        self.stopAttempted, self.primary, self.abandoned = False, None, False
        self.start = time.monotonic_ns()
        self.pin = False
        self.worker = Thread(target=self.work, name="actant-process-supervisor", daemon=True, context=Context())

    def requestStop(self, reason):
        """Latch only state; actual signal callback never closes/waits on IO."""
        with self.lane:
            if not self.done.is_set() and self.reason is None:
                self.reason = reason
            self.stop.set()

    def _read(self, index, pipe, cap):
        """Read concurrent raw streams with at most cap+1 observed bytes each."""
        data, observed, eof, overflow, failed = bytearray(), 0, False, False, False
        try:
            while True:
                chunk = pipe.read(min(8192, cap - observed + 1))
                if not chunk:
                    eof = True
                    break
                observed += len(chunk)
                data.extend(chunk[:max(0, cap - len(data))])
                if observed > cap:
                    overflow = True
                    self.requestStop("stdout-limit" if index == 0 else "stderr-limit")
                    break
                with self.lane:
                    self.streams[index] = CapturedStream(bytes(data), observed, False, False, False)
        except BaseException as error:
            failed = True
            if not isinstance(error, Exception):
                with self.lane:
                    self.primary = self.primary or error
            self.requestStop("execution-failed")
        finally:
            try:
                pipe.close()
            except BaseException as error:
                failed = True
                if not isinstance(error, Exception):
                    with self.lane:
                        self.primary = self.primary or error
                self.requestStop("execution-failed")
            with self.lane:
                self.streams[index] = CapturedStream(bytes(data), observed, eof, overflow, failed)

    def work(self):
        """Own callback then spawn and child disposition; blocked ACK cannot clean itself."""
        try:
            if self.stop.is_set() or not self.state.valid.is_set():
                self.failure = "interrupted"
                return
            accepted = self.callback(self.request)
            self.callback = None
            with self.lane:
                self.ack = True
                if accepted is not True:
                    self.failure = "ack-rejected"
                    return
                if (self.stop.is_set() or self.abandoned or not self.state.valid.is_set()
                        or time.monotonic() >= self.prepared._expires):
                    self.failure = self.failure or "expired"
                    return
                self.phase, self.launch = "spawn", "attempted"
                self.spawnResolved = False
                self.deadline = time.monotonic() + self.request.timeoutSeconds
            self.child = subprocess.Popen([self.request.resolvedExecutable, *self.request.arguments],
                cwd=self.request.workingDirectory, env=self.prepared._environment, shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            with self.lane:
                self.launch, self.started, self.phase = "started", time.time_ns(), "capture"
                self.spawnResolved = True
            for index, pipe in enumerate((self.child.stdout, self.child.stderr)):
                cap = self.request.stdoutBytes if index == 0 else self.request.stderrBytes
                worker = Thread(target=self._read, args=(index, pipe, cap),
                                name="actant-process-reader", daemon=True, context=Context())
                self.readers.append(worker)
                worker.start()
            stopTime = None
            while True:
                code = self.child.poll()
                if code is not None:
                    self.exitCode = self.child.wait()
                    if all(not reader.is_alive() for reader in self.readers):
                        break
                if time.monotonic() >= self.deadline:
                    self.requestStop("timeout")
                if self.stop.is_set() and code is None:
                    self.stopAttempted = True
                    if stopTime is None:
                        stopTime = time.monotonic()
                        self.child.terminate()
                    elif time.monotonic() - stopTime >= min(.25, self.request.cleanupSeconds / 2):
                        self.child.kill()
                time.sleep(.005)
            for worker in self.readers:
                worker.join()
        except BaseException as error:
            with self.lane:
                if self.phase == "spawn" and isinstance(error, OSError):
                    self.spawnResolved = True
                if not isinstance(error, Exception):
                    self.primary = self.primary or error
                    self.reason = self.reason or "interrupted"
                self.failure = self.failure or ("ack-failed" if not self.ack else
                    "spawn-failed" if self.launch != "started" else "capture-failed")
        finally:
            self.callback = None
            self.ack = True
            self.done.set()

    def settled(self):
        """Confirm every actual owned worker/pipe/child obligation, never descendants."""
        workerDone = not self.worker.is_alive()
        readersDone = all(not worker.is_alive() for worker in self.readers)
        childDone = (self.child is None and self.spawnResolved) or self.exitCode is not None
        pipesDone = self.child is None or all(pipe.closed for pipe in (self.child.stdout, self.child.stderr))
        return workerDone and readersDone and childDone and pipesDone and self.ack and self.spawnResolved

    def release(self):
        """Drop pending roots and pin only after ACK and all owned workers settle."""
        if self.pin:
            self.state.workspace._unpin()
            self.pin = False
        self.callback = None
        self.prepared._environment = None
        with _pendingLane:
            _pending.pop(self.request.requestId, None)
        with self.runner._processLane:
            if self.runner._active is self:
                self.runner._active = None

    def reap(self):
        """Retain unknown ownership; late completion never changes returned evidence."""
        self.worker.join()
        # If the supervisor itself failed after owning a child, retain exact handles
        # and attempt disposition here; no client callback is called again.
        while not self.settled():
            if self.child is not None and self.child.poll() is None:
                try:
                    self.child.kill()
                except OSError:
                    pass
            if self.child is not None:
                if self.child.poll() is not None:
                    self.exitCode = self.child.wait()
                    self.spawnResolved = True
                for pipe in (self.child.stdout, self.child.stderr):
                    if all(not r.is_alive() for r in self.readers) and not pipe.closed:
                        try:
                            pipe.close()
                        except OSError:
                            pass
            time.sleep(.02)
        self.release()

    def outcome(self, confirmed):
        """Freeze a consistency-checked disposition without exposing mutable owners."""
        with self.lane:
            category, phase, reason = self.failure, self.phase, self.reason
            if self.launch == "started" and any(s.readFailure or not s.eof or s.limitExceeded for s in self.streams):
                category = category or "capture-failed"
            status = "completed"
            if reason == "cancelled":
                status = "cancelled"
            elif reason == "timeout":
                status = "timeout"
            elif reason in ("stdout-limit", "stderr-limit"):
                status = "output-limit"
            elif category is not None or reason is not None:
                status = "failed"
            if not confirmed:
                category, phase = "cleanup-unknown", "cleanup"
                if status == "completed":
                    status = "failed"
            elif status == "completed":
                if self.launch != "started" or self.exitCode is None:
                    status, category = "failed", category or "interrupted"
                else:
                    phase = "completed"
            childDone = (self.child is None and self.spawnResolved) or self.exitCode is not None
            readersDone = all(not worker.is_alive() for worker in self.readers)
            pipesDone = self.child is None or all(p.closed for p in (self.child.stdout, self.child.stderr))
            cleanup = ProcessCleanup("owned-child-ack-spawn-supervisor-and-capture-workers",
                childDone, readersDone, pipesDone, self.spawnResolved, self.ack,
                confirmed, self.stopAttempted,
                "confirmed" if confirmed else "unknown", None if confirmed else self.request.requestId,
                "released-pin" if confirmed and self.pin else "retained" if self.pin else "unaffected")
            return ProcessOutcome(self.request, status, phase, self.launch, self.started,
                time.time_ns(), time.monotonic_ns() - self.start, self.exitCode, reason,
                self.streams[0], self.streams[1], cleanup, category)


def execute(runner, state, prepared, beforeLaunch):
    """Consume one ACK-gated ticket and return/raise after bounded ownership wait."""
    runner.requireReady()
    if type(prepared) is not PreparedProcess or prepared._state is not state or not callable(beforeLaunch):
        raise TypeError("Invalid prepared process admission.")
    with prepared._lane:
        if prepared._used:
            raise RuntimeError("Prepared process already consumed.")
        prepared._used = True
    if not state.valid.is_set():
        raise RuntimeError("Process invocation is inactive.")
    operation = _Operation(runner, prepared, beforeLaunch)
    beforeLaunch = None
    if time.monotonic() >= prepared._expires or (state.signal is not None and state.signal.requested):
        operation.phase, operation.ack = "admission", True
        operation.callback = None
        prepared._environment = None
        if state.signal is not None and state.signal.requested:
            operation.reason = "cancelled"
            raise ProcessCancelled(operation.outcome(True))
        operation.failure = "expired"
        raise ProcessOperationError("expired", operation.outcome(True))
    with runner._processLane:
        runner.requireReady()
        if runner._active is not None:
            raise RuntimeError("Configured process execution is busy.")
        runner._active = operation
    if state.workspace is not None:
        try:
            state.workspace._pin()
            operation.pin = True
        except BaseException:
            with runner._processLane:
                runner._active = None
            raise
    with _pendingLane:
        _pending[operation.request.requestId] = operation
    unregister = (state.signal.registerCallback(lambda: operation.requestStop("cancelled"))
                  if state.signal is not None else lambda: None)
    primary, stopDeadline, started = None, None, False
    try:
        try:
            operation.worker.start()
            started = True
        except BaseException as error:
            started = operation.worker.is_alive()
            operation.ack = not started
            operation.failure, operation.phase = "capture-failed", "admission"
            if not isinstance(error, Exception):
                primary = error
            if started:
                operation.requestStop("interrupted")
        while started:
            try:
                if operation.done.wait(.005):
                    operation.worker.join(operation.request.cleanupSeconds)
                    break
                if not state.valid.is_set():
                    operation.requestStop("execution-failed")
                deadline = operation.deadline or prepared._expires
                if time.monotonic() >= deadline:
                    operation.requestStop("timeout")
                if operation.stop.is_set():
                    stopDeadline = stopDeadline or time.monotonic() + operation.request.cleanupSeconds
                    if time.monotonic() >= stopDeadline:
                        break
            except BaseException as error:
                if not isinstance(error, Exception):
                    primary = primary or error
                    operation.requestStop("interrupted")
                else:
                    operation.failure = operation.failure or "capture-failed"
                    operation.requestStop("execution-failed")
                stopDeadline = stopDeadline or time.monotonic() + operation.request.cleanupSeconds
                if time.monotonic() >= stopDeadline:
                    break
    finally:
        try:
            unregister()
        except BaseException as error:
            operation.failure = operation.failure or "capture-failed"
            if not isinstance(error, Exception):
                primary = primary or error
    confirmed = operation.settled()
    if not confirmed:
        with operation.lane:
            operation.abandoned = True
            operation.stop.set()
        runner._quarantined.set()
    value = operation.outcome(confirmed)
    primary = primary or operation.primary
    if confirmed:
        operation.release()
    else:
        try:
            Thread(target=operation.reap, name="actant-process-retained-owner", daemon=True,
                   context=Context()).start()
        except BaseException as error:
            # Exact roots/pin remain retained if the OS cannot schedule cleanup.
            if not isinstance(error, Exception):
                primary = primary or error
    if primary is not None:
        try:
            primary.processEvidence = value
        except BaseException:
            pass
        raise primary
    if value.status == "cancelled":
        raise ProcessCancelled(value)
    if value.status != "completed":
        raise ProcessOperationError(value.failureCategory or value.status, value)
    return value
