# file: first-party/llmDrivers/llamaCpp/requestEvidence.py ; version: 1
"""Host-only private inference evidence; scopes locate, never grant execution.

Sinks synchronously return True after their own durable acknowledgement. No sink
is called under broker locks. Final sink failures are observed on scope exit,
after actual provider cleanup, without replacing an earlier execution exception.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
from threading import RLock, get_ident
from uuid import uuid4

from backend.llm.errors import LlmProviderProtocolError


class RequestEvidenceError(LlmProviderProtocolError):
    """Fixed content-free capture failure, distinct from provider cleanup facts."""


@dataclass(frozen=True, slots=True)
class RequestEvidence:
    """Private immutable receipt; no owner, headers, paths or runtime handles."""

    callId: str
    correlation: tuple[str, ...]
    phase: str
    body: bytes | None = field(default=None, repr=False)
    length: int | None = None
    sha256: str | None = None
    mayHaveSent: bool | None = None
    responseHeadersObserved: bool | None = None
    responseBodyObserved: bool | None = None
    contentType: str = "application/json"
    encoding: str = "utf-8"
    serverConsumption: str = "unknown"
    schemaId: str = "actant.llamaCpp.requestEvidence@1"


class _Scope:
    """Atomic single-use ticket; copied contexts share this same claim state."""

    def __init__(self, owner, correlation, sink):
        """Keep transient host identity and sink until scope and cleanup finish."""
        self.owner, self.correlation, self.sink = owner, correlation, sink
        self.thread = get_ident()
        self.live, self.claimed, self.finished = True, False, False
        self.call = None
        self.callId = uuid4().hex
        self.failure = None
        self.lane = RLock()

    @property
    def failed(self):
        """Expose only a content-free sticky failure bit to host diagnostics."""
        with self.lane:
            return self.failure is not None

    def requireLive(self):
        """Reject unsupported thread crossings and closed scope use."""
        with self.lane:
            if not self.live or self.thread != get_ident():
                raise RequestEvidenceError("Private request evidence scope is unavailable.")

    def emit(self, phase, **facts):
        """Call a required sink outside locks; discard ordinary error chains."""
        event = RequestEvidence(self.callId, self.correlation, phase, **facts)
        failure = None
        try:
            if self.sink(event) is not True:
                failure = RequestEvidenceError("Private request evidence was not acknowledged.")
        except Exception:
            failure = RequestEvidenceError("Private request evidence sink failed.")
        except BaseException as error:
            failure = error
        if failure is not None:
            with self.lane:
                if self.failure is None:
                    self.failure = failure
            raise failure

    def serialized(self, body, limit):
        """Capture the exact immutable bytes which the provider passes to open."""
        self.requireLive()
        if type(body) is not bytes or len(body) > limit:
            raise RequestEvidenceError("Private request evidence body exceeds its bound.")
        self.emit("serialized", body=body, length=len(body), sha256=sha256(body).hexdigest())
        self.requireLive()

    def attempt(self):
        """Acknowledge intention only; this is not a send or server receipt."""
        self.requireLive()
        self.emit("transport-attempt")
        self.requireLive()

    def finish(self, *, mayHaveSent, headers, body):
        """Exactly-once final diagnostics survive closure without new authority."""
        with self.lane:
            if self.finished:
                return
            self.finished = True
        try:
            self.emit("final", mayHaveSent=mayHaveSent,
                responseHeadersObserved=headers, responseBodyObserved=body)
        except BaseException:
            pass  # Sticky failure is surfaced by scope exit, after actual cleanup.
        finally:
            with self.lane:
                self.call = self.owner = self.sink = None


class PrivateRequestEvidence:
    """Instance-local locator with exact actual-owner and admitted-ticket claims."""

    def __init__(self):
        """Create an inert broker; composition separately binds required policy."""
        self._current = ContextVar("llama.cpp.private-request-evidence", default=None)

    @contextmanager
    def scope(self, owner, *, correlation, sink):
        """Open one host scope; reject nesting, mutable correlation and bad sinks.

        The host must observe scope exit. A final sink failure raises here only if
        the wrapped operation had no primary exception; actual cleanup is unchanged.
        """
        if self._current.get() is not None:
            raise RequestEvidenceError("Nested private request evidence scopes are unsupported.")
        if (owner is None or not callable(sink) or type(correlation) is not tuple or
                not 1 <= len(correlation) <= 4 or any(type(s) is not str or not s or
                len(s) > 128 or not all(c.isascii() and (c.isalnum() or c in "-_.") for c in s)
                for s in correlation)):
            raise RequestEvidenceError("Invalid private request evidence scope.")
        scope = _Scope(owner, correlation, sink)
        token = self._current.set(scope)
        primary = None
        try:
            yield scope
        except BaseException as error:
            primary = error
            raise
        finally:
            self._current.reset(token)
            with scope.lane:
                scope.live = False
                failure = scope.failure
                if not scope.claimed:
                    scope.owner = scope.sink = None
                elif not scope.finished and failure is None:
                    failure = RequestEvidenceError("Private request evidence finalization is unavailable.")
            if primary is None and failure is not None:
                raise failure

    def claim(self, owner, admission):
        """Atomically consume exactly one scope for the actual trusted caller."""
        scope = self._current.get()
        if scope is None:
            raise RequestEvidenceError("Required private request evidence scope is missing.")
        with scope.lane:
            scope.requireLive()
            if scope.owner is not owner or scope.claimed:
                raise RequestEvidenceError("Private request evidence scope cannot be claimed.")
            scope.claimed, scope.call = True, admission
        return scope
