# file: backend/orchestration/cancellation.py ; version: 1
from __future__ import annotations

from threading import Event, RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["CancellationSignal", "ExecutionCancelled"]


class ExecutionCancelled(RuntimeError):
    """Signals intentional execution cancellation without classifying it as failure."""


class CancellationSignal:
    """
    Thread-safe cooperative cancellation and execution-stop signal.

    The signal carries no Job lifecycle state. Job owns the semantic cancellation
    outcome; this object only lets execution layers observe a request and register
    no-fail stop callbacks for active execution mechanisms.
    """

    def __init__(self) -> None:
        """Creates one initially unrequested cancellation signal."""
        self._event = Event()
        self._lane = RLock()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._nextCallbackId = 1

    @property
    def requested(self) -> bool:
        """Reports whether cancellation has been requested."""
        return self._event.is_set()

    def request(self) -> bool:
        """
        Requests cancellation once and invokes registered execution-stop callbacks.

        Returns True only for the first request. Registered callbacks are required
        to be idempotent and must not raise; execution adapters should wrap fallible
        stop operations before registration.
        """
        with self._lane:
            if self._event.is_set():
                return False
            self._event.set()
            callbacks = tuple(self._callbacks.values())

        for callback in callbacks:
            callback()
        return True

    def registerCallback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """
        Registers one execution-stop callback and returns an unregister function.

        If cancellation was already requested, callback is invoked immediately and
        the returned unregister function is a no-op.
        """
        if not callable(callback):
            raise TypeError("callback must be callable.")

        with self._lane:
            if self._event.is_set():
                invokeImmediately = True
                callbackId = 0
            else:
                invokeImmediately = False
                callbackId = self._nextCallbackId
                self._nextCallbackId += 1
                self._callbacks[callbackId] = callback

        if invokeImmediately:
            callback()

        def unregister() -> None:
            """Withdraws this callback when it is still registered."""
            if callbackId == 0:
                return
            with self._lane:
                self._callbacks.pop(callbackId, None)

        return unregister

    def raiseIfRequested(self) -> None:
        """Raises ExecutionCancelled when cancellation has been requested."""
        if self.requested:
            raise ExecutionCancelled("Execution cancellation was requested.")
